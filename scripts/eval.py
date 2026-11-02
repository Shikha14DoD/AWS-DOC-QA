"""Retrieval + answer evaluation harness.

Runs a fixed question set (eval/questions.json) against the deployed query
API and reports:

  - retrieval hit rate: for "in_corpus" questions, did a chunk from the
    expected source document show up in the top-k citations at all?
  - answer accuracy: keyword match (fast, brittle) and, with --judge, an LLM
    judge's yes/no call on whether the answer actually matches
    expected_answer - catches a correct answer phrased without the exact
    keyword, and a wrong answer that happens to contain one.
  - hallucination rate: for "out_of_scope" questions (nothing in the corpus
    answers them), did the model fabricate an answer instead of declining?

This measures the deployed system, not a mock - it's a real check of whether
the "answer only from context" prompt design (see query.py) actually holds up.

The chat model runs at temperature 0.2, so a single run can land differently
run to run. --runs N repeats the whole question set N times and reports the
range across runs, not just one sample of it.

Usage:
    python scripts/eval.py
    python scripts/eval.py --judge          # also LLM-grade each answer
    python scripts/eval.py --runs 3 --judge  # repeat 3x, report the range
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import boto3

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "lambdas" / "layers" / "common" / "python")
)
from doc_qa_common import gemini  # noqa: E402

# LLMs favor typographically "correct" Unicode punctuation over plain ASCII -
# a narrow no-break space (U+202F) between a number and its unit ("400 KB"),
# a non-breaking hyphen (U+2011) in compound words ("on‑demand"), en/em
# dashes for a regular hyphen. An exact-string keyword check would call a
# genuinely right answer wrong over it, so normalize both sides before
# comparing.
_WS = re.compile(r"\s+")
_DASH = re.compile(r"[‐‑‒–—−]")


def _normalize(text: str) -> str:
    return _WS.sub(" ", _DASH.sub("-", text))

STACK_NAME = "InfrastructureStack"
REGION = "us-east-2"
QUESTIONS_PATH = Path(__file__).resolve().parents[1] / "eval" / "questions.json"
RESULTS_PATH = Path(__file__).resolve().parents[1] / "eval" / "results.md"

# Same SSM path the deployed stack itself reads from (infrastructure_stack.py's
# GEMINI_API_KEY_PARAM) - the judge calls Gemini directly, not through the API.
os.environ.setdefault("GEMINI_API_KEY_PARAM", "/docqa/gemini-api-key")

JUDGE_MODEL = "gemini-3.6-flash"

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


JUDGE_PROMPT = """You are grading one answer from a document Q&A system.

Question: {question}
Expected answer: {expected_answer}
System's actual answer: {answer}

