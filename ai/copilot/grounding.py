"""Deterministic checks on model output. The model proposes; code verifies. Shared by Q&A, summaries and prior-auth drafts."""
import re

_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_SMALL = {"1", "2", "3", "4", "5"}   # counts in prose ("2 stents", "3 days") are not data


def check_statement(text: str, cited_ids: list[str], evidence: dict[str, str]) -> list[str]:
    """Problems with one statement: no citation, a citation that does not exist, or a number the cited evidence does not contain."""
    if not cited_ids:
        return [f'no citation: "{text[:70]}"']
    unknown = [c for c in cited_ids if c not in evidence]
    if unknown:
        return [f'unknown evidence id {unknown} in: "{text[:70]}"']
    cited_nums = set(_NUM.findall(" ".join(evidence[c] for c in cited_ids)))
    return [f'number {n} in "{text[:70]}" is not in the cited evidence {cited_ids}' for n in _NUM.findall(text) if n not in cited_nums and n not in _SMALL]
