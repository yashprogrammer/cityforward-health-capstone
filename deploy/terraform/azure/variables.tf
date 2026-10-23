variable "name" {
  description = "Short name used in resource names (lowercase letters and digits)."
  type        = string
  default     = "cityforward"
}

variable "location" {
  description = "Azure region. Pick the region your data-residency policy allows."
  type        = string
  default     = "centralindia"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "private_cluster" {
  description = "Keep the Kubernetes API private (reachable only from the VNet/VPN). The hospital-style default."
  type        = bool
  default     = true
}

variable "admin_group_object_ids" {
  description = "Entra ID groups that administer the cluster (Azure RBAC for Kubernetes)."
  type        = list(string)
}

variable "authorized_ip_ranges" {
  description = "Only used when private_cluster is false: CIDRs allowed to reach the API server."
  type        = list(string)
  default     = []
}

variable "node_count" {
  type    = number
  default = 3
}

variable "node_vm_size" {
  type    = string
  default = "Standard_D4s_v5"
}

variable "postgres_sku" {
  type    = string
  default = "GP_Standard_D2ds_v5"
}

variable "postgres_ha" {
  description = "Zone-redundant high availability for PostgreSQL."
  type        = bool
  default     = true
}

variable "customer_managed_key_id" {
  description = "Optional Key Vault key id for PostgreSQL and storage encryption at rest (HITRUST: customer-managed keys). Empty = Microsoft-managed keys."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "Log Analytics retention. HIPAA documentation retention is six years; archive to immutable storage for the long tail."
  type        = number
  default     = 365
}

variable "tags" {
  type    = map(string)
  default = {}
}