Does the system's answer convey the same fact as the expected answer? Minor
wording differences are fine; a materially different or missing fact is not.
Reply with exactly one word: "yes" or "no"."""


def judge_answer(question: str, expected_answer: str, answer: str) -> bool | None:
    """LLM-graded correctness: does `answer` match `expected_answer`'s fact?

    Catches what a keyword check can't - a right answer phrased without the
    exact keyword, or a wrong one that happens to contain it. Returns None
    (not True/False) on a judge failure, so a flaky grading call doesn't get
    silently counted as wrong.
    """
    try:
        verdict = gemini.generate(
            JUDGE_PROMPT.format(question=question, expected_answer=expected_answer, answer=answer),
            model=JUDGE_MODEL,
            temperature=0,
        )
    except Exception as exc:
        print(f"  (judge call failed: {exc})")
        return None
    return verdict.strip().lower().startswith("y")


def run_once(questions: list[dict], base_url: str, use_judge: bool) -> list[dict]:
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
            normalized_answer = _normalize(answer).lower()
            row["answer_correct"] = any(
                _normalize(kw).lower() in normalized_answer for kw in q["expected_keywords"]
            )
            if use_judge:
                row["judge_correct"] = judge_answer(q["question"], q["expected_answer"], answer)
        else:  # out_of_scope
            row["hallucinated"] = not looks_like_decline(answer)

        rows.append(row)
        ok = row.get("judge_correct") if use_judge else None
        if ok is None:
            ok = row.get("answer_correct", not row.get("hallucinated"))
        print(f"[{q['type']:11}] {q['question'][:60]:60} -> {'OK' if ok else 'MISS'}")

    return rows


def summarize(rows: list[dict], use_judge: bool) -> dict:
    in_corpus = [r for r in rows if r["type"] == "in_corpus"]
    out_of_scope = [r for r in rows if r["type"] == "out_of_scope"]

    correctness_key = "judge_correct" if use_judge else "answer_correct"
    # A None (judge call failed) doesn't count as either correct or wrong -
    # drop it from the denominator instead of silently scoring it as a miss.
    graded = [r for r in in_corpus if r.get(correctness_key) is not None]

    return {
        "retrieval_hit_rate": sum(r["retrieval_hit"] for r in in_corpus) / len(in_corpus),
        "answer_accuracy": (sum(r[correctness_key] for r in graded) / len(graded)) if graded else 0.0,
        "answer_accuracy_n": len(graded),
        "hallucination_rate": sum(r["hallucinated"] for r in out_of_scope) / len(out_of_scope),
        "avg_latency_ms": sum(r["latency_ms"] for r in rows) / len(rows),
        "in_corpus_n": len(in_corpus),
        "out_of_scope_n": len(out_of_scope),
    }


def format_summary(m: dict) -> str:
    return (
        f"retrieval hit rate:  {m['retrieval_hit_rate']:.0%}\n"
        f"answer accuracy:     {m['answer_accuracy']:.0%}  (of {m['answer_accuracy_n']}/{m['in_corpus_n']} graded)\n"
        f"hallucination rate:  {m['hallucination_rate']:.0%}\n"
        f"avg latency:         {m['avg_latency_ms']:.0f} ms"
    )


def run(num_runs: int, use_judge: bool) -> None:
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    base_url = api_url()

    run_metrics = []
    last_rows = None
    for run_idx in range(1, num_runs + 1):
        if num_runs > 1:
            print(f"\n=== run {run_idx}/{num_runs} ===")
        rows = run_once(questions, base_url, use_judge)
        metrics = summarize(rows, use_judge)
        print("\n" + format_summary(metrics))
        run_metrics.append(metrics)
        last_rows = rows

    if num_runs > 1:
        print(f"\n=== across {num_runs} runs ===")
        for key in ("retrieval_hit_rate", "answer_accuracy", "hallucination_rate"):
            values = [m[key] for m in run_metrics]
            print(f"{key:20} min {min(values):.0%}  avg {sum(values) / len(values):.0%}  max {max(values):.0%}")

    _write_report(last_rows, run_metrics, use_judge)
    print(f"\nfull report written to {RESULTS_PATH}")


def _write_report(rows: list[dict], run_metrics: list[dict], use_judge: bool) -> None:
    num_runs = len(run_metrics)
    lines = [
        "# Retrieval eval results",
        "",
        f"Run against the live deployed API, {len(rows)} questions "
        f"({sum(r['type'] == 'in_corpus' for r in rows)} in-corpus, "
        f"{sum(r['type'] == 'out_of_scope' for r in rows)} out-of-scope)"
        + (f", {num_runs} repeated runs" if num_runs > 1 else "")
        + (", answer correctness LLM-judged against expected_answer" if use_judge else ", answer correctness by keyword match")
        + ".",
        "",
        "```",
        format_summary(run_metrics[-1]),
        "```",
    ]
    if num_runs > 1:
        lines += ["", f"Range across all {num_runs} runs:", "", "```"]
        for key in ("retrieval_hit_rate", "answer_accuracy", "hallucination_rate"):
            values = [m[key] for m in run_metrics]
            lines.append(
                f"{key:20} min {min(values):.0%}  avg {sum(values) / len(values):.0%}  max {max(values):.0%}"
            )
        lines.append("```")

    lines += [
        "",
        f"Detail for the last of the {num_runs} run(s):" if num_runs > 1 else "",
        "",
        "| Question | Type | Result | Provider | Latency |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        if r["type"] == "in_corpus":
            correct = r.get("judge_correct") if use_judge else r.get("answer_correct")
            if correct is None:
                result = "judge call failed"
            else:
                result = "hit + correct" if r["retrieval_hit"] and correct else (
                    "retrieved, wrong answer" if r["retrieval_hit"] else "missed retrieval"
                )
        else:
            result = "declined (correct)" if not r["hallucinated"] else "hallucinated"
        lines.append(
            f"| {r['question']} | {r['type']} | {result} | {r['provider']} | {r['latency_ms']}ms |"
        )
    RESULTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1, help="repeat the question set N times")
    parser.add_argument(
        "--judge", action="store_true",
        help="also grade each in-corpus answer with an LLM judge against expected_answer",
    )
    args = parser.parse_args()
    run(args.runs, args.judge)
