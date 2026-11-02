"""Turn an S3 key into a document_id - the one place this rule lives.

Both the ingest Lambda and the reconcile Lambda need to agree on exactly how
an S3 key maps to a document_id (ingest, to write chunks under it; reconcile,
to compare what's in S3 against what's in DynamoDB). Two copies of this
function would be the same drift risk already documented elsewhere in this
project (duplicated Gemini/Groq clients, before they were moved into this
layer) - one definition instead.
"""

import re


def document_id_from_key(key: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", key)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_") or "document"
