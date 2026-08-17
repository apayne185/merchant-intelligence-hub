# Single source of truth for every resource whose name/id and its own
# Name tag were previously two independent "${var.project_name}-X"
# literals — one per attribute, editable (and forgettable) separately.
# Resources with no separate `name` argument (VPC, IGW, route table, the
# CloudWatch log group's tag, ECR, the Secrets Manager IAM policy) aren't
# listed here — a local for a string used exactly once buys no DRY benefit.
locals {
  alb_name            = "${var.project_name}-alb"
  target_group_name   = "${var.project_name}-tg"
  cluster_name        = "${var.project_name}-cluster"
  alb_sg_name         = "${var.project_name}-alb-sg"
  task_sg_name        = "${var.project_name}-task-sg"
  execution_role_name = "${var.project_name}-execution-role"
  openai_secret_name  = "${var.project_name}-openai-api-key"
}
