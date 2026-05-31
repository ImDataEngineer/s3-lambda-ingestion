# Your first AWS ingestion, all local — `ingestion.s3-lambda-localstack`

> **Level**: junior · **Estimated time**: ~10 h · **Paid IAmDataEng project**
> **Framework axes**: `ingestion`, `software_engineering_dataops`

You're going to build a real AWS pipeline — S3 triggering a Lambda that
writes to DynamoDB — **with no AWS account**, no credit card, no
LocalStack Pro. Just LocalStack Community, Terraform, and boto3.
Provisioned the way production is (declarative, idempotent), tested the
way production is (end-to-end, automated).

Not a tutorial. You read, you code, you push, CI tells you whether it
passes — with a clear message on every failure pointing at what to fix.

---

## The context

**Sobral**, a logistics startup. At the end of each route, every driver's
tablet drops a CSV of deliveries on S3. Dispatch needs to look up every
delivery for a given truck in under 10 ms — that's a DynamoDB use case,
not a data warehouse one.

Today: a Python script runs on an EC2 the lead has to SSH into every
morning. The lead wants **serverless**, and wants you to wire it
properly: Terraform, scoped IAM, error handling, idempotence.

The catch: you don't have an AWS account and you're not paying for one.
You're going to build **against the AWS APIs** using LocalStack for dev
and CI. When a recruiter asks "have you done AWS work?", you can tell
the truth — *"I built against the AWS APIs using LocalStack, here's the
repo, here's the CI rubric proving it runs"* — instead of padding your
resume.

---

## What you ship

| Deliverable | Where |
|---|---|
| The Lambda code | `src/lambda_handler.py` (S3 -> DynamoDB handler + dead-letter) |
| The LocalStack-aware boto3 helper | `src/aws_config.py` (provided — re-read it) |
| The Terraform infrastructure | `terraform/main.tf` (S3 + DynamoDB + IAM + Lambda + event notification) |
| The compose stack | `docker-compose.yml` (LocalStack Community, provided) |
| The CSV fixture | `fixtures/deliveries_2026-04-16.csv` (1200 rows, 2 malformed, provided) |

You **don't touch** the fixture generator (`fixtures/generate_fixtures.py`)
— the seed is calibrated so the CI rubric asserts on exact numbers (1198
valid rows, 2 malformed). Change the seed and your checks diverge.

---

## Getting started

### In GitHub Codespaces (recommended, zero setup)

The devcontainer does everything automatically on open:
- Python 3.11 + boto3 + pytest installed
- Terraform 1.9.5 + AWS CLI on PATH
- LocalStack starts via `docker compose up -d localstack`
- The CSV fixture is regenerated

Open the Codespace, wait ~90s, code.

### Locally

```bash
# 1. Prerequisites: Docker + docker compose + Terraform 1.6+ + Python 3.11
pip install -r requirements.txt

# 2. Start LocalStack
docker compose up -d localstack

# 3. Generate the fixture (deterministic — seed = 42)
python -m fixtures.generate_fixtures

# 4. Run the assessment rubric (fails until your code stops raising NotImplementedError)
pytest tests/ -v
```

Once your 6 tests pass locally, **commit + push** to your fork. GitHub
Actions CI replays the same rubric — on a clean runner, with a fresh
LocalStack stack — and the IAmDataEng app displays the verdict in your
dashboard.

---

## The architecture

```
                  PutObject CSV
                      |
                      v
   +---------------------------------------+
   |  s3://sobral-deliveries/incoming/*    |
   +---------------------------------------+
                      |
        S3 event notification (ObjectCreated:*)
                      |
                      v
              +---------------+
              |  Lambda       |
              |  sobral_ingest|
              +---------------+
               |             |
   parse_row valid       parse_row None (malformed)
               |             |
               v             v
       batch_write_item    PutObject
       DynamoDB            s3://.../dead-letter/<basename>.csv
       delivery_events
       (pk=truck_id, sk=delivery_id)
```

Read this box for 30 seconds. Everything that follows is just coding up
this diagram.

---

## The 6 rubric checks

Defined in `tests/test_evaluate.py`. Each failure prints a clear
pedagogical message.

| # | Id | What we check |
|---|---|---|
| 1 | `terraform_apply_succeeds_against_localstack` | `terraform init && apply` exits 0 against the LocalStack endpoints, and the 3 outputs (bucket, table, function) are declared. |
| 2 | `dynamodb_row_count_matches` | After uploading the fixture, the `delivery_events` table contains **exactly 1198 items**. Not more (bad validation) and not less (ignored `UnprocessedItems`). |
| 3 | `malformed_rows_quarantined` | The `dead-letter/` prefix contains **one** object, with **2 rows** — the 2 bad rows from the fixture. Not zero (you dropped the data), not N (one file per bad row is noisy). |
| 4 | `lambda_completes_under_timeout` | Lambda execution time is < 10s for 1200 rows. Anything longer means you're doing `put_item` in a loop instead of `batch_write_item`. |
| 5 | `iam_role_least_privilege` | Static check on `main.tf`: no `actions = ["*"]` or `resources = ["*"]`. LocalStack doesn't enforce IAM, but we grade your discipline. |
| 6 | `readme_documents_localstack_boundary` | The `README.fr.md` contains a section listing ≥ 2 LocalStack Community limitations vs real AWS (see below, to be filled in). |

---

## The traps juniors fall into

Seen ten times in code review:

- **Believing LocalStack === AWS.** LocalStack Community does NOT enforce
  IAM by default. A Lambda with an empty policy will still write to
  DynamoDB. On real AWS, that would have failed with `AccessDenied`.
  That is EXACTLY why rubric check 5 does a static check on your policy:
  if LocalStack doesn't punish you, the human reviewer must.

