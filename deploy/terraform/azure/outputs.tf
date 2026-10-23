output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "aks_name" {
  value = azurerm_kubernetes_cluster.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "copilot_identity_client_id" {
  description = "Client id for the Copilot's workload identity (annotate the ServiceAccount with azure.workload.identity/client-id)."
  value       = azurerm_user_assigned_identity.copilot.client_id
}

output "next_steps" {
  value = <<-EOT
    1. From a host inside the VNet run scripts/db-roles.sql as cfadmin (creates the meditrack and copilot roles, CREATE EXTENSION vector in the copilot database).
    2. Put the AI service's SMART signing key, the webhook secret, the payer API key and the model API key into Key Vault (see docs in deploy/README.md).
    3. az aks get-credentials --resource-group ${azurerm_resource_group.main.name} --name ${azurerm_kubernetes_cluster.main.name}
    4. kubectl apply -f deploy/argocd/project.yaml -f deploy/argocd/root-app.yaml
  EOT
}
