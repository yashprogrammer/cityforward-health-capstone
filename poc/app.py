"""POC: patient-history summarizer. `streamlit run poc/app.py` (from the repo root).

Pick a patient bundle from the Synthea folder, press the button, read the summary and the evidence it cites.
This is the thing you show a CMIO after one afternoon of work. Its shortcuts are listed in poc/README.md.
"""
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from summarizer.graph import summarize                    # noqa: E402
from summarizer.llm import LLMError, get_llm              # noqa: E402
from summarizer.records import list_bundles, load_bundle  # noqa: E402

BUNDLES = Path(os.getenv("BUNDLES_DIR", Path(__file__).parent.parent / "customer" / "synthea" / "prepared" / "bundles"))

st.set_page_config(page_title="Patient history summarizer (POC)", page_icon=":stethoscope:", layout="wide")
st.title("Patient history summarizer")
st.caption("Proof of concept on synthetic patients. Not a medical device; a clinician reviews everything.")

options = list_bundles(BUNDLES)
if not options:
    st.error(f"No patient bundles found in {BUNDLES}")
    st.stop()
label = st.sidebar.selectbox("Patient", [o[0] for o in options])
provider = st.sidebar.selectbox("Model", ["auto", "groq", "foundry", "offline"], help="auto = Groq if GROQ_API_KEY is set, else offline template")
path = dict(options)[label]
rec = load_bundle(path)

left, right = st.columns([3, 2])
with right:
    st.subheader("Chart at a glance")
    st.write(f"**{rec.name}**, {rec.age} {rec.sex}, {rec.payer or 'self-pay'}")
    st.write(f"{len(rec.encounters)} encounters · {len(rec.observations)} observations · {len(rec.notes)} notes · {len(rec.medications)} medication orders")

if st.sidebar.button("Summarize", type="primary"):
    try:
        llm = get_llm(None if provider == "auto" else provider)
    except LLMError as e:
        st.sidebar.error(str(e))
        st.stop()
    with st.spinner("Summarizing and checking every citation..."):
        res = summarize(rec, llm)
    with left:
        if res.mode == "template":
            st.warning(f"Template summary (no AI used): {res.note}")
        else:
            st.success(f"AI summary by {res.model}; every citation was checked against the chart" + (f" (after {res.attempts} revision)" if res.attempts else ""))
        s = res.summary
        st.header(s.headline)
        st.markdown(f"**This stay.** {s.course}")
        st.markdown(f"**History.** {s.history}")
        st.subheader("Key findings")
        for f in s.findings:
            st.markdown(f"- {f.text}  `{' '.join(f.sources)}`")
        st.subheader("Open items for the clinician")
        for f in s.follow_up:
            st.markdown(f"- {f.text}  `{' '.join(f.sources)}`")
    with right:
        with st.expander("Evidence the model was given", expanded=False):
            for k, v in res.evidence.items():
                st.markdown(f"**{k}** {v}")
else:
    with left:
        st.info("Choose a patient and press Summarize.")
