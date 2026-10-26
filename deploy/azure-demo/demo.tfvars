# Short-lived, synthetic-data Azure demo. Overrides the hospital-style defaults in deploy/terraform/azure/variables.tf.
# No identifiers here: demo.sh adds your public IP and Entra object id from .data/demo-local.tfvars (never committed).
name        = "cityforward"
location    = "eastus2"
environment = "demo"

# Control plane: public API restricted to one /32 (Terraform gets it from demo-local.tfvars), Free tier, no zones.
private_cluster           = false
admin_group_object_ids    = []
aks_sku_tier              = "Free"
aks_zones                 = []
aks_os_disk_type          = "Managed"
enable_defender           = false
enable_azure_policy       = false # needs Microsoft.PolicyInsights registered; skipped for the demo
enable_container_insights = false
# This subscription cannot use the v5/v6 or B-v2 families in eastus2; D-v7 (AMD D2as_v7, about 0.09 USD per hour) is available with 10 vCPU of quota.
node_vm_size = "Standard_D2as_v7"
node_count   = 2

postgres_sku                   = "GP_Standard_D2ds_v5"
postgres_ha                    = false
postgres_storage_mb            = 32768
postgres_backup_retention_days = 7
postgres_geo_redundant_backup  = false

acr_sku                   = "Basic"
acr_public_network_access = true # Basic has no private endpoint; `az acr build` needs the public endpoint

key_vault_sku                   = "standard"
key_vault_purge_protection      = false # so teardown leaves no reserved name
key_vault_soft_delete_days      = 7
key_vault_public_network_access = true

enable_files_storage = false
log_retention_days   = 30

tags = {
  purpose             = "cf-demo"
  owner               = "yashpatil"
  data-classification = "synthetic"
}
