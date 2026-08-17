resource "aws_lb" "app" {
  name               = local.alb_name
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  tags = { Name = local.alb_name }
}

resource "aws_lb_target_group" "app" {
  name     = local.target_group_name
  port     = var.container_port
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id
  # Fargate's awsvpc mode registers targets by ENI private IP, not
  # instance id — target_type defaults to "instance", which breaks target
  # registration outright if left unset. The single most common
  # ECS+Fargate+ALB Terraform mistake. See DECISIONS.md D33.
  target_type = "ip"

  health_check {
    path     = "/health"
    protocol = "HTTP"
    matcher  = "200"
    # Tuned for "apply, then immediately demo" rather than the 30s-interval/
    # 5-healthy-check default (which would take ~2.5 minutes after the task
    # reaches RUNNING before the target flips healthy). See DECISIONS.md D33.
    interval            = 15
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = { Name = local.target_group_name }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  # HTTP only, no ACM/TLS cert — there's no custom domain for this
  # portfolio deployment to validate a cert against. A deliberate
  # tradeoff, not an oversight. See DECISIONS.md D33.
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
