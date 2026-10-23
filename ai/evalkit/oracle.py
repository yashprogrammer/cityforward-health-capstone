"""Stand-in model backends for testing the evaluation harness itself (not for judging real models).

AskOracle      answers golden `ask` questions perfectly from the passages it is shown (verbatim sentences, correct citations), abstains on the
               unanswerable ones: what a near-perfect model would score.
Hallucinator   answers with invented numbers and treatment directives: what a bad model would score. The grounding code should turn all of it into
               plain passages, so the SAFETY gates still pass while the QUALITY gates fail.
"""
import json
import re

from copilot.llm import Reply

from . import runner


def _passages(user: str) -> dict[str, str]:
    return dict(re.findall(r"^\[(C\d+)\] (.*)$", user, flags=re.M))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


class AskOracle:
    name = "oracle:ask"

    def __init__(self):
        # the redactor turns dates in the question into [D+n]; compare with dates blanked on both sides
        self.cases = {re.sub(r"\d{4}-\d{2}-\d{2}", "<D>", c["question"]): c for c in runner.load_cases()["ask"]}

    def chat(self, system: str, user: str) -> Reply:
        question = re.search(r"QUESTION: (.*)", user).group(1).strip()
        case = self.cases.get(re.sub(r"\[D[+-]\d+\]", "<D>", question))
        if case is None or case.get("unanswerable"):
            reply = {"answer": "The chart passages do not contain this information.", "citations": [], "insufficient_evidence": True}
        else:
            passages = _passages(user)
            used, parts = [], []
            for group in case["must_include"]:
                hit = next(((cid, s) for cid, t in passages.items() for s in _sentences(t) if any(a.lower() in s.lower() for a in group)), None)
                if hit and hit[1] not in parts:
                    if hit[0] not in used:
                        used.append(hit[0])
                    parts.append(hit[1])
            reply = {"answer": " ".join(parts) or "Not found.", "citations": used, "insufficient_evidence": not parts}
        text = json.dumps(reply)
        return Reply(text, tokens_in=len(user) // 4, tokens_out=len(text) // 4)


class Hallucinator:
    name = "bad:hallucinator"

    def chat(self, system: str, user: str) -> Reply:
        if "QUESTION:" in user:
            reply = {"answer": "Troponin peaked at 99999 ng/L; start amiodarone and expect a 45% risk of readmission.", "citations": ["C1"], "insufficient_evidence": False}
        else:
            reply = {"headline": "Everything is fine", "course": "Uneventful stay with 77777 events.", "history": "None.",
                     "findings": [{"text": "Hemoglobin 88888 g/dL", "sources": ["E1"]}], "follow_up": []}
        text = json.dumps(reply)
        return Reply(text, tokens_in=len(user) // 4, tokens_out=len(text) // 4)
