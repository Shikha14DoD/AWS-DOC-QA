"""Upload a local text/markdown document to the documents bucket.

Usage:
    python scripts/upload_document.py path/to/doc.md

Resolves the bucket name from the deployed CloudFormation stack, uploads the
file, and prints the key. The S3 event notification then triggers the ingest
Lambda automatically.
"""

import sys
from pathlib import Path

import boto3

STACK_NAME = "InfrastructureStack"
REGION = "us-east-2"


def bucket_name() -> str:
    # Read the stack output by name rather than "the first S3 bucket in the
    # stack" - there are two buckets now (documents + the demo site), and
    # list_stack_resources doesn't guarantee an order that would pick the
    # right one.
    cfn = boto3.client("cloudformation", region_name=REGION)
    outputs = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]["Outputs"]
    for o in outputs:
        if o["OutputKey"] == "DocumentsBucketName":
            return o["OutputValue"]
    raise SystemExit("DocumentsBucketName output not found - is the stack deployed?")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    path = Path(sys.argv[1])
    if not path.is_file():
        raise SystemExit(f"Not a file: {path}")

    bucket = bucket_name()
    key = path.name
    boto3.client("s3", region_name=REGION).upload_file(str(path), bucket, key)
    print(f"uploaded s3://{bucket}/{key}")


if __name__ == "__main__":
    main()
