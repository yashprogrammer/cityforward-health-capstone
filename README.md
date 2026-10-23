# CityForward Health — FDE Capstone 1 (Healthcare)

The healthcare capstone of the Forward Deployed Engineer course. You join an engagement at **CityForward Health**, a US hospital
running **MediTrack**, a legacy EHR, and take one AI engagement end to end: discovery workshop → POC → production AI features →
a hospital-style security review → executive readout and handoff.

> CityForward Health, MediTrack, all people, payers and patients are **fictional**. Patient data is **synthetic** (Synthea).

## Branches

| Branch | What it contains | Course sessions |
|---|---|---|
| `main` | Course materials only | Discovery, mock call, debrief, scoping the wedge, readout, handoff, interview |
| `legacy` | + **the customer's world**: MediTrack EHR (FHIR R4 API, SMART on FHIR, webhooks), mock payer, the hospital's Kubernetes platform | Explained by the instructor; you study it like an FDE on day one |
| `poc` | + the LangGraph patient-history summarizer POC | Build Session 1, presenting the POC |
| `integrated` | + the production AI layer, compliance controls, deployment (one git tag per build session) | Build Sessions 2–6 onwards |

## Start here

- [`docs/decisions.md`](docs/decisions.md): the decision record and build plan for the whole capstone.
