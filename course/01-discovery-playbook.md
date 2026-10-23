# Discovery playbook (a forward deployed engineer's first two weeks)

Discovery is not a requirements meeting. It is the work of finding **one problem that is painful, measurable, reachable with the data and systems that exist, and allowed by the organization's rules**, and of earning the right to build for it.

## The seven questions every discovery must answer

| # | Question | Evidence you want | CityForward example |
|---|---|---|---|
| 1 | **Who hurts, and how much?** | a named role, a frequency, a cost | case managers and the PA coordinator chase payers by phone; insured patients wait **6.3 h** after discharge is advised vs **2.5 h** self-pay |
| 2 | **What is the current process, really?** | you watched it (not the policy document) | discharge advised → coordinator assembles evidence from 5 places → fax/portal → payer queries (~38%) → rework |
| 3 | **What data exists and how bad is it?** | you looked at records, not at a data dictionary | notes are free text, results are in lab tables, scanned PDFs have no structure, the implant log is a document |
| 4 | **What can we touch?** | integration surface, who owns it, lead time | MediTrack offers a FHIR R4 API, SMART launch, webhooks; the hot folder is the old path; IT owns change windows |
| 5 | **What are we not allowed to do?** | compliance, security, contracts | HIPAA (BAA before PHI leaves), FDA CDS boundary, HITRUST-based review by the CISO |
| 6 | **Who decides, who blocks, who must be happy?** | a stakeholder map with incentives | CMIO (sponsor, clinical safety), CNO/clinical ops (workload), revenue cycle (money), CISO (blocker), IT (operator), HIM (coding) |
| 7 | **How will we know it worked?** | a baseline measured *before* you build | the MIS report: TAT by payer class, query rate and reasons |

## Sequence

1. **Before the first call (2 hours).** Read public material (annual report, the EHR vendor's integration docs, the payer's prior-auth policies). Draft the stakeholder map and a first issue tree (see [02](02-mece-issue-tree.md)). Write the three hypotheses you would bet on.
2. **Stakeholder calls (30-45 min each).** One sponsor, one operator per workflow, one gatekeeper (security/IT), one data owner. Different questions for each (bank below). Record, with consent; send a written readback within 24 hours.
3. **Workflow shadowing (half a day).** Sit with the coordinator through two discharges. Count clicks and tabs and phone calls. Ask "why" at every copy-paste.
4. **Data reality check (a day).** Pull ten real (here: synthetic) charts end to end. Where does the evidence for a payer requirement live? How often is it missing? Write down the percentage, not an adjective.
5. **Constraints register.** Security, compliance, network, change management, procurement, clinical governance. For each: owner, what they need to see, lead time.
6. **Baseline.** Reproduce the customer's own metric from their data. If you cannot reproduce their number you do not yet understand their process.
7. **Synthesis.** Issue tree with numbers, wedge memo ([06](06-wedge-memo.md)), scope document ([03](03-scope-document.md)), risk list. Playback to the sponsor; get explicit sign-off or explicit disagreement.

## Question bank

**Sponsor (CMIO).** What would make this a success in your annual review? What did you try before and why did it stop? What is the one thing that would make you shut this down? Who else has to say yes? How do clinicians currently react to "AI" in the chart?
**Clinical operations.** Walk me through the last discharge that went badly. Where do you wait? Which of those waits are about information and which are about people? Who touches the patient's record in the last 24 hours before discharge?
**Revenue cycle / prior-auth coordinator.** Show me a request that came back with a query. What did the payer ask for? Where was the answer in the chart? How long does assembly take? What do you do when you cannot find it?
**HIM / coding.** Where do you disagree with the clinicians' documentation most often? What queries do you send? What is the turnaround? What coding tools do you use today and what do they get wrong?
**CISO.** What does a vendor have to show you before PHI flows? Which framework do you assess against? Have you approved a model provider before? What is the incident notification window you require? Where must data live?
**IT.** What can integrate with MediTrack today and what is a project? Who operates the cluster? What are your change windows? How do we get a test environment that looks like production?

## Anti-patterns

* Asking "what do you want?" instead of "what happened the last time?".
* Solving in the call ("we could use an LLM for that") before you have quantified the problem.
* Treating the sponsor's account as the process.
* A scope that lists technologies instead of outcomes.
* Skipping the baseline: you will never be able to prove value afterwards.
* Forgetting the gatekeepers until week six.
