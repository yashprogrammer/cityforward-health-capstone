"""Cost and latency budget calculator.

Measures how big the real prompts are (the same pipelines the golden set runs, with a probe in place of the model), then prices them with the volume and price assumptions
at the top of this file. Change the assumptions, not the code. Replace the output-token and latency guesses with your own run's numbers (`python -m evalkit run` prints usage per case).

    PYTHONPATH=ai:customer python -m evalkit.budget
"""
import json
import statistics
import sys

from copilot.llm import PRICES, LLMError, Reply, cost_usd

from . import runner

# ---- assumptions (a 250-bed community hospital) -------------------------------------------------------------------------------------
DISCHARGES_PER_DAY = 45
CALLS_PER_DISCHARGE = {            # model calls per discharge, by purpose, before caching
    "summary": 6.0,                # the team opens the dashboard several times over a stay
    "ask": 5.0,
    "prior-auth": 0.6,             # about 40% of patients are insured by plans that need authorization, 1-2 services each, one call per draft
    "coding": 1.0,
}
CACHE_HIT_RATE = {"summary": 0.7}  # unchanged chart + same prompt/model
OUTPUT_TOKENS = {"summary": 450, "ask": 160, "prior-auth": 380, "coding": 260}      # typical JSON replies: REPLACE with measured values
TOKENS_PER_CHAR = 0.25             # rough English/clinical text ratio
DECODE_TOKENS_PER_S = 350          # output speed of the served model (REPLACE with measured)
TTFT_S = 0.5                       # time to first token incl. network
BUDGET = {"cost_per_discharge_usd": 0.10, "interactive_p95_s": 8.0, "background_p95_s": 60.0}
INTERACTIVE = {"summary", "ask"}
MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]


class Probe:
    """Records the size of every prompt, then fails the call so the graph takes its fallback (no model needed)."""
    name = "probe:measure"

    def __init__(self):
        self.sizes: dict[str, list[int]] = {}
        self.purpose = None

    def chat(self, system, user):
        key = ("coding" if "clinical coding assistant" in system else "prior-auth" if "prior-authorization coordinator" in system
               else "summary" if "summarize a hospital patient" in system else "ask")
        self.sizes.setdefault(key, []).append(len(system) + len(user))
        raise LLMError("probe")


def measure(rt) -> dict[str, int]:
    probe = Probe()
    runner.run(probe, rt, kinds={"ask", "summary", "prior_auth", "coding"})
    return {k: int(statistics.median(v) * TOKENS_PER_CHAR) for k, v in probe.sizes.items()}


def table(in_tokens: dict[str, int]) -> list[dict]:
    rows = []
    for model in MODELS:
        per_discharge, details = 0.0, []
        for purpose, calls in CALLS_PER_DISCHARGE.items():
            effective = calls * (1 - CACHE_HIT_RATE.get(purpose, 0))
            tin, tout = in_tokens.get(purpose, 1500), OUTPUT_TOKENS[purpose]
            c = cost_usd(model, tin, tout) * effective
            lat = TTFT_S + tout / DECODE_TOKENS_PER_S
            per_discharge += c
            details.append({"purpose": purpose, "tokens_in": tin, "tokens_out": tout, "model_calls": round(effective, 2), "usd_per_discharge": round(c, 5),
                            "est_latency_s": round(lat, 1), "budget_s": BUDGET["interactive_p95_s" if purpose in INTERACTIVE else "background_p95_s"]})
        rows.append({"model": model, "usd_per_discharge": round(per_discharge, 4), "usd_per_month": round(per_discharge * DISCHARGES_PER_DAY * 30, 2),
                     "within_budget": per_discharge <= BUDGET["cost_per_discharge_usd"], "by_purpose": details})
    return rows


def main() -> int:
    from .harness import full_stack
    with full_stack() as (_m, rt):
        in_tokens = measure(rt)
    print("median prompt size (tokens):", in_tokens)
    print(f"assumptions: {DISCHARGES_PER_DAY} discharges/day; prices (USD per 1M tokens in/out): " + ", ".join(f"{m}={PRICES[m]}" for m in MODELS))
    for r in table(in_tokens):
        print(f"\n{r['model']}: ${r['usd_per_discharge']}/discharge, ${r['usd_per_month']}/month, within budget (${BUDGET['cost_per_discharge_usd']}): {r['within_budget']}")
        for d in r["by_purpose"]:
            print(f"   {d['purpose']:<11} in {d['tokens_in']:>5} out {d['tokens_out']:>4}  calls/discharge {d['model_calls']:>5}  ${d['usd_per_discharge']:<8}  ~{d['est_latency_s']}s (budget {d['budget_s']}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
