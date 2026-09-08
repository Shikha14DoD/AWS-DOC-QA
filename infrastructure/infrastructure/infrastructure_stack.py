from pathlib import Path

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_ssm as ssm,
)
from constructs import Construct

# Repo root: infrastructure/infrastructure/infrastructure_stack.py -> ../../
REPO_ROOT = Path(__file__).resolve().parents[2]

# SSM parameter that holds the Gemini API key (SecureString). Created out of
# band with `aws ssm put-parameter` so the key never touches git or the
# CloudFormation template.
GEMINI_API_KEY_PARAM = "/aws-doc-qa/gemini-api-key"


class InfrastructureStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

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

        # Reference (not create) the SecureString parameter holding the API key.
        gemini_key_param = ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "GeminiApiKeyParam",
            parameter_name=GEMINI_API_KEY_PARAM,
        )

        # Ingest Lambda. Plain-zip asset (no bundling): the handler uses only
        # the standard library plus boto3, which the Lambda runtime provides.
        self.ingest_fn = lambda_.Function(
            self, "IngestFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(REPO_ROOT / "lambdas" / "ingest")),
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "CHUNKS_TABLE_NAME": self.chunks_table.table_name,
                "GEMINI_API_KEY_PARAM": GEMINI_API_KEY_PARAM,
                "EMBED_MODEL": "text-embedding-004",
            },
        )

        # Least-privilege grants: write-only to the table, read the one
        # object it was handed, decrypt the one SSM parameter.
        self.chunks_table.grant_write_data(self.ingest_fn)
        self.documents_bucket.grant_read(self.ingest_fn)
        gemini_key_param.grant_read(self.ingest_fn)

        # S3 -> Lambda: fire on every object create in the bucket.
        self.documents_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(self.ingest_fn),
        )
