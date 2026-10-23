# Assignment: the deployed system and the recorded executive readout

**Submit:** (1) a running deployment of your capstone, (2) a recorded executive readout, (3) a short written summary. You are graded on engineering judgement and honesty as much as on completeness.

## 1. The deployed system

Deploy the `integrated` branch to a cluster you control (kind on a laptop with a public tunnel is acceptable; AKS is better) so the checker can reach it.

### Self-check before you submit
```bash
python course/check_submission.py --url https://your-host --eval results/your-run/metrics.json --recording https://link-or-path --summary summary.md
```
The checker (no credentials needed) verifies from the outside:

| Check | What it proves |
|---|---|
| `/ai/healthz` and `/fhir/metadata` respond | the system is up behind one origin |
| SMART configuration advertises PKCE (`S256`) and the token endpoint | the launch flow is real |
| unauthenticated calls to `/ai/api/...` return 401 | access control is on |
| `/ai/metrics` is not reachable | metrics are not exposed through the ingress |
| security headers (CSP, `nosniff`, `Referrer-Policy`; HSTS on https) | basic hardening |
| a webhook without a valid signature returns 401 | event ingestion is authenticated |
| your evaluation file passes the **safety** gates (zero directive/risk outputs, zero identifier leaks) and records the model you used | you ran the golden set and kept the safety properties |
| the recording and summary exist | the readout was delivered |

## 2. The recorded executive readout
Follow [08-executive-readout.md](08-executive-readout.md): 10-15 minutes, decision-first, evidence on every claim, a "not yet proven" slide, rehearsed Q&A. Submit a link or file.

## 3. The summary (one page, `summary.md`)
1. Your wedge and why. 2. What you built, in one paragraph. 3. Your results table (real numbers, graders named, or "not measured"). 4. The three decisions you are proudest of and one you would reverse. 5. The gaps. 6. How a hospital reviewer would find your evidence (point at files).

## Rubric (100 points)

| Area | Points | Full marks look like |
|---|---|---|
| Discovery and scoping artifacts | 10 | issue tree with numbers, scope with out-of-scope and acceptance criteria, wedge memo with a scoring and a "what would change my mind" |
| System works end to end | 20 | launch from the EHR, cited summary, prior-auth with the human steps and payer response, coding worksheet, events trigger preparation |
| Safety by construction | 20 | grounding with fallbacks, de-identification with a leakage test, CDS lint, human sign-off enforced in code |
| Access control and audit | 10 | roles, row-level security, break-the-glass, tamper-evident audit |
| Evaluation | 10 | golden set you extended (at least five cases of your own), automatic gates, clinician grading pack, results reported honestly |
| Deployment and operations | 10 | chart renders and validates, secrets not in git, network policy, alerts, runbook |
| Security review pack | 5 | questionnaire answers with evidence links and an honest gaps list |
| Executive readout | 10 | decision-first, honest about what is unproven, handles hard questions |
| Honesty | 5 | no unmeasured claims, no "compliant"/"certified"; gaps named |

**Automatic deductions:** real patient data anywhere (-20); a secret committed (-10); claims of certification (-5 each).
