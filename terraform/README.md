# Terraform: AWS ECS deployment

**English** | [Español](#terraform-despliegue-en-aws-ecs)

**Status: validated, not applied.** `terraform fmt`, `init` and `validate` pass and the image is tested end to end in CI, but this configuration has not been applied to a real AWS account. Nothing here costs anything until you run `terraform apply`. Design rationale: [DECISIONS.md D20](../DECISIONS.md#d20).

## What it deploys

ECS Fargate (one task) running the API behind an Application Load Balancer, in a minimal dedicated VPC (two public subnets, no NAT gateway), with an ECR repository, CloudWatch logs with explicit retention, and an execution role but no task role (the app calls no AWS APIs). The fact store, filing passages and prices are baked into the image. Runs with `MOCK_LLM=1` by default: zero LLM cost, no API key needed.

## Spin up

```bash
cd terraform
terraform init
terraform apply                       # review the plan before typing yes

cd ..
docker buildx build --platform linux/amd64 -t filings-copilot:latest .
REPO_URL=$(cd terraform && terraform output -raw ecr_repository_url)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$REPO_URL"
docker tag filings-copilot:latest "$REPO_URL:latest" && docker push "$REPO_URL:latest"
cd terraform && terraform apply -replace=aws_ecs_service.app

curl "$(terraform output -raw alb_dns_name)/health"
```

Tear down with `terraform destroy` (the ECR repository has `force_delete = true`, so this succeeds with images still pushed).

## Cost (us-east-1, on demand)

| Component | ~3 hour session | Month, if left running |
|---|---|---|
| Fargate 0.5 vCPU / 1 GB | ~$0.07 | ~$18 |
| ALB | ~$0.07 | ~$16-20 |
| ECR storage, CloudWatch logs | ~$0.00 | ~$0.10-3 |
| Secrets Manager (if `enable_openai_secret = true`) | ~$0.00 | $0.40 |
| **Total** | **~$0.15-0.30** | **~$35-58** |

Skipping the NAT gateway keeps the "forgot to destroy" ceiling low; it would add about $33 a month on its own.

## Real LLM mode

Two independent settings in `terraform.tfvars` (copy `terraform.tfvars.example`), deliberately not coupled so that creating the secret never starts spending by itself: `enable_openai_secret = true` (then put a key in the created secret), and `mock_llm = "0"`.

## Not covered here

The EDGAR ingestion workers (DECISIONS.md D11) are deployed by the Kubernetes manifests; on ECS they would be two more services from the same image with a different command, plus ElastiCache for Redis. A shared environment also needs a remote state backend with locking.

---

# Terraform: despliegue en AWS ECS

[English](#terraform-aws-ecs-deployment) | **Español**

**Estado: validado, no aplicado.** `terraform fmt`, `init` y `validate` pasan y la imagen se prueba de extremo a extremo en la CI, pero esta configuración no se ha aplicado en una cuenta real de AWS. Nada cuesta dinero hasta que ejecutes `terraform apply`. Justificación del diseño: [DECISIONS.md D20](../DECISIONS.md#d20-1).

## Qué despliega

ECS Fargate (una tarea) con la API detrás de un Application Load Balancer, en una VPC mínima dedicada (dos subredes públicas, sin NAT gateway), con un repositorio ECR, logs de CloudWatch con retención explícita y un rol de ejecución sin rol de tarea (la app no llama a ninguna API de AWS). El almacén de hechos, los pasajes de los informes y los precios van dentro de la imagen. Se ejecuta con `MOCK_LLM=1` por defecto: coste de LLM cero, sin clave de API.

## Puesta en marcha

```bash
cd terraform
terraform init
terraform apply                       # revisa el plan antes de escribir yes

cd ..
docker buildx build --platform linux/amd64 -t filings-copilot:latest .
REPO_URL=$(cd terraform && terraform output -raw ecr_repository_url)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$REPO_URL"
docker tag filings-copilot:latest "$REPO_URL:latest" && docker push "$REPO_URL:latest"
cd terraform && terraform apply -replace=aws_ecs_service.app

curl "$(terraform output -raw alb_dns_name)/health"
```

Para eliminarlo todo, `terraform destroy` (el repositorio ECR tiene `force_delete = true`, así que funciona aunque queden imágenes).

## Coste (us-east-1, bajo demanda)

| Componente | Sesión de ~3 horas | Mes, si se deja encendido |
|---|---|---|
| Fargate 0,5 vCPU / 1 GB | ~0,07 $ | ~18 $ |
| ALB | ~0,07 $ | ~16-20 $ |
| Almacenamiento ECR, logs de CloudWatch | ~0,00 $ | ~0,10-3 $ |
| Secrets Manager (si `enable_openai_secret = true`) | ~0,00 $ | 0,40 $ |
| **Total** | **~0,15-0,30 $** | **~35-58 $** |

Prescindir del NAT gateway mantiene bajo el coste máximo si se olvida destruirlo; por sí solo añadiría unos 33 $ al mes.

## Modo con LLM real

Dos ajustes independientes en `terraform.tfvars` (copia `terraform.tfvars.example`), separados a propósito para que crear el secreto nunca empiece a gastar por sí solo: `enable_openai_secret = true` (y después guarda una clave en el secreto creado) y `mock_llm = "0"`.

## Lo que no cubre

Los *workers* de ingesta de EDGAR (DECISIONS.md D11) se despliegan con los manifiestos de Kubernetes; en ECS serían dos servicios más con la misma imagen y otro comando, además de ElastiCache para Redis. Un entorno compartido necesita también un *backend* de estado remoto con bloqueo.
