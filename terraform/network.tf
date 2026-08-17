# Minimal VPC for the Copilot's Fargate deployment: 2 public subnets across
# 2 AZs, an Internet Gateway, no NAT gateway — Fargate tasks get public IPs
# directly (see security_groups.tf's task SG egress, ecs.tf's
# assign_public_ip) and reach ECR/CloudWatch over the IGW. A real VPC
# rather than the account's default one is deliberate here — see
# DECISIONS.md D33.

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.project_name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${var.project_name}-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.${count.index}.0/24"
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${var.project_name}-public-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.project_name}-public-rt" }
}

# Without this association, the public subnets silently stay on the VPC's
# main route table (no route to the IGW) and Fargate tasks have no path to
# the internet at all — a failure mode terraform validate can't catch,
# since it's live routing behavior, not a syntax error. See DECISIONS.md D33.
resource "aws_route_table_association" "public" {
  # Derived from aws_subnet.public's own count, not a second independent
  # "2" literal — widening to a 3rd AZ only requires changing the subnet
  # resource; a hardcoded count here could silently mismatch and leave a
  # subnet unassociated to the IGW route, which terraform validate can't
  # catch either.
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}
