# Security groups for the ALB and the ECS task.
#
# Bare aws_security_group resources here (no inline ingress/egress blocks)
# because the ALB SG and task SG reference each other — inline blocks
# referencing a sibling resource's id create a circular dependency
# Terraform can't resolve. Separate aws_vpc_security_group_*_rule resources
# (the current AWS-provider-recommended pattern, replacing the older
# aws_security_group_rule) avoid that.
#
# Both SGs need EXPLICIT egress: unlike a console-created security group,
# Terraform's aws_security_group default-denies all outbound once any
# ingress rule exists for it. Missing egress on the ALB SG breaks health
# checks (looks like a target-group problem, not an SG one); missing
# egress on the task SG breaks ECR/CloudWatch access entirely, since
# there's no NAT gateway as a fallback path. See DECISIONS.md D33.

resource "aws_security_group" "alb" {
  name        = "${var.project_name}-alb-sg"
  description = "Merchant Copilot ALB — inbound HTTP from the internet, outbound to the ECS task only."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.project_name}-alb-sg" }
}

resource "aws_security_group" "task" {
  name        = "${var.project_name}-task-sg"
  description = "Merchant Copilot ECS task — inbound from the ALB only, outbound HTTPS for ECR/CloudWatch."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.project_name}-task-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http_in" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from anywhere"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_task" {
  security_group_id            = aws_security_group.alb.id
  description                  = "To the ECS task on the app port"
  from_port                    = var.container_port
  to_port                      = var.container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.task.id
}

resource "aws_vpc_security_group_ingress_rule" "task_from_alb" {
  security_group_id            = aws_security_group.task.id
  description                  = "App port from the ALB only — never exposed directly to the internet"
  from_port                    = var.container_port
  to_port                      = var.container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_egress_rule" "task_https_out" {
  security_group_id = aws_security_group.task.id
  description       = "HTTPS out — ECR image pull + CloudWatch Logs. Must be direct: no NAT gateway (see network.tf)."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}
