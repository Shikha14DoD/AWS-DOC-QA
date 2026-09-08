#!/usr/bin/env python3
import aws_cdk as cdk

from infrastructure.infrastructure_stack import InfrastructureStack

# Pinned to a concrete account/region on purpose: S3 -> Lambda event
# notifications and CDK context lookups both need a resolved environment at
# synth time. This is a single-account portfolio project, so the portability
# of an environment-agnostic template buys us nothing here.
AWS_ACCOUNT = "761558630882"
AWS_REGION = "us-east-2"

app = cdk.App()
InfrastructureStack(
    app,
    "InfrastructureStack",
    env=cdk.Environment(account=AWS_ACCOUNT, region=AWS_REGION),
)

app.synth()
