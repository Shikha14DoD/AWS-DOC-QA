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
    aws_s3_deployment as s3deploy,
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
            "EMBED_MODEL": "gemini-embedding-001",
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
                "CHAT_MODEL": "gemini-3.6-flash",
                "TOP_K": "5",
                "GROQ_API_KEY_PARAM": GROQ_API_KEY_PARAM,
                "GROQ_CHAT_MODEL": "openai/gpt-oss-20b",
            },
        )

        # Read-only on the table (retrieval scans it); decrypt both SSM keys
        # (gemini primary, groq fallback).
        self.chunks_table.grant_read_data(self.query_fn)
        gemini_key_param.grant_read(self.query_fn)
        groq_key_param.grant_read(self.query_fn)

        # HTTP API (API Gateway v2): cheaper and lower latency than REST API,
        # and enough for a single JSON POST route.
        #
        # CORS wide open (allow_origins=["*"]) on purpose: the endpoint is
        # already unauthenticated and public, so restricting the browser
        # origin adds no real security - anyone can already call it directly.
        # It just lets a browser-hosted demo page call it too.
        http_api = apigw.HttpApi(
            self, "QueryApi",
            description="AWS Document Q&A - query endpoint",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=["*"],
                allow_methods=[apigw.CorsHttpMethod.POST],
                allow_headers=["content-type"],
            ),
        )
        http_api.add_routes(
            path="/query",
            methods=[apigw.HttpMethod.POST],
            integration=apigw_int.HttpLambdaIntegration("QueryIntegration", self.query_fn),
        )

        # --- Upload path ----------------------------------------------------

        # The one public *write* surface in this project - everything else is
        # read-only or S3-event-triggered. Validates hard before it touches
        # anything: allowed extension, a size cap, and an LLM topic check
        # (this demo only accepts AWS-related content) - then writes to the
        # same documents bucket the ingest Lambda already watches, so
        # ingestion is unchanged.
        self.upload_fn = lambda_.Function(
            self, "UploadFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="upload.handler",
            code=lambda_.Code.from_asset(str(LAMBDAS / "upload")),
            layers=[common_layer],
            timeout=Duration.seconds(20),
            memory_size=256,
            environment={
                "DOCUMENTS_BUCKET_NAME": self.documents_bucket.bucket_name,
                "GEMINI_API_KEY_PARAM": GEMINI_API_KEY_PARAM,
                "CHAT_MODEL": "gemini-3.6-flash",
                "GROQ_API_KEY_PARAM": GROQ_API_KEY_PARAM,
                "GROQ_CHAT_MODEL": "openai/gpt-oss-20b",
                "MAX_UPLOAD_BYTES": "20000",
            },
        )
        self.documents_bucket.grant_write(self.upload_fn)
        gemini_key_param.grant_read(self.upload_fn)
        groq_key_param.grant_read(self.upload_fn)

        upload_routes = http_api.add_routes(
            path="/upload",
            methods=[apigw.HttpMethod.POST],
            integration=apigw_int.HttpLambdaIntegration("UploadIntegration", self.upload_fn),
        )

        # Per-route throttling via raw property overrides - the L2 HttpApi/
        # HttpStage constructs only expose a single stage-wide throttle, and
        # the typed L1 RouteSettingsProperty renders camelCase keys that this
        # resource's own CloudFormation handler rejects (it wants PascalCase
        # here even though every top-level ApiGatewayV2 property is
        # camelCase - a real AWS inconsistency, confirmed by deploying it and
        # reading the resulting error). add_property_override bypasses the
        # typed property entirely and writes the exact JSON keys wanted.
        # Uploads should be far rarer than questions: 1 req/s (burst 2) for
        # uploads, a looser 10 req/s (burst 20) default for everything else.
        default_stage = http_api.default_stage.node.default_child
        # The RouteSettings override above references "POST /upload" by name,
        # which CDK's automatic dependency graph doesn't see (it's a raw
        # string in an override, not an object reference) - without this,
        # CloudFormation can try to deploy the stage's route settings before
        # the route itself exists and fail with "Unable to find Route by key".
        for route in upload_routes:
            default_stage.node.add_dependency(route)
        default_stage.add_property_override("DefaultRouteSettings", {
            "ThrottlingRateLimit": 10,
            "ThrottlingBurstLimit": 20,
        })
        default_stage.add_property_override("RouteSettings", {
            "POST /upload": {
                "ThrottlingRateLimit": 1,
                "ThrottlingBurstLimit": 2,
            },
        })

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

        # --- Demo site -----------------------------------------------------

        # Static demo page (demo/index.html), served straight from S3 - the
        # project's own thesis is AWS-native, so the demo shouldn't live on
        # someone else's platform either. A plain S3 website endpoint (no
        # CloudFront) is enough for a static single-file page and keeps this
        # in the same free-tier-first pattern as everything else here.
        self.demo_site_bucket = s3.Bucket(
            self, "DemoSiteBucket",
            website_index_document="index.html",
            public_read_access=True,
            # BLOCK_ACLS alone still leaves the "block public policy" account
            # default on (see cdk.json's publicAccessBlockedByDefault flag),
            # which then refuses the bucket policy public_read_access needs -
            # a static site's whole point is a public bucket policy, so that
            # part has to be explicitly opted out of.
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=True,
                ignore_public_acls=True,
                block_public_policy=False,
                restrict_public_buckets=False,
            ),
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Deploys demo/index.html on every `cdk deploy`, so the live site
        # never drifts from what's committed.
        s3deploy.BucketDeployment(
            self, "DemoSiteDeployment",
            sources=[s3deploy.Source.asset(str(REPO_ROOT / "demo"))],
            destination_bucket=self.demo_site_bucket,
        )

        # --- Outputs ----------------------------------------------------

        CfnOutput(self, "DocumentsBucketName", value=self.documents_bucket.bucket_name)
        CfnOutput(self, "ChunksTableName", value=self.chunks_table.table_name)
        CfnOutput(self, "QueryApiUrl", value=http_api.api_endpoint)
        CfnOutput(self, "DemoSiteUrl", value=self.demo_site_bucket.bucket_website_url)
