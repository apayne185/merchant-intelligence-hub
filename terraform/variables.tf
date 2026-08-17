variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name prefix for all resources (ECR repo, ECS cluster/service, ALB, security groups, log group)."
  type        = string
  default     = "merchant-copilot"
}

variable "container_port" {
  description = "Port the Copilot API listens on inside the container — matches the Dockerfile's CMD and src/copilot/api.py."
  type        = number
  default     = 8001
}

variable "image_tag" {
  description = "Tag of the image in ECR to deploy. See terraform/README.md: push an image with this tag before the first `apply` that creates the ECS service, or the task will fail to pull."
  type        = string
  default     = "latest"
}

variable "fargate_cpu" {
  description = "Fargate task vCPU units (256=.25vCPU, 512=.5vCPU, 1024=1vCPU, ...). Must be a valid AWS Fargate CPU/memory pairing with fargate_memory."
  type        = string
  default     = "512"
}

variable "fargate_memory" {
  description = "Fargate task memory in MiB. Must be a valid AWS Fargate CPU/memory pairing with fargate_cpu."
  type        = string
  default     = "1024"
}

variable "desired_count" {
  description = "Number of Fargate tasks to run. 1 is enough for a demo — this isn't meant to be highly available, see DECISIONS.md D33."
  type        = number
  default     = 1
}

variable "log_retention_days" {
  description = "CloudWatch log group retention. Declared explicitly (not left to ECS's auto-created default of infinite retention) so `terraform destroy` actually removes it — see DECISIONS.md D33."
  type        = number
  default     = 7
}

variable "mock_llm" {
  description = "Value for the container's MOCK_LLM env var. \"1\" (default) = zero-cost, deterministic mock mode (DECISIONS.md D22) — same pattern used everywhere else in this repo. Set to \"0\" only alongside enable_openai_secret=true, once the secret is actually populated with a real key, to demo real (non-mock) mode. The two are independent flags: enabling the secret does NOT switch this on its own — that's intentional, so applying a secret never silently starts spending on OpenAI calls."
  type        = string
  default     = "1"
}

variable "enable_openai_secret" {
  description = "If true, provisions an empty-by-default Secrets Manager secret for OPENAI_API_KEY, wired into the task definition, so real (non-mock) mode can be demoed later by populating it and forcing a new deployment, without touching Terraform again. The app runs with MOCK_LLM=1 regardless of this flag — see DECISIONS.md D33 for the cost/recovery-window tradeoff."
  type        = bool
  default     = false
}
