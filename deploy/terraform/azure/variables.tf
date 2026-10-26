variable "name" {
  description = "Short name used in resource names (lowercase letters and digits)."
  type        = string
  default     = "cityforward"
}

variable "location" {
  description = "Azure region. Pick the region your data-residency policy allows."
  type        = string
  default     = "eastus2"
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

# ---------------------------------------------------------------- cost / posture switches. Every default is the hospital-style behavior above; deploy/azure-demo/demo.tfvars flips them for a short, cheap, synthetic-data demo.

variable "acr_sku" {
  type    = string
  default = "Premium"
}

variable "acr_public_network_access" {
  description = "Premium + private endpoint is the hospital default (false). Basic cannot be private, so the demo sets true."
  type        = bool
  default     = false
}

variable "key_vault_sku" {
  type    = string
  default = "premium"
}

variable "key_vault_purge_protection" {
  description = "Purge protection makes the vault name unrecoverable for the soft-delete window after destroy. Demo: false."
  type        = bool
  default     = true
}

variable "key_vault_soft_delete_days" {
  type    = number
  default = 90
}

variable "key_vault_public_network_access" {
  type    = bool
  default = false
}

variable "aks_sku_tier" {
  type    = string
  default = "Standard"
}

variable "aks_zones" {
  type    = list(string)
  default = ["1", "2", "3"]
}

variable "aks_os_disk_type" {
  description = "Ephemeral needs a VM size with a large enough cache disk."
  type        = string
  default     = "Ephemeral"
}

variable "enable_defender" {
  type    = bool
  default = true
}

variable "enable_container_insights" {
  type    = bool
  default = true
}

variable "enable_azure_policy" {
  type    = bool
  default = true
}

variable "cluster_admin_object_ids" {
  description = "Users or service principals granted 'Azure Kubernetes Service RBAC Cluster Admin' on the cluster (use when no Entra group is available)."
  type        = list(string)
  default     = []
}

variable "postgres_storage_mb" {
  type    = number
  default = 131072
}

variable "postgres_backup_retention_days" {
  type    = number
  default = 35
}

variable "postgres_geo_redundant_backup" {
  type    = bool
  default = true
}

variable "enable_files_storage" {
  description = "The premium Azure Files account for the hot folder. Demo: false (the chart falls back to a managed disk)."
  type        = bool
  default     = true
}
