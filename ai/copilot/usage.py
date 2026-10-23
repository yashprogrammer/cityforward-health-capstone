"""What the AI costs and how fast it is: the numbers behind the budget (docs/cost-latency.md). Aggregates only: no patient data."""
from fastapi import APIRouter

from . import db
from .access import authorize
from .auth import CurrentUser, Principal

router = APIRouter(prefix="/api")


@router.get("/usage")
def usage(days: int = 7, p: Principal = CurrentUser):
    authorize(p, "usage.view")
    days = max(1, min(days, 90))
    from .app import rt
    gw = rt.get_gateway()
    with db.system() as c:
        rows = c.execute("""SELECT purpose, model, count(*) calls, sum(tokens_in) tokens_in, sum(tokens_out) tokens_out, sum(cost_usd)::float cost_usd,
                                   percentile_cont(0.5) WITHIN GROUP (ORDER BY ms)::int p50_ms, percentile_cont(0.95) WITHIN GROUP (ORDER BY ms)::int p95_ms,
                                   count(*) FILTER (WHERE status <> 'ok') failed
                            FROM llm_call WHERE ts > now() - make_interval(days => %s) GROUP BY purpose, model ORDER BY purpose, model""", (days,)).fetchall()
        cache = c.execute("SELECT feature, count(*) entries, sum(hits) hits FROM result_cache GROUP BY feature").fetchall()
    return {"days": days, "by_purpose": rows, "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6), "spend_today_usd": round(gw.spend_today(max_age=0), 6),
            "daily_budget_usd": gw.daily_budget_usd or None, "cache": cache}
