from pathlib import Path

from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    RemovalPolicy,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_sqs as sqs,
    aws_ssm as ssm,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as apigw_int,
    aws_cloudwatch as cloudwatch,
)
from constructs import Construct

# Repo root: infrastructure/infrastructure/infrastructure_stack.py -> ../../
REPO_ROOT = Path(__file__).resolve().parents[2]
LAMBDAS = REPO_ROOT / "lambdas"

# SSM parameters holding API keys (SecureString). Created out of band with
# `aws ssm put-parameter` so keys never touch git or the CloudFormation
# template. Path can't start with "aws" or "ssm" - those prefixes are
# reserved by SSM and PutParameter rejects them (learned the hard way).
GEMINI_API_KEY_PARAM = "/docqa/gemini-api-key"
GROQ_API_KEY_PARAM = "/docqa/groq-api-key"


class InfrastructureStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Storage -------------------------------------------------------

        # S3 bucket to hold uploaded documents. Uploading here triggers the
        # ingest Lambda below.
        self.documents_bucket = s3.Bucket(
            self, "DocumentsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ChunksTable: one item per chunk of a document.
        #   partition key  document_id  - groups every chunk of one document
        #   sort key       chunk_id     - orders chunks within a document
        # Each item also stores: text, embedding (JSON string of floats), and
        # source metadata (source_key, char offsets) for citations.
        #
        # PAY_PER_REQUEST (on-demand): no capacity to provision, no fixed
        # hourly cost, scales to zero.
        #
        # Retrieval reads every chunk (a Scan) and ranks by cosine similarity
        # in the query Lambda - DynamoDB has no vector index. Fine for a small
        # docs corpus; past ~1M chunks this moves to OpenSearch or pgvector.
        self.chunks_table = dynamodb.Table(
            self, "ChunksTable",
            partition_key=dynamodb.Attribute(
                name="document_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="chunk_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- Shared code + secret ---------------------------------------------

        # Gemini client + SSM key helper, shared by both Lambdas. A layer keeps
        # the code in one place and versions independently of the functions.
        common_layer = lambda_.LayerVersion(
            self, "CommonLayer",
            code=lambda_.Code.from_asset(str(LAMBDAS / "layers" / "common")),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Shared Gemini client and SSM key helper (doc_qa_common)",
        )

        # Reference (not create) the SecureString parameters holding the API keys.
        gemini_key_param = ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "GeminiApiKeyParam",
            parameter_name=GEMINI_API_KEY_PARAM,
        )
        groq_key_param = ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "GroqApiKeyParam",
            parameter_name=GROQ_API_KEY_PARAM,
        )

        common_env = {
            "CHUNKS_TABLE_NAME": self.chunks_table.table_name,
            "GEMINI_API_KEY_PARAM": GEMINI_API_KEY_PARAM,
            "EMBED_MODEL": "text-embedding-004",
        }

        # --- Ingest path ----------------------------------------------------

        # S3 invokes the ingest Lambda asynchronously. If it keeps failing
        # (bad file, gemini down past the retry budget, etc) async invokes
        # get retried twice by Lambda itself and then, with a DLQ configured,
        # land here instead of just vanishing.
        ingest_dlq = sqs.Queue(
            self, "IngestDLQ",
            retention_period=Duration.days(14),
        )

        # Plain-zip asset (no bundling): stdlib + boto3 + the shared layer.
        self.ingest_fn = lambda_.Function(
            self, "IngestFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="ingest.handler",
            code=lambda_.Code.from_asset(str(LAMBDAS / "ingest")),
            layers=[common_layer],
            timeout=Duration.minutes(5),
            memory_size=512,
            environment=common_env,
            dead_letter_queue=ingest_dlq,
            retry_attempts=2,
        )

        # Least-privilege: write-only to the table, read the handed object,
        # decrypt the one SSM parameter.
        self.chunks_table.grant_write_data(self.ingest_fn)
        self.documents_bucket.grant_read(self.ingest_fn)
        gemini_key_param.grant_read(self.ingest_fn)

        # S3 -> Lambda: fire on every object create in the bucket.
        self.documents_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(self.ingest_fn),
        )

        # --- Query path ---------------------------------------------------

        self.query_fn = lambda_.Function(
            self, "QueryFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="query.handler",
            code=lambda_.Code.from_asset(str(LAMBDAS / "query")),
            layers=[common_layer],
            timeout=Duration.seconds(30),
            memory_size=512,
            environment={
                **common_env,
                "CHAT_MODEL": "gemini-2.0-flash",
                "TOP_K": "5",
                "GROQ_API_KEY_PARAM": GROQ_API_KEY_PARAM,
                "GROQ_CHAT_MODEL": "llama-3.1-8b-instant",
            },
        )

        # Read-only on the table (retrieval scans it); decrypt both SSM keys
        # (gemini primary, groq fallback).
        self.chunks_table.grant_read_data(self.query_fn)
        gemini_key_param.grant_read(self.query_fn)
        groq_key_param.grant_read(self.query_fn)

        # HTTP API (API Gateway v2): cheaper and lower latency than REST API,
        # and enough for a single JSON POST route.
        http_api = apigw.HttpApi(
            self, "QueryApi",
            description="AWS Document Q&A - query endpoint",
        )
        http_api.add_routes(
            path="/query",
            methods=[apigw.HttpMethod.POST],
            integration=apigw_int.HttpLambdaIntegration("QueryIntegration", self.query_fn),
        )

        # --- Alarms ----------------------------------------------------

        # No SNS action wired up (that means picking an email/subscriber,
        # which is a call for whoever runs this, not something to bake into
        # the stack) - these alarms are visible in the CloudWatch console and
        # ready for an action to be attached later.
        cloudwatch.Alarm(
            self, "IngestErrorsAlarm",
            metric=self.ingest_fn.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            alarm_description="Ingest Lambda raised an error",
        )
        cloudwatch.Alarm(
            self, "QueryErrorsAlarm",
            metric=self.query_fn.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            alarm_description="Query Lambda raised an error",
        )
        cloudwatch.Alarm(
            self, "IngestDLQAlarm",
            metric=ingest_dlq.metric_approximate_number_of_messages_visible(
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            alarm_description="A document failed ingestion and landed in the DLQ",
        )

        # --- Outputs ----------------------------------------------------

        CfnOutput(self, "DocumentsBucketName", value=self.documents_bucket.bucket_name)
        CfnOutput(self, "ChunksTableName", value=self.chunks_table.table_name)
        CfnOutput(self, "QueryApiUrl", value=http_api.api_endpoint)
