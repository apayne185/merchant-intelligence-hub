# Terraform — AWS deployment for the Merchant Intelligence Copilot

**Status: not currently deployed.** This is IaC for how the Copilot would
run on AWS — written, locally validated (`terraform fmt`/`init`/`validate`,
plus the Docker image built and run end-to-end), but never `apply`'d against
a real AWS account. Nothing here costs anything until you run `apply`
yourself. See DECISIONS.md D33 for the full design rationale.

## What this deploys

ECS Fargate (1 task, no autoscaling) running the Copilot API
(`src/copilot/api.py`) behind an ALB, in a minimal dedicated VPC (2 public
subnets, no NAT gateway). No RDS, no S3 — the app's in-memory vector store
and its small data files are baked directly into the container image. Runs
with `MOCK_LLM=1` by default: zero API cost, deterministic, no
`OPENAI_API_KEY` required. See the architecture table in the root
`README.md`.

## Prerequisites

- An AWS account and credentials configured locally (`aws configure`, or
  equivalent env vars / SSO profile) — **you run `apply`/`destroy`
  yourself; nothing here does it for you.**
- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.7
- [Docker](https://docs.docker.com/get-docker/) (with `buildx`, included by
  default in recent Docker Desktop/Engine)
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
  (for the `aws ecr get-login-password` step below)

## Spin up

```bash
cd terraform
terraform init
terraform apply   # creates the VPC/ALB/ECS cluster/ECR repo — review the
                   # plan it shows you before typing yes
```

The first `apply` creates an **empty** ECR repository — the ECS service
won't have anything to run yet. Build and push the image, then re-apply
(or just update the service) to actually start the task:

```bash
cd ..   # repo root

# Match the Fargate task's architecture explicitly — see DECISIONS.md D33.
docker buildx build --platform linux/amd64 -t merchant-copilot:latest .

REPO_URL=$(cd terraform && terraform output -raw ecr_repository_url)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$REPO_URL"

docker tag merchant-copilot:latest "$REPO_URL:latest"
docker push "$REPO_URL:latest"

cd terraform
terraform apply -replace=aws_ecs_service.app   # forces a fresh deployment pulling the image you just pushed
```

Get the URL:

```bash
terraform output alb_dns_name
# wait ~30-60s for the task to pass its health check (health_check_grace_period_seconds), then:
curl "$(terraform output -raw alb_dns_name)/health"
```

## Tear down

```bash
cd terraform
terraform destroy
```

`force_delete = true` on the ECR repo (DECISIONS.md D33) means this
succeeds even with an image still pushed — without it, `destroy` fails
partway through and leaves resources (and their cost) running.

## Cost (us-east-1, on-demand rates)

| Component | ~3hr demo session | Full month (730hr) if left running |
|---|---|---|
| Fargate 0.5vCPU/1GB | ~$0.07 | ~$18 |
| ALB | ~$0.07 | ~$16-20 |
| ECR storage / CloudWatch Logs | ~$0.00 | ~$0.10-3 |
| Secrets Manager (if `enable_openai_secret=true`) | ~$0.00 | $0.40 |
| **Total** | **~$0.15-0.30** | **~$35-58** |

No NAT gateway is a meaningful part of why the "forgot to destroy" ceiling
stays this low — it would otherwise add ~$33+/month baseline by itself.
This is a real but bounded number, not a runaway-bill risk, as long as you
run `terraform destroy` when you're done. `terraform apply` will tell you
exactly what it's about to create before it creates anything.

## Real (non-mock) mode

Two independent `terraform.tfvars` settings (copy `terraform.tfvars.example`),
deliberately not coupled — enabling the secret alone never silently starts
spending on OpenAI calls:

1. `enable_openai_secret = true`, apply, then populate the created Secrets
   Manager secret with a real key.
2. `mock_llm = "0"`, apply again — this is what actually switches the
   running container out of mock mode.

Not done by default — the whole point of this deployment is a zero-cost,
deterministic demo.

## What was and wasn't verified

- **Verified**: the Docker image builds and runs correctly — `GET /health`
  and a risk-routed `POST /ask` both tested from the host shell against a
  running container (not `docker exec`, which would falsely succeed even
  with a broken host binding — see DECISIONS.md D33). `terraform fmt
  -check`, `terraform init`, and `terraform validate` (including the
  `enable_openai_secret=true` branch) all pass.
- **Not verified**: `terraform plan`/`apply` against a real AWS account —
  there are no AWS credentials in the environment this was built in. The
  resource wiring has been checked carefully (see DECISIONS.md D33 for the
  specific failure modes an independent architecture review caught and
  this configuration addresses), but a real `apply` is the only way to
  fully confirm it end-to-end. That step is yours to run.
