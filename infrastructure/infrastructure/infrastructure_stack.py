from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_s3 as s3,
)
from constructs import Construct

class InfrastructureStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 bucket to hold uploaded documents.
        # Uploading here will later trigger our ingestion Lambda.
        documents_bucket = s3.Bucket(
            self, "DocumentsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )