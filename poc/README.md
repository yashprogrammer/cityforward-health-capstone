# Build Session 1: the POC (patient-history summarizer)

One afternoon of work, on synthetic patients, to answer the CMIO's first question: *"can it read a chart and tell me what matters?"*

```bash
pip install -r poc/requirements.txt
export GROQ_API_KEY=...                  # optional; without it the app shows the labelled template summary
streamlit run poc/app.py
PYTHONPATH=poc pytest poc/tests          # no network or key needed (the model is faked)
```

Model: `LLM_PROVIDER=groq` (default `openai/gpt-oss-20b`), `foundry` (any OpenAI-compatible endpoint), or `offline`.

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
| Reads bundle files from disk | no EHR access yet | integrated: reads through MediTrack's FHIR API with a SMART token |
| Whole chart in the prompt | 20 short patients | Build Session 2: retrieval (RAG) |
| Identifiers in the prompt | synthetic data | Build Session 4: PHI redaction before any model call |
| No login, no audit trail | one user, laptop | Sessions 4 and RBAC: audit log, roles, row-level security |
| Grounding is a number/citation check | catches invented facts, not wrong reasoning | golden eval set graded by clinicians |
| Streamlit, single process | demo | Session 6: SMART app inside the EHR |
