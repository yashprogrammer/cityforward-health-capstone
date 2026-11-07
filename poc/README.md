# Build Session 1: the POC (patient-history summarizer)

Instructor guide: [TEACHING-POC.md](../TEACHING-POC.md).

One afternoon of work, on synthetic patients, to answer the CMIO's first question: *"can it read a chart and tell me what matters?"*

```bash
pip install -r poc/requirements.txt
# Configure LLM_BASE_URL, LLM_MODEL and Foundry authentication first.
streamlit run poc/app.py
PYTHONPATH=poc pytest poc/tests          # no network or key needed (the model is faked)
```

The POC screen uses Azure AI Foundry. If the model response fails its checks or
the model is unavailable, it shows a labelled template summary instead.

## Optional: connect the POC to the legacy EHR

The default file-based lesson still works. To read the synthetic EHR instead, set
`POC_EHR_URL`, `POC_EHR_PUBLIC_URL`, `POC_FHIR_CLIENT_ID`, and
`POC_FHIR_KEY_FILE` to a registered SMART backend client. `summarizer/fhir.py`
requests a read-only token, fetches the selected patient's FHIR chart, and loads
the linked clinical notes. Keep the private key outside source control.

For the Azure demo, `poc/deploy/compose.yaml` adds this POC alongside the legacy
services and `register_reader.py` registers only the public key. Foundry can use
the VM's managed identity with `FOUNDRY_AUTH=managed_identity`; no model API key
is needed. This POC has no staff login or PHI redaction and is for synthetic data
on a restricted demo network only.

## What it does

`summarizer/records.py` flattens a Synthea FHIR bundle into a typed chart. `summarizer/graph.py` is a small LangGraph:

```
build_context -> summarize -> ground --ok--> done
                                |  bad, one retry -> revise -> ground
                                |  still bad / model down -> template -> done
```

The model must cite evidence ids; `ground` checks (in plain Python) that each citation exists and each number it quotes appears in the cited
evidence. A summary that fails is sent back once with the list of problems, then replaced by a deterministic template that says so.

## POC shortcuts (the list the next session removes)

| Shortcut | Why it is fine for a POC | Where it gets fixed |
|---|---|---|
| By default reads bundle files from disk | no EHR access needed for the first lesson | optional live FHIR adapter; integrated: SMART launch inside the EHR |
| Whole chart in the prompt | 20 short patients | Build Session 2: retrieval (RAG) |
| Identifiers in the prompt | synthetic data | Build Session 4: PHI redaction before any model call |
| No login, no audit trail | one user, laptop | Sessions 4 and RBAC: audit log, roles, row-level security |
| Grounding is a number/citation check | catches invented facts, not wrong reasoning | golden eval set graded by clinicians |
| Streamlit, single process | demo | Session 6: SMART app inside the EHR |