- **`put_item` in a loop over 1200 rows.** 1200 network round-trips, even
  over loopback, is ~30s. You time out. `batch_write_item` takes 25
  items max per call — that's the DynamoDB limit, not an optional
  detail.

- **Ignoring `UnprocessedItems`.** Under throttling (and sometimes even
  without, depending on the DynamoDB version), `batch_write_item`
  returns some of your items in `UnprocessedItems`. That is NOT an error
  — it's a "retry me" signal. Loop until the list is empty, with a
  max-attempts counter so you don't loop forever on a real outage.

- **Crashing on the first bad row.** If your handler raises instead of
  quarantining, S3 will re-deliver the event in a loop (Lambda retries)
  and you'll never ingest the 1198 good rows. A bad row is data, not an
  exception.

- **`s3.get_object(...)["Body"].read()` then split in memory.** Works on
  1200 rows. OOMs on 10 million. Learn streaming now:
  `csv.DictReader(io.TextIOWrapper(obj["Body"]))` — no full read.

- **Committing `.tfstate` to git.** It's in `.gitignore` but we've seen
  it force-committed more than once. State contains resource IDs and
  sometimes plaintext secrets. Never.

- **`terraform apply` that isn't idempotent.** A `local-exec` with `aws
  s3 cp` on every apply, a `random_id` without `keepers`, a timestamp
  in a resource name — all these break the "apply twice = same result"
  promise. CI checks this indirectly (a re-apply must not break the
  state).

- **Claiming on your resume "I built on AWS".** Be precise: *"I built
  against the AWS APIs using LocalStack Community for local dev and
  CI"*. Serious recruiters respect precision. The recruiters who
  reward padding aren't the ones you want.

---

## Differences vs real AWS

This section is **required by the rubric** (check 6). You must list at
least **2 distinct limitations** from the following, with enough detail
to show that you know what LocalStack did NOT teach you:

- **IAM not enforced in Community.** LocalStack Community doesn't verify
  permissions by default. On real AWS, a Lambda without `s3:GetObject`
  on the correct ARN gets `AccessDenied` at runtime, not at `terraform
  apply` time. Consequence: your IAM check has to be *static* (reading
  the policy) or *dynamic integration on an AWS sandbox*, not dynamic
  locally.

- **Network latency and cost.** On LocalStack everything is loopback
  (~0.1 ms). On AWS, a cross-region `batch_write_item` call takes 5-50
  ms. The cumulative cost of 1200 round-trips becomes meaningful.
  That's why `batch_*` APIs exist — not just to respect a limit, but to
  amortize latency.

- **KMS / S3 encryption (SSE).** By default a LocalStack bucket accepts
  unencrypted objects. On AWS, a serious org enforces `aws:kms` via a
  bucket policy; your Terraform has to declare
  `aws_s3_bucket_server_side_encryption_configuration` and your IAM
  policy has to allow `kms:GenerateDataKey` on the right key.

- **Lambda concurrency and throttling.** LocalStack doesn't apply
  concurrency limits by default. On AWS, a burst of S3 uploads can
  saturate your Lambda concurrency and trigger `TooManyRequestsException`
  errors — you have to either set `reserved_concurrent_executions`, or
  handle throttling S3-side (SQS DLQ).

- **Observability.** CloudWatch logs from LocalStack Lambda are not
  bit-identical to AWS ones (`REPORT` line format, for example). For
  production observability, plan on X-Ray + custom metrics — none of
  that is testable locally.

- **VPC / network isolation.** LocalStack doesn't simulate VPC
  endpoints, security groups, or NAT gateways. On AWS, if your Lambda
  sits in a private VPC to talk to an RDS, you have to declare a VPC
  endpoint for S3 and DynamoDB (otherwise traffic exits via the public
  internet).

Pick your 2-3 favorite angles and write them up. One paragraph per
limitation, not a dry bullet list.

---

## Going further

No reading is mandatory to pass this project. But if you want to
understand WHY the patterns above exist:

- **Joe Reis & Matt Housley**, *Fundamentals of Data Engineering*
  (O'Reilly, 2022) — **ch. 7 "Ingestion", pp. 230-255**: event-driven
  ingestion, batch via S3-triggered Lambda, consumer-side idempotence.
- **Yan Cui**, *Production-Ready Serverless* — Lambda error-handling
  patterns, DLQ, handler-side idempotence (the patterns are the same
  even when you test on LocalStack).
- **AWS docs**:
  - [Lambda + S3 event source](https://docs.aws.amazon.com/lambda/latest/dg/with-s3.html)
  - [DynamoDB BatchWriteItem](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_BatchWriteItem.html) — pay attention to the `UnprocessedItems` section.
- **LocalStack docs**: [Community feature coverage](https://docs.localstack.cloud/user-guide/aws/feature-coverage/)
  — the matrix that tells you explicitly what is Community vs Pro.
- **Terraform docs**: [AWS provider — endpoints](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/guides/custom-service-endpoints) — how to point at LocalStack.

---

## If you're stuck

The project is calibrated for ~10 h. If you've been spinning on the same
check for more than an hour and a half:

1. Re-read the test error message — it almost always points at the cause.
2. Inspect LocalStack by hand:
   - `aws --endpoint-url=http://localhost:4566 s3 ls s3://sobral-deliveries/`
   - `aws --endpoint-url=http://localhost:4566 dynamodb scan --table-name delivery_events`
   - `aws --endpoint-url=http://localhost:4566 logs tail /aws/lambda/sobral_ingest`
3. Open an issue on your fork with the `help-wanted` label — the
   IAmDataEng community hangs out there.

Good luck.
