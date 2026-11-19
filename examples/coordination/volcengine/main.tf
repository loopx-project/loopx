# ==============================================================================
# LoopX Volcengine cn-beijing Coordination & Gateway Infrastructure Template
#
# WARNING: STRICT HUMAN CONFIRMATION REQUIRED
# Do NOT run `terraform apply` automatically without explicit user authorization.
# ==============================================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    volcengine = {
      source  = "volcengine/volcengine"
      version = ">= 0.0.186, < 0.1.0"
    }
  }
}

variable "region" {
  type        = string
  default     = "cn-beijing"
  description = "Volcengine region for coordination infrastructure"
}

variable "availability_zone" {
  type        = string
  default     = "cn-beijing-a"
  description = "Primary Volcengine availability zone"
}

variable "secondary_availability_zone" {
  type        = string
  default     = ""
  description = "Secondary Volcengine availability zone for RDS PostgreSQL (defaults to availability_zone)"
}

variable "vpc_cidr" {
  type        = string
  default     = "172.16.0.0/16"
  description = "VPC CIDR block"
}

variable "subnet_cidr" {
  type        = string
  default     = "172.16.1.0/24"
  description = "Subnet CIDR block for coordination services"
}

variable "db_node_spec" {
  type        = string
  default     = "rds.postgres.1c2g"
  description = "RDS for PostgreSQL 16 node specification (1C 2GB)"
}

variable "db_storage_gb" {
  type        = number
  default     = 20
  description = "Allocated storage size in GB (range [20, 3000], step 10)"
  validation {
    condition     = var.db_storage_gb >= 20 && var.db_storage_gb <= 3000 && var.db_storage_gb % 10 == 0
    error_message = "db_storage_gb must be between 20 and 3000 GB in steps of 10."
  }
}

variable "operator_allowlist_ips" {
  type        = list(string)
  default     = []
  description = "List of authorized operator IP CIDRs allowed to connect to Gateway port 443"
}

provider "volcengine" {
  region = var.region
}

# 1. Virtual Private Cloud (VPC)
resource "volcengine_vpc" "coordination_vpc" {
  vpc_name   = "loopx-coordination-vpc-beijing"
  cidr_block = var.vpc_cidr
  lifecycle {
    prevent_destroy = true
  }
}

# 2. Isolated Subnet
resource "volcengine_subnet" "coordination_subnet" {
  subnet_name = "loopx-coordination-subnet-a"
  vpc_id      = volcengine_vpc.coordination_vpc.id
  cidr_block  = var.subnet_cidr
  zone_id     = var.availability_zone
  lifecycle {
    prevent_destroy = true
  }
}

# 3. Security Group for Remote Authority Gateway (ECS)
resource "volcengine_security_group" "gateway_sg" {
  security_group_name = "loopx-gateway-sg"
  vpc_id              = volcengine_vpc.coordination_vpc.id
  description         = "Security group for LoopX Remote Authority TLS Gateway"
}

# Ingress: Allow TLS 443 from authorized operator CIDRs only
resource "volcengine_security_group_rule" "gateway_ingress_https" {
  count             = length(var.operator_allowlist_ips) > 0 ? length(var.operator_allowlist_ips) : 1
  security_group_id = volcengine_security_group.gateway_sg.id
  direction         = "ingress"
  protocol          = "tcp"
  port_start        = 443
  port_end          = 443
  cidr_ip           = length(var.operator_allowlist_ips) > 0 ? var.operator_allowlist_ips[count.index] : var.vpc_cidr
  description       = "Allow inbound TLS 443"
}

# 4. Security Group for RDS for PostgreSQL (Strict VPC internal only)
resource "volcengine_security_group" "rds_sg" {
  security_group_name = "loopx-rds-internal-sg"
  vpc_id              = volcengine_vpc.coordination_vpc.id
  description         = "Strict internal security group for RDS PostgreSQL (No public ingress)"
}

# Ingress: Port 5432 strictly from gateway security group
resource "volcengine_security_group_rule" "rds_ingress_from_gateway" {
  security_group_id        = volcengine_security_group.rds_sg.id
  direction                = "ingress"
  protocol                 = "tcp"
  port_start               = 5432
  port_end                 = 5432
  source_group_id          = volcengine_security_group.gateway_sg.id
  description              = "Allow PostgreSQL access from gateway only"
}

# 5. RDS for PostgreSQL 16 Instance
resource "volcengine_rds_postgresql_instance" "coordination_db" {
  db_engine_version = "PostgreSQL_16"
  instance_name     = "loopx-coordination-rds-pg16"
  node_spec         = var.db_node_spec
  primary_zone_id   = var.availability_zone
  secondary_zone_id = var.secondary_availability_zone != "" ? var.secondary_availability_zone : var.availability_zone
  storage_space     = var.db_storage_gb
  subnet_id         = volcengine_subnet.coordination_subnet.id

  charge_info {
    charge_type = "PostPaid" # Pay-as-you-go
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [charge_info]
  }
}

output "vpc_id" {
  value       = volcengine_vpc.coordination_vpc.id
  description = "Coordination VPC ID"
}

output "subnet_id" {
  value       = volcengine_subnet.coordination_subnet.id
  description = "Coordination Subnet ID"
}

output "rds_endpoints" {
  value       = volcengine_rds_postgresql_instance.coordination_db.endpoints
  description = "PostgreSQL endpoint records exported by the Volcengine RDS instance"
}

output "rds_endpoint" {
  value       = try(volcengine_rds_postgresql_instance.coordination_db.endpoints[0].address[0].domain, "")
  description = "Primary internal PostgreSQL domain endpoint for RemoteAuthorityService connection"
}
