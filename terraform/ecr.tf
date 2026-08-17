resource "aws_ecr_repository" "app" {
  name = var.project_name

  # Without this, `terraform destroy` fails partway through on a non-empty
  # repository — there will always be at least one pushed image — which
  # directly undermines the "clean teardown between demos" goal this whole
  # deployment is built around. Discovered only live against a real
  # account mid-teardown, not something `terraform validate` catches. See
  # DECISIONS.md D33.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = var.project_name }
}
