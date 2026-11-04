# Two public subnets, no NAT gateway and no VPC endpoints: the task gets a
# public IP and reaches Bedrock, ECR, Secrets Manager, GitHub, Qdrant, Groq and
# LangSmith over the internet gateway. Saves the ~$1/day a NAT gateway costs.

#trivy:ignore:AVD-AWS-0178 No VPC flow logs: a one-task portfolio stack, and inbound traffic is CloudFront-only
resource "aws_vpc" "this" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "cra-vpc" }
}

locals {
  public_subnets = {
    "1a" = { cidr = "10.0.0.0/20", name = "cra-subnet-public1-${var.region}a" }
    "1b" = { cidr = "10.0.16.0/20", name = "cra-subnet-public2-${var.region}b" }
  }
}

resource "aws_subnet" "public" {
  for_each = local.public_subnets

  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value.cidr
  availability_zone       = "${var.region}${substr(each.key, 1, 1)}"
  map_public_ip_on_launch = false # the ECS service assigns the task's public IP itself

  tags = { Name = each.value.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "cra-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "cra-rtb-public" }
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}
