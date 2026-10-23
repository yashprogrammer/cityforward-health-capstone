# VPC / on-prem-style deployment

**Where:** tag `deploy`: `deploy/` (Helm chart, Argo CD apps, platform policies, Terraform, kind bootstrap) and `deploy/README.md`.

## Learning objectives
1. Describe a **hospital-style** network posture: private API server, no public ingress, private endpoints for data stores, default-deny network policy, an allow-listed model endpoint.
2. Package an application so a hospital's platform team accepts it: non-root, read-only filesystem, no API token, resource limits, signed images, mesh mTLS, secrets from a vault.
3. Use **GitOps** (Argo CD) so git is the change record and rollback is a revert.
4. Test infrastructure code *without* the infrastructure: render, schema-validate, assert security properties.

## Walk-through (60 minutes)
1. The diagram in `deploy/README.md`: follow a clinician's request and a webhook through every boundary.
2. `helm template` the chart for kind and AKS values; diff them. Why does production forbid in-cluster Postgres and `:latest`?
3. Read `test_chart.py`: each test is a promise to the security reviewer. Break one on purpose (remove `readOnlyRootFilesystem`) and watch it fail.
4. Kyverno: read the supply-chain policy. What does "signed by this workflow on this branch" give you that "pulled from our registry" does not?
5. Terraform: read the Postgres and Key Vault blocks. Where is the public network access? Where are the passwords?
6. Run `deploy/kind/bootstrap.sh` (with Docker and kind) and `helm test`.

## Discussion
* "On-prem-style": what changes when there is no cloud? (Replace managed services with operators; the chart does not care.)
* Where would you put the model if the hospital forbids external calls?
* What is the first thing that will go wrong in a real deployment, and which of our checks would *not* have caught it? (Be specific: the answer is in `deploy/README.md`'s "not run" column.)

## Pitfalls
Secrets in values files; a chart that only works with cluster-admin; policies enforced before they have been read in audit mode; confusing "private cluster" with "private data path".
