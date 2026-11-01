# Part 1: The hospital before the AI project

This branch is the starting point for the engagement. It contains MediTrack,
the mock payer, synthetic patients and the interfaces an outside system can use.
There is no AI application in this branch. The patient names and records are
fictional.

## Start a separate teaching copy

Install Docker Desktop and start it. Run these commands from this branch's root:

```bash
export PG_PORT=127.0.0.1:15432 PAYER_PORT=127.0.0.1:18003 MEDITRACK_PORT=127.0.0.1:18001
export PUBLIC_BASE_URL=http://localhost:18001
docker compose -p cityforward-legacy up --build -d
```

Open http://localhost:18001 for the EHR and http://localhost:18003/rules for the
mock payer. The named project gives this copy its own database and volumes; the
different ports let it coexist with the integrated demo. Stop it with
`docker compose -p cityforward-legacy stop`. Do not add `-v` to cleanup commands
unless you intend to delete that copy's saved data.

The user switcher is a local teaching shortcut, not authenticated staff login.
Use synthetic records only and keep this demonstration on your own computer.

## A walkthrough for recording

1. Open **IPD Census**, select a patient and show the diagnosis, labs, notes and
   discharge summary. These are the records the hospital already has.
2. Open **MIS: Discharge TAT** to explain the delay the project addresses.
3. Explain the integration doors using `customer/meditrack/backend/fhir.py`,
   `smart.py`, and `events.py`. These interfaces exist before the AI product.
4. Open the payer's `/rules` page. It is a simulation, not a real insurer.

Next, switch to the `poc` branch for the standalone chart summarizer. The
`integrated` branch adds the AI service and the embedded clinician dashboard.
See `customer/README.md` for the system's schema and example patient stories.
