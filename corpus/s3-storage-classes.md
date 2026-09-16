# S3 storage classes (sample corpus document)

## Durability and availability

S3 Standard is designed for 99.999999999% (11 nines) durability of objects
across multiple Availability Zones, and 99.99% availability over a given
year.

## Storage classes

S3 Standard is for frequently accessed data with no retrieval fee. S3
Standard-Infrequent Access (Standard-IA) and One Zone-IA cost less per GB
stored but charge a per-GB retrieval fee, and are meant for data accessed
less than once a month. S3 Glacier Instant Retrieval, Glacier Flexible
Retrieval, and Glacier Deep Archive are for archival data, with retrieval
times ranging from milliseconds to many hours and progressively lower
storage cost.

## Versioning

When versioning is enabled on a bucket, every write creates a new version of
an object instead of overwriting it, and a delete adds a delete marker rather
than removing prior versions. This protects against accidental overwrite or
deletion but means storage cost includes every retained version.

## Lifecycle rules

Lifecycle rules can automatically transition objects between storage classes
or expire them after a set number of days, based on prefix or tag filters.
