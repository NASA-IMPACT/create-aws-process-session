# create-aws-process-session

A complete kit for vending short-lived AWS credentials to external collaborators
so they can call Amazon Bedrock from their own machines — **without ever
provisioning long-lived IAM users for them**.

The system has two halves:

| Half | Lives in | What it is |
| ---- | -------- | ---------- |
| **Backend** (`cdk/`) | the AWS account you want Bedrock invoked in | API Gateway + 2 Lambdas + 2 IAM roles + Secrets Manager. Deployed via CDK. |
| **Client installer** (`creds-installer.py`) | each researcher's laptop | One curl-pipe-to-python command. Writes a credential helper to `~/.aws/get_temp_creds.py` and adds a `temp-bedrock` profile to `~/.aws/credentials`. |

When a researcher runs `aws --profile temp-bedrock <anything>`, the AWS CLI
calls the helper, which hits the API with an `x-api-key` header, gets back
short-lived STS credentials (the role can invoke Bedrock), and hands them to
the AWS CLI. The CLI caches them until expiry; the next call refreshes
silently. Researchers never see the rotation.

---

## Architecture

```
researcher's machine                          AWS account that owns Bedrock access
─────────────────                              ───────────────────────────────────
aws --profile temp-bedrock                              ┌──────────────────────┐
        │                                               │ API Gateway          │
        ▼                                               │   GET /credentials   │
~/.aws/get_temp_creds.py  ──── x-api-key header ────▶   │   custom authorizer  │
        │                                               └──────────┬───────────┘
        │                                                          │
        │  AWS CLI ◀─── JSON creds ───┐                            ▼
        │                              │                  ┌─────────────────┐
        │                              │                  │ Authorizer λ    │
        ▼                              │                  │  reads API key  │
~/.aws/credentials_cache_python.json   │                  │  from Secrets   │
                                       │                  │  Manager        │
                                       │                  └────────┬────────┘
                                       │                           │ Allow
                                       │                  ┌────────▼────────┐
                                       │                  │ Credentials λ   │
                                       └──────────────────│  sts:AssumeRole │
                                                          │  on bedrock-    │
                                                          │  creds-bedrock- │
                                                          │  access role    │
                                                          └────────┬────────┘
                                                                   │
                                                          ┌────────▼────────┐
                                                          │ IAM Role        │
                                                          │ bedrock-creds-  │
                                                          │ bedrock-access  │
                                                          │  bedrock:Invoke*│
                                                          │  bedrock:Converse│
                                                          └─────────────────┘
```

---

## Researcher usage

### Prerequisites

- macOS, Linux, or Windows (use WSL on Windows)
- Python 3.8+ with `requests`: `python3 -m pip install requests`
- AWS CLI v2 (`brew install awscli` on macOS)
- An **API URL** and **API key** sent to you privately. Treat the API key like a password.

### One-time setup

```bash
export AWS_GET_TEMP_CREDS_API_URL=<URL you were given>
export AWS_GET_TEMP_CREDS_API_KEY=<KEY you were given>
export AWS_REGION=us-west-2
export AWS_PROFILE=temp-bedrock

curl -s https://raw.githubusercontent.com/NASA-IMPACT/create-aws-process-session/refs/heads/main/creds-installer.py | python3
```

You should see:

```
✓ Created executable script: ~/.aws/get_temp_creds.py
✓ Updated AWS credentials file: ~/.aws/credentials
✓ Added [temp-bedrock] profile with credential_process
```

### Verify it works

```bash
aws sts get-caller-identity --profile temp-bedrock
```

Expected output: a JSON block showing an assumed-role ARN ending in
`/bedrock-creds-bedrock-access/bedrock-creds-session`.

### Invoke a model

```bash
aws bedrock-runtime converse \
  --profile temp-bedrock \
  --region us-west-2 \
  --model-id meta.llama3-1-8b-instruct-v1:0 \
  --messages '[{"role":"user","content":[{"text":"Hello"}]}]' \
  --inference-config '{"maxTokens":100}'
```

From Python with boto3:

```python
import boto3
sess = boto3.Session(profile_name="temp-bedrock", region_name="us-west-2")
brt = sess.client("bedrock-runtime")
r = brt.converse(
    modelId="meta.llama3-1-8b-instruct-v1:0",
    messages=[{"role": "user", "content": [{"text": "Hello"}]}],
    inferenceConfig={"maxTokens": 100},
)
print(r["output"]["message"]["content"][0]["text"])
```

### Day-to-day usage

Set the profile in any shell session and use any AWS-aware tool normally:

