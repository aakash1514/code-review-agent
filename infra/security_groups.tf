# Internet -> CloudFront -> ALB:80 -> task:8000 -> EFS:2049. Each hop accepts
# only the security group (or prefix list) in front of it.

# AWS-managed list of CloudFront's origin-facing IP ranges. Looked up by name,
# because the pl-... ID differs per region.
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name        = "cra-alb-sg"
  description = "cra-alb-sg"
  vpc_id      = aws_vpc.this.id
}

resource "aws_security_group" "task" {
  name        = "cra-task-sg"
  description = "cra-task-sg"
  vpc_id      = aws_vpc.this.id
}

resource "aws_security_group" "efs" {
  name        = "cra-efs-sg"
  description = "cra-efs-sg"
  vpc_id      = aws_vpc.this.id
}

# ---- ingress --------------------------------------------------------------

# Only CloudFront can reach the ALB. The app also checks X-Origin-Verify, so
# another CloudFront distribution pointed at this ALB still gets a 403.
resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from CloudFront origin-facing ranges"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
}

resource "aws_vpc_security_group_ingress_rule" "task_from_alb" {
  security_group_id            = aws_security_group.task.id
  description                  = "App port from the ALB"
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_task" {
  security_group_id            = aws_security_group.efs.id
  description                  = "NFS from the ECS task"
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
  referenced_security_group_id = aws_security_group.task.id
}

# ---- egress: all open, as in the Console stack ----------------------------

#trivy:ignore:AVD-AWS-0104 The task calls Bedrock, ECR, Secrets Manager, GitHub, Qdrant, Groq and LangSmith over the internet (no NAT, no endpoints); their IPs aren't fixed
resource "aws_vpc_security_group_egress_rule" "task_all" {
  security_group_id = aws_security_group.task.id
  description       = "All outbound (AWS APIs and SaaS over the IGW)"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

#trivy:ignore:AVD-AWS-0104 Same egress as the live Console SG; the ALB only forwards to tasks in this VPC
resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  description       = "All outbound (forwards to the task)"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

#trivy:ignore:AVD-AWS-0104 Same egress as the live Console SG; EFS never opens outbound connections
resource "aws_vpc_security_group_egress_rule" "efs_all" {
  security_group_id = aws_security_group.efs.id
  description       = "All outbound (unused by EFS)"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
