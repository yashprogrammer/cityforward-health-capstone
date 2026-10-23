# The hospital-style landing zone for CityForward: private network, private cluster, managed PostgreSQL with pgvector, Key Vault, private storage for the
# document hot folder, centralized logging. Everything that holds PHI is private-endpoint-only and encrypted at rest.

data "azurerm_client_config" "current" {}

locals {
  prefix = "${var.name}-${var.environment}"
  tags   = merge({ project = "cityforward", environment = var.environment, data-classification = "phi" }, var.tags)
}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.prefix}"
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------- network
resource "azurerm_virtual_network" "main" {
  name                = "vnet-${local.prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = ["10.20.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "aks" {
  name                 = "snet-aks"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.20.0.0/22"]
}

resource "azurerm_subnet" "data" {
  name                 = "snet-data"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.20.8.0/24"]
  delegation {
    name = "postgres"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "endpoints" {
  name                 = "snet-private-endpoints"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.20.9.0/24"]
}

resource "azurerm_network_security_group" "data" {
  name                = "nsg-data-${local.prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags

  security_rule {
    name                       = "allow-aks-to-postgres"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "5432"
    source_address_prefix      = "10.20.0.0/22"
    destination_address_prefix = "*"
  }
  security_rule {
    name                       = "deny-everything-else-inbound"
    priority                   = 4000
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "data" {
  subnet_id                 = azurerm_subnet.data.id
  network_security_group_id = azurerm_network_security_group.data.id
}

resource "azurerm_private_dns_zone" "postgres" {
  name                = "${local.prefix}.private.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "postgres-link"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.main.id
}

# ---------------------------------------------------------------- observability & registry
resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_container_registry" "main" {
  name                          = "${var.name}acr${random_string.suffix.result}"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  sku                           = "Premium" # private endpoints, content trust, geo-replication
  admin_enabled                 = false
  public_network_access_enabled = false
  tags                          = local.tags
}

# ---------------------------------------------------------------- Key Vault (secrets reach pods through the CSI driver, never through git)
resource "azurerm_key_vault" "main" {
  name                          = "kv-${var.name}-${random_string.suffix.result}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "premium" # HSM-backed keys
  enable_rbac_authorization     = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 90
  public_network_access_enabled = false
  tags                          = local.tags
}

resource "azurerm_role_assignment" "deployer_kv" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "random_password" "postgres_admin" {
  length  = 32
  special = false
}

resource "random_password" "copilot_db" {
  length  = 32
  special = false
}

resource "random_password" "meditrack_db" {
  length  = 32
  special = false
}

# ---------------------------------------------------------------- PostgreSQL flexible server (pgvector), private, TLS only
resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "psql-${local.prefix}-${random_string.suffix.result}"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "16"
  sku_name                      = var.postgres_sku
  storage_mb                    = 131072
  delegated_subnet_id           = azurerm_subnet.data.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false
  administrator_login           = "cfadmin"
  administrator_password        = random_password.postgres_admin.result
  backup_retention_days         = 35
  geo_redundant_backup_enabled  = true
  tags                          = local.tags

  dynamic "high_availability" {
    for_each = var.postgres_ha ? [1] : []
    content {
      mode = "ZoneRedundant"
    }
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
  lifecycle {
    ignore_changes = [zone, high_availability[0].standby_availability_zone]
  }
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "VECTOR,PGAUDIT"
}

# pgAudit: database-level audit of reads, writes and DDL, independent of the application's own audit log (HITRUST 09.aa). Needs a server restart to load.
resource "azurerm_postgresql_flexible_server_configuration" "pgaudit_preload" {
  name      = "shared_preload_libraries"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "pgaudit"
}

resource "azurerm_postgresql_flexible_server_configuration" "pgaudit_log" {
  name       = "pgaudit.log"
  server_id  = azurerm_postgresql_flexible_server.main.id
  value      = "read,write,ddl,role"
  depends_on = [azurerm_postgresql_flexible_server_configuration.pgaudit_preload]
}

resource "azurerm_postgresql_flexible_server_configuration" "require_tls" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "on"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_connections" {
  name      = "log_connections"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "on"
}

resource "azurerm_postgresql_flexible_server_database" "meditrack" {
  name      = "meditrack"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_database" "copilot" {
  name      = "copilot"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# The application roles (meditrack, copilot) are created by deploy/terraform/azure/scripts/db-roles.sql from inside the VNet, so the Terraform runner needs no database network path.

# ---------------------------------------------------------------- hot folder storage (Azure Files over a private endpoint)
resource "azurerm_storage_account" "files" {
  name                              = "${var.name}files${random_string.suffix.result}"
  resource_group_name               = azurerm_resource_group.main.name
  location                          = azurerm_resource_group.main.location
  account_tier                      = "Premium"
  account_kind                      = "FileStorage"
  account_replication_type          = "ZRS"
  min_tls_version                   = "TLS1_2"
  public_network_access_enabled     = false
  allow_nested_items_to_be_public   = false
  infrastructure_encryption_enabled = true
  tags                              = local.tags
}

resource "azurerm_storage_share" "hotfolder" {
  name               = "meditrack-data"
  storage_account_id = azurerm_storage_account.files.id
  quota              = 100
  enabled_protocol   = "SMB"
}

# ---------------------------------------------------------------- AKS
resource "azurerm_user_assigned_identity" "aks" {
  name                = "id-aks-${local.prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_kubernetes_cluster" "main" {
  name                              = "aks-${local.prefix}"
  location                          = azurerm_resource_group.main.location
  resource_group_name               = azurerm_resource_group.main.name
  dns_prefix                        = local.prefix
  kubernetes_version                = null # latest supported; pin in production and upgrade on a schedule
  private_cluster_enabled           = var.private_cluster
  sku_tier                          = "Standard"
  oidc_issuer_enabled               = true
  workload_identity_enabled         = true
  azure_policy_enabled              = true
  local_account_disabled            = true
  role_based_access_control_enabled = true
  automatic_upgrade_channel         = "patch"
  tags                              = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.aks.id]
  }

  azure_active_directory_role_based_access_control {
    azure_rbac_enabled     = true
    admin_group_object_ids = var.admin_group_object_ids
  }

  default_node_pool {
    name                         = "system"
    vm_size                      = var.node_vm_size
    node_count                   = var.node_count
    vnet_subnet_id               = azurerm_subnet.aks.id
    zones                        = ["1", "2", "3"]
    only_critical_addons_enabled = false
    os_disk_type                 = "Ephemeral"
    temporary_name_for_rotation  = "tmp"
    upgrade_settings {
      max_surge = "33%"
    }
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_policy      = "cilium"
    network_data_plane  = "cilium"
    load_balancer_sku   = "standard"
    outbound_type       = "loadBalancer"
  }

  dynamic "api_server_access_profile" {
    for_each = var.private_cluster ? [] : [1]
    content {
      authorized_ip_ranges = var.authorized_ip_ranges
    }
  }

  key_vault_secrets_provider {
    secret_rotation_enabled = true
  }

  microsoft_defender {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  }

  oms_agent {
    log_analytics_workspace_id      = azurerm_log_analytics_workspace.main.id
    msi_auth_for_monitoring_enabled = true
  }
}

resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
}

# The Copilot's pods read their secrets from Key Vault through a federated workload identity (no static credentials in the cluster).
resource "azurerm_user_assigned_identity" "copilot" {
  name                = "id-copilot-${local.prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "copilot" {
  name                = "copilot"
  resource_group_name = azurerm_resource_group.main.name
  parent_id           = azurerm_user_assigned_identity.copilot.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject             = "system:serviceaccount:cityforward:copilot"
}

resource "azurerm_role_assignment" "copilot_kv_reader" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.copilot.principal_id
}

# ---------------------------------------------------------------- secrets the application chart expects (synced to the cityforward-db Secret by the CSI driver)
resource "azurerm_key_vault_secret" "meditrack_url" {
  name         = "meditrack-url"
  key_vault_id = azurerm_key_vault.main.id
  value        = "postgresql://meditrack:${random_password.meditrack_db.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/meditrack?sslmode=require"
  depends_on   = [azurerm_role_assignment.deployer_kv]
}

resource "azurerm_key_vault_secret" "copilot_url" {
  name         = "copilot-url"
  key_vault_id = azurerm_key_vault.main.id
  value        = "postgresql://copilot:${random_password.copilot_db.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/copilot?sslmode=require"
  depends_on   = [azurerm_role_assignment.deployer_kv]
}

# ---------------------------------------------------------------- audit trail for the platform itself
resource "azurerm_monitor_diagnostic_setting" "keyvault" {
  name                       = "to-log-analytics"
  target_resource_id         = azurerm_key_vault.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  enabled_log {
    category = "AuditEvent"
  }
}

resource "azurerm_monitor_diagnostic_setting" "postgres" {
  name                       = "to-log-analytics"
  target_resource_id         = azurerm_postgresql_flexible_server.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  enabled_log {
    category = "PostgreSQLLogs"
  }
}
