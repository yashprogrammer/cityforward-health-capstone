terraform {
  required_version = ">= 1.6.0"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Remote state in a storage account with versioning and a private endpoint (state contains generated passwords):
  # backend "azurerm" { resource_group_name = "tfstate-rg" storage_account_name = "cftfstate" container_name = "tfstate" key = "cityforward.tfstate" }
}

provider "azurerm" {
  features {
    key_vault { purge_soft_delete_on_destroy = false }
  }
}
