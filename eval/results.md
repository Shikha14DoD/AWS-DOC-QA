# Retrieval eval results

Run against the live deployed API, 19 questions (14 in-corpus, 5 out-of-scope), answer correctness LLM-judged against expected_answer.

```
retrieval hit rate:  71%  (10/14)
answer accuracy:     71%  (10/14 graded, of 14)
out-of-scope:        3/5 declined, 0 hallucinated
api errors:          6/19 requests failed (counted as misses)
avg latency:         3051 ms
```

Single run, 2026-11-02, recorded as-is (not re-run for a better number).

**How to read it:**

- **Every request that got an answer was right.** 10/10 answered in-corpus
  questions retrieved the right source and were judged correct. 3/3 answered
  out-of-scope questions were declined, and nothing was hallucinated.
- **6/19 requests failed (HTTP 502), and they count as misses above.** All six
  were Groq rate limits: its free tier allows 8,000 tokens/minute, each
  question used ~1,100, and the logs show 429s with "try again in ~5 s". The
  API is set to fail fast rather than wait (a 30 s request can't afford to),
  so those became 502s. This is the system's real free-tier capacity limit
  under a burst, not an eval bug.
- **Every answer came from Groq, not Gemini.** Gemini's free tier allows 20
  generate requests per day, and that day's were already used. So this
  measures the fallback path, and the judge (also Groq, same account) was
  grading Groq's own answers. The judge's calls also used the same
  tokens-per-minute budget as the API, which contributed to the 429s.
- **The first attempt at this run crashed and recorded nothing.** The API
  returned a 503 because the query Lambda waited out a long Gemini retry hint
  and hit its 30 s timeout before falling back. That was a real bug, fixed
  in PR #2 before this run.

## Judge spot-check (2026-11-02, later the same day)

A second run was made only to check the LLM judge, and its numbers don't
replace the recorded run above. Its answers and verdicts are in
[answers.json](answers.json). I graded all 14 answered in-corpus rows by hand
(`human_correct`) against `expected_answer` and the source docs, then ran
`python scripts/eval.py --agreement`:

```
judge agreed with the human on 14/14 rows
```

- The keyword check agreed on 13/14. It marked the LSI answer ("must be
  defined when the table is created; it cannot be added later") wrong for
  missing the keyword. The judge and I both marked it right.
- Answers came 7 from Gemini and 7 from Groq, so the judge wasn't only
  grading its own provider this time. One out-of-scope request failed with
  HTTP 502 (Groq tokens/minute again).
- Limit: every answer in this run was correct, so this shows the judge
  agrees on right answers. It doesn't show that it catches wrong ones.

Previous run (11 questions, keyword-graded, mostly Gemini): retrieval 8/8,
answers 7/8, out-of-scope 3/3 declined.

| Question | Type | Result | Provider | Latency |
|---|---|---|---|---|
| What is the maximum execution timeout for a Lambda function? | in_corpus | hit + correct | groq | 2720ms |
| How much memory can a single Lambda function be configured with? | in_corpus | hit + correct | groq | 1133ms |
| What is the default concurrent execution limit per AWS account for Lambda? | in_corpus | hit + correct | groq | 1294ms |
| What is the default timeout for a newly created Lambda function? | in_corpus | hit + correct | groq | 1198ms |
| What happens to a synchronous Lambda invocation when the concurrency limit is exceeded? | in_corpus | hit + correct | groq | 1363ms |
| What is the maximum size of a single DynamoDB item? | in_corpus | hit + correct | groq | 1312ms |
| Which DynamoDB billing mode has no cost when the table sits idle? | in_corpus | hit + correct | groq | 1237ms |
| Can a DynamoDB Local Secondary Index be added to a table after it's created? | in_corpus | API error (HTTP 502) | None | 4833ms |
| How many Global Secondary Indexes can a DynamoDB table have by default? | in_corpus | hit + correct | groq | 2897ms |
| Can a Global Secondary Index serve a strongly consistent read? | in_corpus | API error (HTTP 502) | None | 4912ms |
| How many nines of durability does S3 Standard offer? | in_corpus | hit + correct | groq | 1213ms |
| What happens when you delete an object in a versioning-enabled S3 bucket? | in_corpus | API error (HTTP 502) | None | 4789ms |
| How many lifecycle rules can a single S3 bucket have? | in_corpus | hit + correct | groq | 1077ms |
| Which S3 storage class has the slowest object retrieval time? | in_corpus | API error (HTTP 502) | None | 4784ms |
| What is the current price of a Tesla Model 3? | out_of_scope | declined (correct) | groq | 5158ms |
| Who won the 2024 US presidential election? | out_of_scope | API error (HTTP 502) | None | 4995ms |
| What is the weather in Paris right now? | out_of_scope | declined (correct) | groq | 2908ms |
| What is the capital of France? | out_of_scope | API error (HTTP 502) | None | 4910ms |
| Translate 'good morning' into Spanish. | out_of_scope | declined (correct) | groq | 5227ms |
