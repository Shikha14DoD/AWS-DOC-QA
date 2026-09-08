# AWS Lambda quotas and behavior (sample corpus document)

## Function timeout

A Lambda function's maximum execution timeout is 900 seconds (15 minutes).
The default timeout when you create a function is 3 seconds. If a function
runs longer than its configured timeout, the runtime is stopped and the
invocation fails with a timeout error.

## Memory and CPU

Function memory can be configured from 128 MB to 10,240 MB (10 GB) in 1 MB
increments. CPU is allocated proportionally to memory: at 1,769 MB a function
gets the equivalent of one full vCPU. Increasing memory therefore also
speeds up CPU-bound work.

## Deployment package size

The zipped deployment package can be up to 50 MB when uploaded directly, or
250 MB unzipped including layers. Container images can be up to 10 GB. The
/tmp directory provides between 512 MB and 10,240 MB of ephemeral storage.

## Concurrency

The default account-level concurrent execution limit is 1,000 across all
functions in a Region. Reserved concurrency carves out a guaranteed slice for
one function; provisioned concurrency keeps environments initialized to avoid
cold starts. Exceeding the concurrency limit results in throttling, and
asynchronous invocations are retried while synchronous callers receive a
429 TooManyRequestsException.

## Retry behavior

For asynchronous invocation, Lambda retries a failed execution twice, with
delays between retries. Events that still fail are sent to a dead-letter
queue or an on-failure destination if one is configured, otherwise they are
discarded. For stream-based sources such as Kinesis and DynamoDB Streams,
Lambda retries until the record expires or succeeds unless you configure a
bisect-on-error or maximum retry age.
