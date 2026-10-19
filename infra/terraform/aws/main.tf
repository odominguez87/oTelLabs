# ════════════════════════════════════════════════════════════════════════════
# AWS — Arquitectura observable (Lab 4). Validado con `terraform validate`;
# NO aplicado (sin costos). Equivalencias con el entorno local en README.md.
# ════════════════════════════════════════════════════════════════════════════
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { app = "otel-lab", env = "dev", owner = "grupo-7" }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}
variable "alert_email" {
  type    = string
  default = "sre-oncall@example.com"
}
variable "db_password" {
  type      = string
  sensitive = true
  default   = "change-me-in-secrets-manager"
}

data "aws_availability_zones" "available" { state = "available" }
data "aws_caller_identity" "current" {}

resource "aws_sns_topic" "oncall" {
  name = "otel-lab-oncall"
}

resource "aws_sns_topic_subscription" "oncall_email" {
  topic_arn = aws_sns_topic.oncall.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
