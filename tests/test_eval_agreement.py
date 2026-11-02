"""Offline test for the eval's judge-vs-human agreement check."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import eval as eval_script  # noqa: E402


def test_agreement_counts_only_rows_both_graded():
    records = [
        {"question": "a", "human_correct": True, "judge_correct": True},
        {"question": "b", "human_correct": False, "judge_correct": True},
        {"question": "c", "human_correct": None, "judge_correct": True},   # not hand-graded yet
        {"question": "d", "human_correct": True, "judge_correct": None},   # judge call failed
        {"question": "e", "type": "out_of_scope"},                          # no verdicts at all
    ]

    result = eval_script.judge_agreement(records)

    assert result == {"graded": 2, "agreed": 1, "disagreements": ["b"]}
