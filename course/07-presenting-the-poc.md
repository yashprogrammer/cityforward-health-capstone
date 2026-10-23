# Presenting the POC

The POC is the patient-history summarizer (`poc/`, Build Session 1). You present it to the CMIO and a clinician after discovery, to earn the right to build the real thing. It is not a demo of technology; it is a **demonstration that you understood their problem and respect their risk**.

## Goal of the meeting
Leave with: (1) agreement that the summaries are *useful*, (2) agreement on the *failure modes* that matter to clinicians, (3) a yes to the wedge scope and the pilot units, (4) named clinicians for grading.

## Structure (20 minutes)

1. **The problem in their words (2 min).** "6.3 hours vs 2.5, and about 38% of requests come back with questions." Show the issue tree slide, not code.
2. **Show it work (5 min).** Open a golden patient (Robert Hale). Click Summarize. Point at the citations. Click one: the chart text appears. "Every statement links to where it came from."
3. **Show it refuse (4 min).** Ask a question the chart cannot answer (the penicillin allergy). It says so. Turn the model off: it still shows a clearly labelled template. "When we are unsure we show you the chart, not a guess."
4. **Show the checking (3 min).** Open the grounding test: a summary with an invented troponin value is sent back, then replaced by the plain passages. This is the ChartBot conversation, answered with a demonstration.
5. **Say what it is not (2 min).** No decisions, no predictions, no alerts. It prepares information for a clinician to review.
6. **The shortcuts, honestly (2 min).** Use the table in `poc/README.md`: whole chart in the prompt, no PHI protection, no login, no audit. "These are exactly what the next sessions remove, in this order, and why."
7. **Ask (2 min).** Pilot units, two clinicians for grading, a call with the CISO this week.

## What to say, and what not to say

| Say | Do not say |
|---|---|
| "It cites the chart for every statement; if it cannot, it says so." | "It's 95% accurate." (an average hides the failure that hurts) |
| "A clinician reviews everything; nothing is submitted or decided by software." | "It will reduce clinician burden by X%." (you have not measured) |
| "This runs on synthetic patients. Real data waits for your security review and a BAA." | "It's HIPAA compliant." (nothing is *compliant* in the abstract: say what controls exist) |
| "These shortcuts are deliberate and listed." | "Production-ready." |
| "I'd like two of your clinicians to try to break it." | "Don't worry about the edge cases." |

## Objections you will hear

| Objection | Response |
|---|---|
| "ChartBot made things up. Why is this different?" | Show the grounding check and the unanswerable-question behaviour. "I cannot promise it will never be wrong; I can promise that every number it shows is in the cited text, and you can click through." |
| "Which model is it? Where does our data go?" | "gpt-oss-20b. In this demo, only synthetic patients go to Groq. In your environment it would be the model your CISO approves, in your boundary, with de-identified prompts." |
| "Will it replace Carol?" | "It does the first hour of assembly so she can do the judgement part. She approves and submits. We measure her time before and after, with her." |
| "Can it predict who will be readmitted?" | "Not in this project: a risk score changes the regulatory category. We can show the documented facts that matter and let the clinician judge. I can write up the difference if useful." |
| "How do we know it keeps working after you leave?" | "A golden evaluation set run on every change, clinician-graded; alerts on fallback rate and spend; a runbook; named owners." |

## After the meeting
Send the readback within 24 hours. Add every objection you heard to the risk list. Add every question the clinicians asked the POC to the golden set.
