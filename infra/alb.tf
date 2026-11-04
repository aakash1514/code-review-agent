# Public ALB, but its security group admits only CloudFront (security_groups.tf)
# and the app rejects requests without the X-Origin-Verify header.

#trivy:ignore:AVD-AWS-0053 Must be internet-facing for CloudFront to reach it (no VPC origin); the SG admits only CloudFront's prefix list
resource "aws_lb" "this" {
  name               = "cra-alb"
  internal           = false
  load_balancer_type = "application"
  ip_address_type    = "ipv4"
  security_groups    = [aws_security_group.alb.id]
  subnets            = [for s in aws_subnet.public : s.id]

  # WebSockets and long reviews: keep idle connections open for 5 minutes.
  idle_timeout               = 300
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "app" {
  name                 = "cra-tg"
  target_type          = "ip"
  protocol             = "HTTP"
  protocol_version     = "HTTP1"
  port                 = 8000
  ip_address_type      = "ipv4"
  vpc_id               = aws_vpc.this.id
  deregistration_delay = 30

  health_check {
    enabled             = true
    protocol            = "HTTP"
    port                = "traffic-port"
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# Plain HTTP: CloudFront terminates TLS for viewers and talks to the ALB on
# port 80, since there is no custom domain to put an ACM certificate on.
#trivy:ignore:AVD-AWS-0054 CloudFront -> ALB over HTTP by design (no custom domain/certificate); viewers get HTTPS from CloudFront, and the ALB admits only CloudFront
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
