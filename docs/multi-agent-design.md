# Multi-agent design: when to split, and how this system does it

The course lecture on this is mostly about restraint. The code is `ai/copilot/discharge.py`; the tests are `ai/tests/test_discharge.py`.

## The question to ask first: do we need more than one agent?

A single prompt with all the context can do summaries, coding and prior-auth drafts. We split anyway because the three jobs differ on every axis that matters in a hospital:

| Axis | Summary | Coding | Prior authorization |
|---|---|---|---|
| Who may run it | any clinician on the unit | coder, attending | coordinator, clinician |
| Output | prose a clinician reads | code lines a coder decides | structured evidence per payer requirement |
| Wrong looks like | a misleading sentence | an unbillable or Excludes1-conflicting code | a number the chart does not contain |
| Deterministic check | citations and numbers appear in sources | code is valid, billable, quote is verbatim | value meets policy, date is fresh, attestation is human |
| Model that earns its cost | strongest available (prose quality) | small and fast (the code set does the checking) | small and fast (the policy does the checking) |
| Failure that must not spread | none | none | the payer being down |
| Evaluated by | stay-summary cases | coding cases | requirement-status cases |

Different permissions, different models, different verifiers, different failure modes, different evaluations: that is what justifies separate agents. If two of those jobs had matched on every row, they would be one agent.

## Rules we followed

1. **A plain-code supervisor, not an orchestrator model.** The routing rules are: run the agents this role is allowed to run; run one prior-auth draft per service the plan will want authorized; skip what is already open. That is an `if`, not a judgement call. A model in that seat would add latency, cost and a new way to be wrong for no flexibility anyone needs. (Use a model to route when the routing really is open-ended: choosing among tools for an unforeseen task. This task is not.)
2. **Agents never call each other.** They share the chart (read-only) and write only their own artifact (a cached summary, a coding worksheet, prior-auth drafts). No hidden coupling, so each can be tested, replaced and evaluated alone.
3. **Permissions per agent, enforced by the same role matrix as the API.** A case manager who triggers "prepare discharge packet" gets the summary agent only; the coding and prior-auth agents report `skipped` with the reason. The event worker runs as a service principal that holds all three.
4. **Failure isolation.** Each agent runs in its own worker with a timeout; an exception or a hang becomes that agent's `failed` result, never the packet's. In the event path, a failed agent makes the whole event retry later (idempotently: finished artifacts exist and are not duplicated), and after the retry budget the event is dead-lettered for a human.
5. **Parallel by default.** The three agents are independent, so the packet takes as long as the slowest agent, not the sum (`test_agents_run_in_parallel_and_the_supervisor_uses_no_model`).
6. **Each agent has its own purpose label.** Metering, budgets, model routing (`LLM_MODEL_CODING`), traces and the golden set all key on it, which is what makes the cost and quality of each agent visible separately.
7. **Humans remain the only deciders.** The packet's output is a *to-do list for people*: prior-auth requirements that need an attestation, coding documentation queries, attention flags. Nothing is submitted, signed or coded by the supervisor.

## What it looks like at runtime

```
discharge-advised event (or "Prepare discharge packet" in the dashboard)
   └─ supervisor: index the chart, then in parallel:
        summary agent    -> cached cited summary                 (strong model)
        coding agent     -> OPEN worksheet with queries           (small model)
        prior-auth agent -> DRAFT per service, gaps listed        (small model)
   └─ supervisor: collect blockers (open requirements, coding queries, attention flags) -> stored "readiness" record
```

## When we would add an LLM supervisor, honestly

If the system grew open-ended tasks ("find everything blocking this patient's discharge, across systems, and propose next steps"), a planner agent with tools would be reasonable: with a budget, a step limit, tool permissions equal to the caller's, every tool call audited and every output going through the same grounding and CDS checks. Until then, the simplest design that is safe wins.

## Anti-patterns avoided

* An "agent" per prompt (a pipeline of seven model calls where one deterministic function would do).
* Agents that talk to each other in natural language (untestable, injection-friendly, and the transcript becomes PHI).
* A shared mutable scratchpad all agents write to.
* Giving the supervisor model more permissions than the caller.
