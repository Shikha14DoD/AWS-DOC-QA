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

## Updating the policy

The policy has needed one addition since it was first scoped: a
`CdkStackRecovery` statement granting `cloudformation:DescribeStackEvents`
and `cloudformation:ContinueUpdateRollback` on this stack only. `cdk deploy`
normally never touches CloudFormation directly - it assumes `cfn-exec-role`
for that - but a stack stuck in `UPDATE_ROLLBACK_FAILED` (which happened once,
from an ApiGatewayV2 resource that failed to roll back cleanly) can't be
un-stuck through `cdk deploy` itself; it needs the human identity to call
`ContinueUpdateRollback` directly. That's the one legitimate exception to "the
deploying user only needs AssumeRole."

To push an update to the policy after editing the JSON file:

```bash
aws iam create-policy-version \
  --policy-arn arn:aws:iam::761558630882:policy/docqa-shikha-dev-least-privilege \
  --policy-document file://infrastructure/iam/shikha-dev-least-privilege.json \
  --set-as-default
```

IAM keeps up to 5 versions per policy; once you're near that, delete an old
non-default version first (`aws iam list-policy-versions` /
`delete-policy-version`).

Before changing a policy (or detaching a broader one), simulate the exact
version against the operations actually used with `iam:SimulateCustomPolicy`
- a broader attached policy hides every gap in the scoped one. That's how the
`lambda:ListFunctions` bug (it needs `Resource: "*"`) was found: it had been
silently useless since the policy was first written.

## GitHub Actions deploy role (CI/CD)

`.github/workflows/tests.yml` runs the tests on every push and PR. Its
`deploy` job runs `cdk deploy` on a push to `main` after the tests pass, using
**GitHub OIDC** - GitHub gets a short-lived token and exchanges it for AWS
credentials, so there are no stored AWS keys anywhere. The job is skipped until
the repo variable `AWS_DEPLOY_ROLE_ARN` exists, so the workflow stays green
until this is set up.

The role's permissions (`github-deploy-permissions.json`) are only
`sts:AssumeRole` on the five CDK bootstrap roles plus two read actions - the
same small footprint as the human deploy user, since `cdk deploy` does the
real work through those roles. Its trust policy
(`github-deploy-trust-policy.json`) only accepts tokens from this repo's
`main` branch, so a pull request (or a fork) can never deploy.

Creating the role needs IAM permissions that `shikha-dev` deliberately
doesn't have, so do it once from the console as the root user:

1. **IAM -> Identity providers -> Add provider**: type *OpenID Connect*,
   provider URL `https://token.actions.githubusercontent.com`, audience
   `sts.amazonaws.com`. (Skip if it already exists.)
2. **IAM -> Roles -> Create role**: *Web identity*, provider
   `token.actions.githubusercontent.com`, audience `sts.amazonaws.com`, GitHub
   organization `Shikha14DoD`, repository `AWS-DOC-QA`, branch `main`. Skip the
   managed-policy step, name it `docqa-github-deploy`.
3. Open the role -> **Add permissions -> Create inline policy -> JSON**, paste
   `github-deploy-permissions.json`, save. (Optionally compare the role's trust
   policy with `github-deploy-trust-policy.json` - the wizard should produce the
   same conditions.)
4. Copy the role's ARN. In GitHub: **repo -> Settings -> Secrets and variables
   -> Actions -> Variables -> New repository variable**, name
   `AWS_DEPLOY_ROLE_ARN`, value the ARN.

The next push to `main` will then deploy. The `deploy` job has not run against
a real role yet - the build steps were verified in a clean checkout with no AWS
credentials, but the OIDC exchange itself is untested until this is set up.
