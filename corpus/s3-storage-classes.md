# S3 storage classes

Source: https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-class-intro.html,
https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html,
https://docs.aws.amazon.com/AmazonS3/latest/userguide/intro-lifecycle-rules.html
(facts below are paraphrased from AWS's own S3 user guide)

## Durability and availability

S3 Standard is designed for 99.999999999% (11 nines) durability of objects,
and 99.99% availability over a given year. Nearly all S3 storage classes
share that same 11-nines durability figure; what differs between classes is
mainly availability, retrieval cost, and minimum storage duration.

## Storage classes

S3 Standard is the default class, for frequently accessed data with
millisecond access and no retrieval fee. S3 Standard-IA (Infrequent Access)
and S3 One Zone-IA cost less per GB stored but charge a per-GB retrieval fee,
and are meant for data accessed roughly once a month or less; One Zone-IA
stores data in only one Availability Zone (cheaper, but not resilient to
losing that zone), while Standard-IA replicates across multiple zones. S3
Glacier Instant Retrieval, Glacier Flexible Retrieval, and Glacier Deep
Archive are for long-term archival data accessed a few times a year or less,
with retrieval times ranging from milliseconds (Instant Retrieval) to hours
(Deep Archive), and progressively lower storage cost the longer the expected
retrieval time.

## Versioning

When versioning is enabled on a bucket, every write creates a new object
version instead of overwriting the previous one, and deleting an object
inserts a delete marker - which becomes the new "current" version - rather
than removing the object outright. The prior version can still be restored.
Versioning cannot be fully disabled once enabled, only suspended. Every
retained version is billed as a full object, so storage cost scales with the
number of versions kept, not just the number of distinct objects.

## Lifecycle rules

An S3 Lifecycle configuration is a set of rules (up to 1,000 per bucket) that
tell S3 to transition objects to a different storage class or expire
(delete) them after a specified time or date. Each rule can be scoped with a
filter based on object key prefix, one or more object tags, object size, or
a combination of these - or left unfiltered to apply to every object in the
bucket.
