"""Generate the Copilot's backend-services key pair. Run once; keep the private key secret, give hospital IT the JWKS.

    python scripts/gen_keys.py ./keys        -> keys/ai-backend.pem (private), keys/ai-backend.jwks.json (public, goes into MediTrack's SMT_CLIENT)

In compose the `keygen` one-shot service does this into a shared volume; in Kubernetes the private key is a Secret (Key Vault CSI in AKS).
"""
import json
import os
import sys
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

out = Path(sys.argv[1] if len(sys.argv) > 1 else "keys")
kid = sys.argv[2] if len(sys.argv) > 2 else "ai-backend-1"
out.mkdir(parents=True, exist_ok=True)
pem_path = out / "ai-backend.pem"
if pem_path.exists():
    key = serialization.load_pem_private_key(pem_path.read_bytes(), None)
else:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    pem_path.chmod(0o644 if os.getenv('KEYGEN_SHARED') else 0o600)   # shared dev volume only; in Kubernetes the key is a Secret
jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
jwk.update(kid=kid, alg="RS256", use="sig")
(out / "ai-backend.jwks.json").write_text(json.dumps({"keys": [jwk]}))
print(f"wrote {pem_path} and {out / 'ai-backend.jwks.json'}")
