"""Code-set checks that need no model and no network. ICD-10-CM is public domain (CMS); the `simple-icd-10-cm` package ships the FY release.

SNOMED CT needs a UMLS licence and cannot be redistributed, so this repo carries only the handful of SNOMED -> ICD-10-CM pairs that appear in
the synthetic cohort (a demonstration subset: a production system would load the licensed SNOMED-to-ICD-10-CM map from the UMLS).
CPT is AMA-licensed and not used; HCPCS Level II is public.
"""
import re
from functools import lru_cache

import simple_icd_10_cm as icd

SNOMED_TO_ICD10CM_DEMO = {
    "401303003": "I21.02",      # Acute ST segment elevation myocardial infarction of anterior wall
    "49436004": "I48.91",       # Atrial fibrillation -> unspecified AF
    "195967001": "J45.909",     # Asthma
    "13645005": "J44.9",        # COPD
    "38341003": "I10",          # Hypertensive disorder
    "44054006": "E11.9",        # Type 2 diabetes mellitus
}


def norm(code: str) -> str:
    return code.replace(".", "").replace("-", "").upper()


def pretty(code: str) -> str:
    """I2102 -> I21.02"""
    c = norm(code)
    return c if len(c) <= 3 else f"{c[:3]}.{c[3:]}"


def valid(code: str) -> bool:
    return icd.is_valid_item(pretty(code))


def billable(code: str) -> bool:
    """A code can be reported only at its most specific (leaf) level."""
    c = pretty(code)
    return icd.is_valid_item(c) and icd.is_leaf(c)


def describe(code: str) -> str:
    return icd.get_description(pretty(code))


def children(code: str) -> list[str]:
    return [c for c in icd.get_children(pretty(code))]


@lru_cache(maxsize=2048)
def _excludes1_patterns(code: str) -> tuple[str, ...]:
    c = pretty(code)
    out = []
    for node in [c, *icd.get_ancestors(c)]:
        if not icd.is_valid_item(node):
            continue
        for text in icd.get_excludes1(node):
            out += [norm(m) for m in re.findall(r"\(([A-Z][0-9A-Z.\-]*)\)", text)]
    return tuple(out)


def excludes1_conflict(a: str, b: str) -> bool:
    """ICD-10-CM Excludes1: 'NOT CODED HERE': the two conditions cannot be reported together."""
    na, nb = norm(a), norm(b)
    return any(nb.startswith(p) for p in _excludes1_patterns(pretty(a)) if p) or any(na.startswith(p) for p in _excludes1_patterns(pretty(b)) if p)


def is_unspecified(code: str) -> bool:
    d = describe(code).lower()
    return "unspecified" in d or d.endswith(" nos")
