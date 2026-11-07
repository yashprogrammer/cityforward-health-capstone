"""Register the POC's public key with the synthetic legacy EHR."""
import json
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives import serialization

key = serialization.load_pem_private_key(Path('/run/poc-key/private.pem').read_bytes(), password=None)
jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
jwk.update(kid='poc-reader', alg='RS256', use='sig')
response = httpx.post('http://meditrack:8001/int/admin/smart/clients', json={
    'client_id': 'poc-reader', 'name': 'POC FHIR reader', 'type': 'BACKEND',
    'jwks': {'keys': [jwk]}, 'scopes': 'system/*.read',
}, timeout=30)
response.raise_for_status()
print('Registered the POC FHIR reader.')
