from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class InfrastructureStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 bucket to hold uploaded documents.
        # Uploading here will later trigger our ingestion Lambda.
        self.documents_bucket = s3.Bucket(
            self, "DocumentsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ChunksTable: one row per chunk of a document.
        #   partition key  document_id  - groups every chunk of one document
        #   sort key       chunk_id     - orders chunks within a document
        # Each item also stores: text, embedding (list of floats), and source
        # metadata (title, heading, char offsets) for citations.
        #
        # PAY_PER_REQUEST (on-demand): no read/write capacity to provision, no
        # fixed hourly cost, scales to zero. The alternative (provisioned
        # capacity) is cheaper only under sustained predictable load, which a
        # portfolio project does not have.
        #
        # Retrieval reads every chunk (a Scan) and ranks by cosine similarity
        # in the query Lambda - DynamoDB has no vector index. That is fine for
        # a small docs corpus; past ~1M chunks this moves to OpenSearch or
        # pgvector.
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
