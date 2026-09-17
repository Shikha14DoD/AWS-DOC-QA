# DynamoDB basics

Source: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.CoreComponents.html,
https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.ReadWriteCapacityMode.html,
https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.ReadConsistency.html,
https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.NamingRulesDataTypes.html
(facts below are paraphrased from AWS's own DynamoDB developer guide)

## Billing modes

DynamoDB offers on-demand mode and provisioned mode. On-demand mode is a
serverless, pay-per-request option: you don't specify expected throughput,
and you only pay for the read and write requests you actually make, with no
cost when a table is idle. Provisioned mode requires specifying the number
of reads and writes per second you need in advance; you're charged based on
the hourly capacity provisioned, not how much of it you actually use. On-demand
is the default and recommended option for most workloads; provisioned suits
steady, predictable workloads where you can reliably forecast capacity.

## Item size limit

The maximum DynamoDB item size is 400 KB, including attribute names and
values. There is no limit on the number of items a table can hold, and no
practical limit on a table's total size.

## Keys and indexes

Every table has a primary key, which is either a simple partition key or a
composite partition-key-plus-sort-key. A Global Secondary Index (GSI) has a
partition key and sort key that can be different from the base table's, and
its primary key values don't need to be unique. A Local Secondary Index (LSI)
has the same partition key as the base table but a different sort key, and
must be created at table creation time - it cannot be added later. A table
can have up to 20 GSIs (default quota) and up to 5 LSIs.

## Consistency

Reads default to eventually consistent, meaning a read immediately after a
write might not reflect that write yet, though it will if repeated shortly
after. Strongly consistent reads (via the ConsistentRead parameter on
GetItem, Query, or Scan) return the most up-to-date data, but are only
supported on tables and Local Secondary Indexes - strongly consistent reads
from a Global Secondary Index are not supported. Eventually consistent reads
cost half as much as strongly consistent reads.
