# AWS Lambda quotas and behavior

Source: https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html
(facts below are paraphrased from AWS's own Lambda quotas documentation)

## Function timeout

Function timeout is 900 seconds (15 minutes) for standard invocations. The
default timeout when you create a function is 3 seconds. If a function runs
longer than its configured timeout, the runtime is stopped and the invocation
fails with a timeout error.

## Memory and CPU

Function memory allocation ranges from 128 MB to 10,240 MB, in 1 MB
increments. Lambda allocates CPU power in proportion to the amount of memory
configured: at 1,769 MB, a function has the equivalent of one full vCPU.
Increasing memory therefore also speeds up CPU-bound work.

## Deployment package size

A deployment package (.zip file archive) can be up to 50 MB when uploaded
directly through the Lambda API, console, or SDKs; larger packages must go
through Amazon S3. The maximum size of the contents of a deployment package,
including layers and custom runtimes, is 250 MB unzipped. Container image
code packages can be up to 10 GB (maximum uncompressed image size, including
all layers). The /tmp directory provides between 512 MB and 10,240 MB of
ephemeral storage, in 1 MB increments.

## Concurrency

The default concurrent-executions quota is 1,000 per account per Region.
Reserved concurrency carves out a guaranteed slice of that quota for one
function; provisioned concurrency keeps execution environments initialized in
advance to avoid cold starts. When concurrency is exceeded, synchronous
callers receive a 429 TooManyRequestsException, while asynchronous
invocations are retried automatically instead of being rejected immediately.

## Retry behavior

For asynchronous invocation, Lambda retries a failed execution automatically,
with delays between attempts. Events that still fail after retries are sent
to a configured dead-letter queue or on-failure destination if one exists;
otherwise they are discarded. For stream-based event sources such as Kinesis
and DynamoDB Streams, Lambda retries until the record expires or succeeds,
unless bisect-on-error or a maximum retry age is configured.
