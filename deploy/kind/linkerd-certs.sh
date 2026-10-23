#!/usr/bin/env bash
# Linkerd needs a trust anchor and an issuer certificate. Production: cert-manager with a CA the hospital controls. Laptop/demo: this script.
# Creates ./linkerd-certs/{ca.crt,issuer.crt,issuer.key} (keep them out of git: .gitignore lists linkerd-certs/).
set -euo pipefail
mkdir -p linkerd-certs && cd linkerd-certs
openssl ecparam -name prime256v1 -genkey -noout -out ca.key
openssl req -x509 -new -key ca.key -sha256 -days 3650 -subj "/CN=root.linkerd.cluster.local" -out ca.crt
openssl ecparam -name prime256v1 -genkey -noout -out issuer.key
openssl req -new -key issuer.key -subj "/CN=identity.linkerd.cluster.local" -out issuer.csr
printf "basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n" > ext.cnf
openssl x509 -req -in issuer.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 365 -sha256 -extfile ext.cnf -out issuer.crt
rm -f issuer.csr ext.cnf ca.srl
echo "next: kubectl -n linkerd create secret tls linkerd-identity-issuer --cert=issuer.crt --key=issuer.key  (and pass ca.crt as identityTrustAnchorsPEM)"
