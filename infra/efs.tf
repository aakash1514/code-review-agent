# /data in the container: the SQLite review history and knowledge_graph_*.json.
# No backups: destroying the stack deletes this data (see README.md).

resource "aws_efs_file_system" "data" {
  creation_token   = "cra-efs"
  encrypted        = true # AWS-managed key (aws/elasticfilesystem)
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"

  tags = { Name = "cra-efs" }
}

resource "aws_efs_backup_policy" "data" {
  file_system_id = aws_efs_file_system.data.id

  backup_policy {
    status = "DISABLED"
  }
}

resource "aws_efs_mount_target" "data" {
  for_each = aws_subnet.public

  file_system_id  = aws_efs_file_system.data.id
  subnet_id       = each.value.id
  security_groups = [aws_security_group.efs.id]
}

# The task mounts through this access point, so it sees /data as its root and
# writes as uid/gid 1000 (the image's non-root "app" user).
resource "aws_efs_access_point" "data" {
  file_system_id = aws_efs_file_system.data.id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/data"

    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0755"
    }
  }

  tags = { Name = "cra-data-ap" }
}

# Same two statements as the Console's policy wizard: mount only through a
# mount target, and refuse any connection without TLS.
resource "aws_efs_file_system_policy" "data" {
  file_system_id = aws_efs_file_system.data.id
  policy         = data.aws_iam_policy_document.efs.json
}

data "aws_iam_policy_document" "efs" {
  statement {
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions = [
      "elasticfilesystem:ClientRootAccess",
      "elasticfilesystem:ClientWrite",
      "elasticfilesystem:ClientMount",
    ]
    resources = [aws_efs_file_system.data.arn]
    condition {
      test     = "Bool"
      variable = "elasticfilesystem:AccessedViaMountTarget"
      values   = ["true"]
    }
  }

  statement {
    effect = "Deny"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions   = ["*"]
    resources = [aws_efs_file_system.data.arn]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}
