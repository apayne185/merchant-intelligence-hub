resource "aws_ecs_cluster" "main" {
  name = local.cluster_name

  tags = { Name = local.cluster_name }
}

# Declared explicitly with retention_in_days set — otherwise ECS
# auto-creates this on first log write with infinite retention, and
# because Terraform never created it, `terraform destroy` never deletes it
# either: a quiet, permanent violation of "nothing persists between
# demos." See DECISIONS.md D33.
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.project_name}"
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project_name}-logs" }
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.project_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.fargate_cpu
  memory                   = var.fargate_memory
  execution_role_arn       = aws_iam_role.execution.arn
  # No task_role_arn — deliberate, see iam.tf: the app makes zero AWS SDK
  # calls at runtime under MOCK_LLM=1, and Fargate has no minimum
  # permission floor on the task role.

  # Match whatever architecture the image was actually built/pushed for
  # (docker buildx build --platform linux/amd64, per the Dockerfile/README)
  # rather than relying on defaults agreeing. See DECISIONS.md D33.
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = var.project_name
      image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
      essential = true
      portMappings = [{
        containerPort = var.container_port
        protocol      = "tcp"
      }]
      environment = [
        # var.mock_llm, not a hardcoded literal — controllable via tfvars
        # without hand-editing this file. Defaults to "1": zero-cost,
        # deterministic mock mode (DECISIONS.md D22), same pattern used
        # everywhere else in this repo.
        { name = "MOCK_LLM", value = var.mock_llm },
      ]
      secrets = var.enable_openai_secret ? [
        { name = "OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.openai_api_key[0].arn },
      ] : []
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = { Name = var.project_name }
}

resource "aws_ecs_service" "app" {
  name            = var.project_name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # Gives the task time to finish its (heavy) import chain — pandas,
  # sklearn, shap, lightgbm, duckdb, langgraph, agno all import eagerly at
  # module load (src/copilot/graph.py imports every tool at the top of the
  # file specifically so this cost lands here, at startup, not on
  # whichever live request is first to route to a given node) — before
  # ALB health-check failures start counting against it. See DECISIONS.md D33.
  health_check_grace_period_seconds = 60

  network_configuration {
    subnets         = aws_subnet.public[*].id
    security_groups = [aws_security_group.task.id]
    # Not the Terraform default (false) — with no NAT gateway, omitting
    # this leaves the task with no path to the internet at all, failing
    # silently (CannotPullContainerError / timeout) in a way that doesn't
    # obviously point back to this one flag. See DECISIONS.md D33.
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = var.project_name
    container_port   = var.container_port
  }

  # AWS's own documented gotcha: a service can fail to register targets if
  # it comes up before the listener exists to route to them, even though
  # the target group itself already exists at that point.
  depends_on = [aws_lb_listener.http]

  tags = { Name = var.project_name }
}
