# Part 2: The standalone proof of concept

The POC is a small, separate patient-history summarizer for a stakeholder demo.
It reads the synthetic FHIR bundle files in `customer/synthea/prepared/bundles`;
it does not need the MediTrack server, a database, or a payer service running.
The `customer/` folder is retained to provide the fictional hospital and data.

## Start with no API key

From the root of this branch, with Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r poc/requirements.txt
streamlit run poc/app.py
```

In the sidebar choose a synthetic patient, select **offline**, and click
**Summarize**. The screen labels the result as a template: this proves the
fallback works and does not make a model call. For an actual model-generated
summary, configure a supported provider as described in `poc/README.md` and
select it. Use synthetic data only.

Run the tests without a model or key:

```bash
PYTHONPATH=poc python -m pytest poc/tests
```

## Explain the data flow

1. `poc/summarizer/records.py` reads a FHIR bundle into a typed patient chart.
2. `poc/summarizer/graph.py` builds evidence and runs the summarization flow.
3. The flow checks citation identifiers and quoted numbers, retries a failed
   answer once, and falls back to the labelled template if it cannot verify it.
4. `poc/app.py` displays the summary and the evidence for human review.

These checks do not prove every statement clinically correct. This prototype
does not provide authentication, an audit trail, or an EHR-embedded workflow.
Those are part of the next stage, not features to imply in the POC demo.

The `legacy` branch is the hospital's starting system. The `integrated` branch
adds the AI service, retrieval, prior authorization, coding review and an EHR
dashboard. See `poc/README.md` for the tradeoffs between the two designs.
