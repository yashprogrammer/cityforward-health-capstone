# Hospital identity provider (Keycloak)

`cityforward-realm.json` is what hospital IT would configure: roles (ATTENDING, RESIDENT, NURSE, PA_COORD, HIM_CODER, IT_ADMIN, COMPLIANCE), staff users with a `units` attribute, brute-force
protection, short token lifetimes, password policy, and the AI dashboard registered as a **public client with PKCE**. Mappers add `units` and the audience `cityforward-ai` to the access token.

```bash
docker compose --profile idp up -d keycloak     # http://localhost:8180  (admin / admin), realm "cityforward", staff password: demo-password-12
export KEYCLOAK_ISSUER=http://localhost:8180/realms/cityforward KEYCLOAK_AUDIENCE=cityforward-ai
docker compose up -d ai                           # the AI service now also trusts Keycloak-issued tokens
```

Status: the realm file and the service's claim mapping are written to Keycloak 26's documented import format and covered by unit tests with locally signed tokens
(`ai/tests/test_rbac.py`); a live Keycloak could not be started in the authoring sandbox, so the first `--import-realm` boot is the first real test.