```bash
export AWS_PROFILE=temp-bedrock
```

There is nothing to refresh, log into, or rotate. The helper auto-refreshes
credentials when they're within 5 minutes of expiry.

### Troubleshooting

| Symptom | Likely cause / fix |
| ------- | ------------------ |
| `Error when retrieving credentials from custom-process:` | The helper failed. AWS CLI hides the error — run `~/.aws/get_temp_creds.py` directly to see it. |
| `HTTP 401 from credentials API: {"message":"Unauthorized"}` | API key invalid or revoked. Contact the operator. |
| `HTTP 403 ... explicit deny` | API key valid but request denied by the authorizer (e.g. IP allow-list). |
| `ModuleNotFoundError: No module named 'requests'` | `python3 -m pip install requests` |
| `ResourceNotFoundException: Model use case details have not been submitted` | Bedrock account-level setting. The **operator** of the AWS account must fill out the model's use-case form once in the Bedrock console (Settings → Model access). |
| `AccessDeniedException: ... bedrock:InvokeModel` | The role doesn't permit that model. Ask the operator to broaden the policy, or use a permitted model family (anthropic, meta). |

---

## Operator: deploying the backend

You'll deploy the API Gateway + Lambdas + IAM roles in the AWS account that
should host Bedrock invocations and be billed for them.

### Prerequisites

- AWS account with admin (or a role that can create IAM roles, Lambdas, API Gateway, Secrets Manager)
- Node 20+ and AWS CDK CLI: `npm install -g aws-cdk`
- Python 3.10+
- AWS CLI configured with credentials for the target account

### One-time bootstrap

CDK requires bootstrapping the target account/region (creates the
`CDKToolkit` CloudFormation stack with an S3 staging bucket).

```bash
cd cdk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cdk bootstrap aws://<ACCOUNT_ID>/<REGION>
```

### Deploy

```bash
cdk deploy
```

You'll get three outputs:

- `ApiUrl` — value for researchers' `AWS_GET_TEMP_CREDS_API_URL`
- `ApiKeySecretArn` — fetch the API key value with:
  ```bash
  aws secretsmanager get-secret-value \
    --secret-id bedrock-creds/api-key \
    --query SecretString --output text
  ```
- `BedrockRoleArn` — the role researchers' credentials assume

Distribute the API URL and API key to researchers via a secure channel. Do
**not** commit either to source control.

### What gets created

| Resource | Purpose |
| -------- | ------- |
| API Gateway REST API `bedrock-creds-api` | Endpoint at `/credentials` |
| Custom REQUEST authorizer | Validates `x-api-key` header against the secret |
| Lambda `bedrock-creds-authorizer` | Compares header to Secrets Manager value; optionally checks source IP |
| Lambda `bedrock-creds-credentials` | Calls `sts:AssumeRole`, returns short-lived creds |
| IAM role `bedrock-creds-bedrock-access` | What researchers actually get — invoke Bedrock on anthropic + meta foundation models |
| IAM role `bedrock-creds-credentials-lambda` | Lambda exec role; can assume the bedrock role |
| IAM role `bedrock-creds-authorizer-lambda` | Lambda exec role; can read the API key secret |
| Secrets Manager secret `bedrock-creds/api-key` | The API key, auto-generated on first deploy |
| API Gateway usage plan | Throttle 10 req/s, burst 20, 10k req/day |

### Improvements over the legacy hand-built stack

This CDK replaces an earlier hand-built version. Differences:

- **API key in Secrets Manager** rather than a plain Lambda env var (which is
  readable by anyone with `lambda:GetFunctionConfiguration`).
- **Authorizer caching** (5-minute TTL) — was 0 in the legacy stack, hitting
  Lambda on every request.
- **API Gateway usage plan** with rate-limit and daily quota — was absent.
- **Bedrock model scope**: `anthropic.*` + `meta.*` foundation-model ARNs and
  this account's inference profiles, rather than `"Resource": "*"`.
- **Env var renamed**: `BEDROCK_ROLE_ARN` (was `S3_PUT_ROLE_ARN`, a leftover
  from another project).
- **Log retention**: 30 days on both Lambdas.

### Cleanup

```bash
cdk destroy
```

The Secrets Manager secret has a deletion delay (default 30 days) so you can
recover the API key if you destroy by mistake.

---

## Notes

- The helper writes its API key into `~/.aws/get_temp_creds.py` in plaintext;
  the installer now chmod's that file to `0o700` (owner only).
- For Anthropic Claude 4.x models, AWS Bedrock requires the account owner to
  complete the model's use-case form once. See the troubleshooting table.
