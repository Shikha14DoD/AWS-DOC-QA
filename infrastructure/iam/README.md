# IAM scoping for `shikha-dev`

`shikha-dev` currently has `AdministratorAccess`, kept deliberately broad
while the project's full service footprint was still unknown (see
`PROJECT_HANDOFF.md`). Now that it's deployed and stable, `shikha-dev-least-
privilege.json` is what it actually needs.

## Why this is small

Modern CDK bootstrap (`cdk bootstrap`, run early in this project) already
created five dedicated IAM roles in this account
(`cdk-hnb659fds-deploy-role`, `-file-publishing-role`,
`-image-publishing-role`, `-lookup-role`, `-cfn-exec-role`). `cdk deploy`
assumes those roles to do the actual work (create Lambdas, write
CloudFormation, publish assets) - they carry the broad permissions, scoped to
this account, created and managed by AWS's own CDK tooling.

That means the *human* identity deploying doesn't need direct
`lambda:CreateFunction`, `dynamodb:CreateTable`, `iam:CreateRole`, etc. It
only needs `sts:AssumeRole` on those five roles. Everything else in this
policy is the small set of ad hoc CLI actions actually used day to day:
storing API keys in SSM, uploading a document to S3, scanning the chunks
table to check ingestion, and tailing Lambda logs to debug.

## Applying it

```bash
aws iam create-policy \
  --policy-name docqa-shikha-dev-least-privilege \
  --policy-document file://infrastructure/iam/shikha-dev-least-privilege.json

aws iam attach-user-policy \
  --user-name shikha-dev \
  --policy-arn arn:aws:iam::761558630882:policy/docqa-shikha-dev-least-privilege
```

This is additive - `AdministratorAccess` stays attached until you've tested
that the new policy alone is enough (redeploy, ingest a doc, run
`scripts/eval.py`). Only once that's confirmed:

```bash
aws iam detach-user-policy \
  --user-name shikha-dev \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
```

Detaching `AdministratorAccess` is the one genuinely risky step here - get it
wrong and console/CLI access could break in ways that are annoying to
recover from with only this one IAM user. Test first, and keep the
`AdministratorAccess` re-attach command handy:

```bash
aws iam attach-user-policy \
  --user-name shikha-dev \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
```
