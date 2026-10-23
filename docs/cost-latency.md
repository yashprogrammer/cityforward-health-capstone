# Cost and latency budget

Numbers come from `python -m evalkit.budget` (it measures the real prompts the pipelines build, then prices them). Prices are illustrative list prices for budgeting exercises: check the provider's current sheet.
Assumptions (a 250-bed community hospital): 45 discharges a day; per discharge 6 dashboard opens (70% served from cache), 5 questions, 0.6 prior-auth drafts, 1 coding worksheet.

## The budget

| Dimension | Budget | Why |
|---|---|---|
| Model cost | **$0.10 per discharge** (and a hard daily cap) | an order of magnitude below anything a finance team will argue about; the cap exists for bugs, not for normal days |
| Interactive latency (summary, ask) | p95 **8 s** | a clinician will not wait longer than they would take to open the chart themselves; a spinner with "reading the chart" tolerates this |
| Background latency (prior-auth draft, coding) | p95 **60 s** | prepared before anyone asks, triggered by the discharge-advised event |
| Availability of the *clinical workflow* | independent of the model | every feature has a labelled no-AI fallback |

## Measured prompt sizes (median, tokens) and the result

| Feature | prompt tokens | reply tokens (assumed) | model calls per discharge | $ per discharge, gpt-oss-20b | $ per discharge, gpt-oss-120b | est. latency |
|---|---|---|---|---|---|---|
| Stay summary | 1361 | 450 | 1.8 (after 70% cache hits) | 0.00043 | 0.00085 | 1.8 s |
| Ask the chart | 541 | 160 | 5.0 | 0.00044 | 0.00089 | 1.0 s |
| Prior-auth draft (one call extracts all requirements) | 892 | 380 | 0.6 | 0.00011 | 0.00022 | 1.6 s |
| Coding worksheet | 1420 | 260 | 1.0 | 0.00018 | 0.00037 | 1.2 s |
| **Total** | | | | **$0.0012** (about $1.60 a month) | **$0.0023** (about $3.10 a month) | all inside budget |

### What this teaches (say it out loud in the lecture)

1. **Tokens are not where the money is.** At these sizes the model is a rounding error next to a coordinator's hour or a bed-day. The budgeting question that matters in a hospital is *"what does one avoided hour of discharge delay or one avoided payer query save?"*, not "which model is cheapest". Measure the *business* number (the MIS report: 6.3 h for insured vs 2.5 h self-pay) before optimizing tokens.
2. **Real charts are bigger.** The synthetic charts are small. Multiply prompt size by 10 (a long stay with hundreds of notes) and the cost is still about a cent. The thing that grows is latency and the risk of the model losing the middle of a long prompt, which is why retrieval selects evidence instead of pasting the chart.
3. **Choose the model per job, not per product.** `LLM_MODEL_CODING=...` routes a purpose to a different model (`RoutedBackend`); the bigger model costs twice as much and only earns it where the golden set shows a quality difference.
4. **The cheapest call is the one you do not make.** The result cache (`copilot/cache.py`) keys on prompt version + model + the exact evidence, so it cannot serve a stale answer and needs no invalidation. The prior-auth draft extracts every requirement in one call instead of one call per requirement.
5. **A budget is a control, not a report.** `COPILOT_DAILY_BUDGET_USD` makes the gateway refuse model calls when the day's spend reaches the cap; every feature then falls back to its no-AI mode and an audit event and a metric fire (`copilot_budget_blocked_total`). Test: `ai/tests/test_cost.py::test_daily_budget_is_a_circuit_breaker_that_falls_back_to_no_ai`.
6. **Latency is the sum of the slowest honest path.** Retrieval (Postgres, tens of ms), de-identification (ms), the model (about 0.5 s to first token plus output length / decode speed), grounding checks (ms). The retry after a failed grounding check doubles the model time, which is why the interactive budget is 8 s and not 4 s.

## How to watch it in production

* `GET /api/usage?days=7` (IT/compliance roles): calls, tokens, spend, p50/p95 latency, failure counts and cache hits by purpose and model.
* Prometheus: `copilot_llm_cost_usd_total`, `copilot_llm_latency_seconds`, `copilot_cache_total`, `copilot_budget_blocked_total`; alerts `ModelSpendAboveBudget`, `ModelLatencyHigh`, `HighFallbackRate` (`deploy/platform/monitoring/prometheus-rules.yaml`).
* Re-run `python -m evalkit.budget` and the golden set whenever the model, the prompt or the evidence format changes: a silent model update is a cost, latency and *quality* change.

## Levers, in the order to try them

1. Cache more (summaries are done; stable chart sections could be summarized once per day).
2. Send less: fewer, better-chosen passages (`rag.search(k=...)`, `summary.select_facts(limit=...)`).
3. Route cheap jobs to a smaller model, verified by the golden set.
4. Prompt caching at the provider for the long, unchanging system prompt.
5. Only then, a smaller or self-hosted model: it changes the compliance story (no vendor BAA) as much as the cost.
