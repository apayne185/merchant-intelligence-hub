# Task EXECUTION role — used by ECS itself to launch the task (pull the
# image from ECR, start the container, wire up CloudWatch logging, resolve
# any Secrets Manager references). Distinct from a task ROLE, which is what
# the running application's own AWS SDK calls would use — there is
# deliberately no task role in this deployment: confirmed the app makes
# zero AWS API calls at runtime under MOCK_LLM=1 (src/copilot/retrieval_core.py's
# OpenAIEmbedder/real OpenAI client only instantiate when is_mock_mode() is
# false), and Fargate imposes no minimum IAM permission floor on the task
# role — an empty or omitted one is correct here, not a shortcut. See
# DECISIONS.md D33. Do not attach AmazonECSTaskExecutionRolePolicy to a
# task role "just in case" — that over-privileges it for nothing.

resource "aws_iam_role" "execution" {
  name = local.execution_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })

  tags = { Name = local.execution_role_name }
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Optional: an empty-by-default secret for OPENAI_API_KEY, so real (non-mock)
# mode can be demoed later by populating it and forcing a new deployment,
# without touching Terraform again. recovery_window_in_days = 0 is
# deliberate: the default 30-day recovery window means the secret (and its
# ~$0.40/month) would otherwise outlive `terraform destroy`. See
# DECISIONS.md D33.
resource "aws_secretsmanager_secret" "openai_api_key" {
  count                   = var.enable_openai_secret ? 1 : 0
  name                    = local.openai_secret_name
  recovery_window_in_days = 0

  tags = { Name = local.openai_secret_name }
}

# AmazonECSTaskExecutionRolePolicy does NOT include secretsmanager:GetSecretValue
# — the secret is resolved by the execution role before the container ever
# starts (task definition `secrets` block), so this has to be an explicit
# addition, not assumed to already be covered. See DECISIONS.md D33.
resource "aws_iam_role_policy" "execution_secrets" {
  count = var.enable_openai_secret ? 1 : 0
  name  = "${var.project_name}-secrets-access"
  role  = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.openai_api_key[0].arn]
    }]
  })
}
