# Retrieval eval results

Run against the live deployed API, 11 questions (8 in-corpus, 3 out-of-scope).

```
retrieval hit rate:  100%  (8/8)
answer accuracy:     62%  (5/8)
hallucination rate:  0%  (0/3)
avg latency:         6911 ms
```

| Question | Type | Result | Provider | Latency |
|---|---|---|---|---|
| What is the maximum execution timeout for a Lambda function? | in_corpus | hit + correct | gemini | 11678ms |
| How much memory can a single Lambda function be configured with? | in_corpus | hit + correct | gemini | 5552ms |
| What is the default concurrent execution limit per AWS account for Lambda? | in_corpus | hit + correct | gemini | 3132ms |
| What is the maximum size of a single DynamoDB item? | in_corpus | hit + correct | gemini | 2485ms |
| Which DynamoDB billing mode has no cost when the table sits idle? | in_corpus | retrieved, wrong answer | groq | 7062ms |
| Can a DynamoDB Local Secondary Index be added to a table after it's created? | in_corpus | retrieved, wrong answer | groq | 5261ms |
| How many nines of durability does S3 Standard offer? | in_corpus | retrieved, wrong answer | groq | 5344ms |
| What happens when you delete an object in a versioning-enabled S3 bucket? | in_corpus | hit + correct | gemini | 6680ms |
| What is the current price of a Tesla Model 3? | out_of_scope | declined (correct) | groq | 5295ms |
| Who won the 2024 US presidential election? | out_of_scope | declined (correct) | groq | 6504ms |
| What is the weather in Paris right now? | out_of_scope | declined (correct) | gemini | 17032ms |
