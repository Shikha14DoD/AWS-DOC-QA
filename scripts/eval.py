"""Retrieval + answer evaluation harness.

Runs a fixed question set (eval/questions.json) against the deployed query
API and reports:

  - retrieval hit rate: for "in_corpus" questions, did a chunk from the
    expected source document show up in the top-k citations at all?
  - answer accuracy: does the answer contain one of the expected keywords?
  - hallucination rate: for "out_of_scope" questions (nothing in the corpus
    answers them), did the model fabricate an answer instead of declining?

This measures the deployed system, not a mock - it's a real check of whether
the "answer only from context" prompt design (see query.py) actually holds up.

Usage:
    python scripts/eval.py
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import boto3

STACK_NAME = "InfrastructureStack"
REGION = "us-east-2"
QUESTIONS_PATH = Path(__file__).resolve().parents[1] / "eval" / "questions.json"
RESULTS_PATH = Path(__file__).resolve().parents[1] / "eval" / "results.md"

DECLINE_PATTERNS = [
    r"\bi don'?t know\b",
    r"\bnot (?:in|part of|found in|mentioned in) the (?:context|passages|provided)\b",
    r"\bno (?:information|mention|context)\b",
    r"\bcan'?t answer\b",
    r"\bcannot answer\b",
    r"\bdoes not (?:contain|mention|provide)\b",
    r"\bdoesn'?t (?:contain|mention|provide)\b",
    r"\bunable to answer\b",
]


def api_url() -> str:
    cfn = boto3.client("cloudformation", region_name=REGION)
    outputs = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]["Outputs"]
    for o in outputs:
        if o["OutputKey"] == "QueryApiUrl":
            return o["OutputValue"].rstrip("/")
    raise SystemExit("QueryApiUrl output not found - is the stack deployed?")


def ask(base_url: str, question: str) -> dict:
    req = urllib.request.Request(
        f"{base_url}/query",
        data=json.dumps({"question": question}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=35) as resp:
        body = json.loads(resp.read())
    body["_latency_ms"] = round((time.time() - started) * 1000)
    return body


def looks_like_decline(answer: str) -> bool:
    lowered = answer.lower()
    return any(re.search(p, lowered) for p in DECLINE_PATTERNS)


def run() -> None:
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    base_url = api_url()

    rows = []
    for q in questions:
        result = ask(base_url, q["question"])
        answer = result.get("answer", "")
        citations = result.get("citations", [])
        sources_hit = {c["document_id"] for c in citations}

        row = {
            "question": q["question"],
            "type": q["type"],
            "answer": answer,
            "provider": result.get("provider"),
            "latency_ms": result["_latency_ms"],
        }

        if q["type"] == "in_corpus":
            row["retrieval_hit"] = q["expected_source"] in sources_hit
            row["answer_correct"] = any(
                kw.lower() in answer.lower() for kw in q["expected_keywords"]
            )
        else:  # out_of_scope
            row["hallucinated"] = not looks_like_decline(answer)

        rows.append(row)
        print(f"[{q['type']:11}] {q['question'][:60]:60} -> "
              f"{'OK' if row.get('answer_correct', not row.get('hallucinated')) else 'MISS'}")

    in_corpus = [r for r in rows if r["type"] == "in_corpus"]
    out_of_scope = [r for r in rows if r["type"] == "out_of_scope"]

    retrieval_hit_rate = sum(r["retrieval_hit"] for r in in_corpus) / len(in_corpus)
    answer_accuracy = sum(r["answer_correct"] for r in in_corpus) / len(in_corpus)
    hallucination_rate = sum(r["hallucinated"] for r in out_of_scope) / len(out_of_scope)
    avg_latency = sum(r["latency_ms"] for r in rows) / len(rows)

    summary = (
        f"retrieval hit rate:  {retrieval_hit_rate:.0%}  ({sum(r['retrieval_hit'] for r in in_corpus)}/{len(in_corpus)})\n"
        f"answer accuracy:     {answer_accuracy:.0%}  ({sum(r['answer_correct'] for r in in_corpus)}/{len(in_corpus)})\n"
        f"hallucination rate:  {hallucination_rate:.0%}  ({sum(r['hallucinated'] for r in out_of_scope)}/{len(out_of_scope)})\n"
        f"avg latency:         {avg_latency:.0f} ms"
    )
    print("\n" + summary)

    _write_report(rows, summary)
    print(f"\nfull report written to {RESULTS_PATH}")


def _write_report(rows, summary) -> None:
    lines = [
        "# Retrieval eval results",
        "",
        f"Run against the live deployed API, {len(rows)} questions "
        f"({sum(r['type'] == 'in_corpus' for r in rows)} in-corpus, "
        f"{sum(r['type'] == 'out_of_scope' for r in rows)} out-of-scope).",
        "",
        "```",
        summary,
        "```",
        "",
        "| Question | Type | Result | Provider | Latency |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        if r["type"] == "in_corpus":
            result = "hit + correct" if r["retrieval_hit"] and r["answer_correct"] else (
                "retrieved, wrong answer" if r["retrieval_hit"] else "missed retrieval"
            )
        else:
            result = "declined (correct)" if not r["hallucinated"] else "hallucinated"
        lines.append(
            f"| {r['question']} | {r['type']} | {result} | {r['provider']} | {r['latency_ms']}ms |"
        )
    RESULTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
