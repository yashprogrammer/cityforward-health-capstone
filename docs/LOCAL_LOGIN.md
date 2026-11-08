# Local staff login

The integrated local demo uses a staff ID and a password. Each staff member has
a separate password, checked by the server. Passwords are stored as salted
hashes, and login creates an expiring server-side session. The browser receives
an HttpOnly session cookie; signing out revokes the session and its SMART access.

## Start and create accounts

From the repository root, start the local services:

```bash
docker compose up --build -d
```

There are no preset passwords. Set a password for each demo account you plan
to use; the command asks for it privately and does not echo it to the terminal:

```bash
docker compose exec meditrack python -m meditrack.backend.local_login attending.raman nurse.lopez him.patel pa.hughes it.admin
```

Choose at least 12 characters for each password. You can also set or change one
account later by passing just its staff ID. Changing a password revokes that
account's existing sessions. The login screen is at http://localhost:8001.

| Staff ID | Person | Role |
|---|---|---|
| `attending.raman` | Dr. Priya Raman | Attending |
| `nurse.lopez` | Maria Lopez | Nurse |
| `him.patel` | Anita Patel | Medical coder |
| `pa.hughes` | Carol Hughes | Prior-authorization coordinator |
| `it.admin` | Sam Whitlock | IT administrator |

To change roles while teaching, sign out and sign in as the next account. The
attending is allowed to save a discharge summary and finalize discharge; other
staff cannot perform those two actions. The AI assistant also applies its own
role and patient-access rules.

This is a **local training login for synthetic data**, not a production identity
solution. It has no SSO, MFA, password-reset workflow or distributed rate
limiting. The local EHR displays a hospital-wide census to clinical staff; the
AI assistant applies separate unit and emergency-access rules. Ports in the
local Compose configuration bind to the loopback interface. Do not use this
setup for real patient information or expose it on a network. Cloud startup
remains disabled until an appropriate deployed authentication system exists.
