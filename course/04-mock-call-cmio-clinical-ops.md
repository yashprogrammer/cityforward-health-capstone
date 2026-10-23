# Mock call: CMIO and Clinical Operations (45 minutes)

**Format.** Two instructors (or one instructor and a TA) play the customer. A learner plays the FDE; the cohort watches and scores with the debrief rubric ([05](05-call-debrief.md)). Record it: the recording is reviewed in the debrief.
**Goal of the call (for the FDE).** Leave with: the workflow and where it breaks, one measurable baseline, the names of everyone who can say no, and agreement on a next step. *Not* a solution.

## The people

| Role | Name | What they want | What they fear | How they talk |
|---|---|---|---|---|
| CMIO (sponsor) | Dr. Naomi Feldman | fewer clinician hours on paperwork; a project that does not embarrass her with the board or the CISO | a "black box" in the chart; another failed vendor pilot | precise, skeptical, warm if you listen; will test whether you understand clinical safety |
| Director of Clinical Operations / CNO | Marcus Webb, RN | patients leave on time; nurses stop chasing paper | anything that adds clicks to nursing workflows | blunt, practical, talks in stories about specific patients |
| Prior-authorization coordinator (joins late) | Carol Hughes | not to be replaced; fewer payer calls | being measured by a tool she does not control | quiet until asked about her process |

## Setting
CityForward Health, Columbus, Ohio. MediTrack EHR (v3.4, "older than our residents' careers"). Four inpatient units. A previous vendor ("ChartBot", an ambient scribe pilot) was dropped last year after clinicians found it "made things up".

## What the customer will say *if asked well* (the facts to elicit)

Hand this section to the role-players only.

| Fact | Surfaces when the FDE asks... | If not asked, they will |
|---|---|---|
| Insured patients wait ~6.3 h from "discharge advised" to leaving; self-pay ~2.5 h | "How long between the decision and the patient leaving? Does it differ by payer?" | say "discharges are slow sometimes" |
| About 38% of authorization requests come back with a documentation query | "How often do the payers come back with questions? Why?" | say "payers are difficult" |
| The five reasons: pending results not documented; operative report missing; principal diagnosis inconsistent with the admitting diagnosis; attending NPI missing; implant record missing | "Take the last query: what did they ask for, and where was it?" | name only "missing paperwork" |
| Carol assembles evidence from five places and it takes 45-90 minutes a request; she keeps a spreadsheet of payer rules | (once Carol joins) "Show me the last one you did." | not mention the spreadsheet |
| The evidence is mostly free text in progress notes; results are in lab tables; scanned PDFs are not searchable | "Where does the evidence actually live?" | say "it's all in the EHR" |
| Dr. Feldman will not accept anything that "decides" or "predicts"; ChartBot's failure was invented facts, not accuracy on average | "What went wrong last time?" | stay polite and noncommittal |
| The CISO (Walter Brandt) must approve; he assesses vendors against HITRUST-based questionnaires and requires a BAA before any PHI goes to a model; 60-day breach clock is not negotiable | "Who else has to say yes? What do they need?" | say "IT will handle it" |
| MediTrack exposes a FHIR API, SMART launch and webhooks, but IT (Sam Whitlock) has only turned them on for one internal pilot | "What can we integrate with today?" | say "it has an API" |
| Change windows are Tuesday 02:00-04:00; the pilot units would be 4 West (cardiac) and 5 South (ortho) because their attendings are supportive | "Where would you pilot? When can we change things?" | say "we'll figure it out" |

## Red herrings (a good FDE sets them aside politely)
* Marcus asks for a readmission-risk score "since we're doing AI anyway" (this crosses the FDA line; see the CDS memo).
* Dr. Feldman mentions an ambient scribe vendor demo she liked (a different, crowded, regulated market).
* A request for "a chatbot for patients to ask about their discharge" (patient-facing: out of scope).

## Run of show

| Min | Segment | Role-player behaviour |
|---|---|---|
| 0-5 | Opening | Dr. Feldman: "We've got 45 minutes. Tell me what you've heard about us and what you think you can do." (A weak opening pitches AI; a strong one says what they read and asks to learn.) |
| 5-20 | Problem and process | Marcus tells a story of a specific patient stuck for a day. Reward follow-ups; stonewall solution-pitching. |
| 20-30 | Data and systems | Dr. Feldman defers to "IT" until asked directly for Sam's name and what is enabled. |
| 30-37 | Constraints and risk | Raise the CISO only if asked "who can block this?". Raise ChartBot only if asked "what did you try before?". |
| 37-42 | Success and next steps | If the FDE proposes a baseline measurement and a shadowing session, grant it. If they propose a build, say "that's a lot to commit to before we agree what we're fixing." |
| 42-45 | Close | Dr. Feldman: "What do you need from me, and when will I hear from you?" (A strong close names three asks, three dates and a written readback within 24 hours.) |

## Role-play rules
Answer only what is asked. Do not volunteer the table above. Be human: interrupt, be briefly distracted by a page, be short with a rambling question. If the FDE explains a technology for more than 60 seconds, glance at the clock.
