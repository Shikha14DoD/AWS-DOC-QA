# Retrieval eval results

Run against the live deployed API, 11 questions (8 in-corpus, 3 out-of-scope).

```
retrieval hit rate:  100%  (8/8)
answer accuracy:     100%  (8/8)
hallucination rate:  0%  (0/3)
avg latency:         5858 ms
```

| Question | Type | Result | Provider | Latency |
|---|---|---|---|---|
| What is the maximum execution timeout for a Lambda function? | in_corpus | hit + correct | gemini | 4743ms |
| How much memory can a single Lambda function be configured with? | in_corpus | hit + correct | gemini | 13902ms |
| What is the default concurrent execution limit per AWS account for Lambda? | in_corpus | hit + correct | gemini | 3832ms |
| What is the maximum size of a single DynamoDB item? | in_corpus | hit + correct | gemini | 2818ms |
| Which DynamoDB billing mode has no cost when the table sits idle? | in_corpus | hit + correct | gemini | 2843ms |
| Can a DynamoDB Local Secondary Index be added to a table after it's created? | in_corpus | hit + correct | gemini | 5905ms |
| How many nines of durability does S3 Standard offer? | in_corpus | hit + correct | groq | 5299ms |
| What happens when you delete an object in a versioning-enabled S3 bucket? | in_corpus | hit + correct | gemini | 10670ms |
| What is the current price of a Tesla Model 3? | out_of_scope | declined (correct) | groq | 4875ms |
| Who won the 2024 US presidential election? | out_of_scope | declined (correct) | groq | 5054ms |
| What is the weather in Paris right now? | out_of_scope | declined (correct) | gemini | 4492ms |
