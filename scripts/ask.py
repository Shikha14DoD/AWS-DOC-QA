"""Ask the deployed query API a question.

Usage:
    python scripts/ask.py "What is the maximum Lambda timeout?"

Reads the API URL from the deployed CloudFormation stack outputs, POSTs the
question to /query, and prints the answer and its citations.
"""

import io
import json
import sys
import urllib.request

import boto3

# windows terminals default to cp1252, which can't encode stuff models like
# to use (em dashes, smart quotes, narrow no-break spaces). force utf-8.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

STACK_NAME = "InfrastructureStack"
REGION = "us-east-2"


def api_url() -> str:
    cfn = boto3.client("cloudformation", region_name=REGION)
    outputs = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]["Outputs"]
    for o in outputs:
        if o["OutputKey"] == "QueryApiUrl":
            return o["OutputValue"].rstrip("/")
    raise SystemExit("QueryApiUrl output not found - is the stack deployed?")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    question = " ".join(sys.argv[1:])

    req = urllib.request.Request(
        f"{api_url()}/query",
        data=json.dumps({"question": question}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=35) as resp:
        body = json.loads(resp.read())

    print(body.get("answer", body))
    if body.get("provider"):
        print(f"\n(answered by {body['provider']})")
    print()
    for c in body.get("citations", []):
        print(f"  [{c['marker']}] {c['source_key']} "
              f"chunk {c['chunk_id']} (score {c['score']})")


if __name__ == "__main__":
    main()
