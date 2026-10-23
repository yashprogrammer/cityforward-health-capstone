# MECE issue trees

**MECE** = *mutually exclusive, collectively exhaustive*: every cause falls into exactly one branch, and together the branches cover everything. It is not a diagram style, it is a discipline for not fooling yourself:
if two branches overlap you will double-count a fix; if a branch is missing you will build for the wrong 60%.

## How to build one (five steps)

1. Write the question as a measurable outcome ("why do insured patients wait 6.3 hours after discharge is advised?").
2. Choose **one principle** to split by (time order, process stage, owner, cause type). Mixing principles is how overlaps sneak in.
3. Split into 3-6 branches. Test: *could one delay belong to two branches?* (not exclusive) / *is there a delay that fits none?* (not exhaustive).
4. Attach a number or a data source to every leaf, even a rough one. An issue tree without numbers is a mind map.
5. Prune by size and by what you can change; the survivors are your candidate wedges.

## CityForward: why do insured patients wait longer after discharge is advised?

Principle: **process stage, in time order, from "discharge advised" to "patient leaves".**

```
Insured TAT 6.3 h vs self-pay 2.5 h   → the 3.8 h gap is the target
├─ A. Clinical readiness (is the patient actually ready?)          same for both groups → ≈0 h of the gap, exclude
├─ B. Evidence assembly (documents, results, notes gathered)       owner: case manager / PA coordinator      data: shadowing + audit
│    B1 pending result not documented
│    B2 operative / procedure report missing
│    B3 implant record missing
│    B4 diagnosis in the summary inconsistent with the admitting diagnosis
├─ C. Authorization submission and rework (the payer loop)         owner: PA coordinator / payer          data: payer queries ≈38%
│    C1 first submission incomplete → documentation query (the five reasons above)
│    C2 resubmission and waiting for the second answer
├─ D. Payer review time (the payer's clock)                        owner: payer                           data: PA status timestamps; mostly not ours to fix
└─ E. Post-acute logistics (facility bed, transport, equipment)    owner: case management                 data: SNF acceptance times
```
Check: a delay while the coordinator hunts for the implant sticker is B3, not C. A delay because the payer queued the request is D, not C. A bed at the SNF opening later is E. Each minute lands in one place.

### Sizing (illustrative until you measure it)

| Branch | Share of the gap | Controllable by us? | Because |
|---|---|---|---|
| A | ~0% | no | equal across payers |
| B | ~25% | yes | information scattered in free text and documents |
| C | ~35% | yes, partly | fewer incomplete first submissions means fewer rework loops |
| D | ~15% | no | payer's service level |
| E | ~25% | no (adjacent) | logistics, not information |

B and C together are the controllable ~60%: **prepare complete, evidence-backed requests before anyone asks**. That is the wedge ([06](06-wedge-memo.md)).

## Common mistakes

* Splitting by *solution* ("an AI could help with...") instead of by *cause*.
* A branch called "Other". Name what is in it or split again.
* Overlapping owners ("coordinator" and "case manager" both under C): split by process stage or by owner, never both.
* Numbers that are the team's opinion. Mark them as hypotheses and name the experiment that will confirm them.
