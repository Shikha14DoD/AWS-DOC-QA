# DynamoDB basics (sample corpus document)

## Billing modes

DynamoDB has two billing modes. On-demand (pay-per-request) charges per read
and write request with no capacity to provision and no cost when the table is
idle. Provisioned capacity requires setting read and write capacity units
ahead of time and is billed hourly whether or not it is used, though it can be
cheaper under sustained, predictable traffic.

## Item size limit

A single DynamoDB item, including attribute names and values, cannot exceed
400 KB. There is no limit on the number of items in a table.

## Keys and indexes

Every table has a partition key, and optionally a sort key, which together
form the primary key. A Global Secondary Index (GSI) can use different
partition and sort keys than the base table and has its own provisioned or
on-demand capacity. A Local Secondary Index (LSI) shares the base table's
partition key but uses a different sort key, and must be created at table
creation time.

## Consistency

Reads are eventually consistent by default. Strongly consistent reads can be
requested explicitly but are not available on Global Secondary Indexes and
consume more read capacity.
