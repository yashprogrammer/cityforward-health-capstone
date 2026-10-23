"""FDA Clinical Decision Support exemption, built into the product (Cures Act, FD&C Act section 520(o)(1)(E)).

Software that supports a clinician is NOT a regulated device if ALL FOUR criteria hold. This module is where the product *enforces* them:

  1. does not acquire, process or analyze a medical image or a signal/pattern from a signal-acquisition system   -> the service never receives images/waveforms;
                                                                                                                  scanned PDFs are text documents; there is no such input path
  2. displays, analyzes or prints medical information about a patient                                           -> chart summaries and Q&A over the chart
  3. supports or provides recommendations to a HEALTH CARE PROFESSIONAL (not a patient or caregiver) and does not
     output a specific directive or a risk probability for a disease                                             -> role gate (no patient role), `lint()` below blocks directives and risk scores
  4. lets the HCP independently review the basis for the output, so they need not rely on it primarily          -> every output carries its sources (links to the FHIR resources), the plain-language method, and limits

Prior-authorization drafting and ICD-10 coding are administrative-support functions (520(o)(1)(A)), not CDS, and additionally always need a human decision.
This is engineering evidence for the regulatory file, not legal advice: have regulatory counsel confirm the classification for your intended use.
"""
import re

INTENDED_USE = ("CityForward Copilot helps licensed health care professionals review a hospitalized patient's existing chart. It summarizes and searches "
                "documentation already in the EHR and shows where each statement comes from. It does not diagnose, predict risk, recommend treatment, "
                "generate alerts, analyze images or waveforms, or act without a clinician's review. It is not for patients or caregivers and not for time-critical decisions.")

METHOD = ("A language model reads only the chart passages listed under 'Sources' and writes the text shown. Software then checks that every statement cites a "
          "source and that every number appears in the cited source. If a check fails, the plain list of source passages is shown instead of generated text. "
          "The model can omit or misread information: confirm anything that matters in the source record.")

LIMITS = ["Only documentation already in the EHR is used; absent documentation does not mean absent disease.",
          "Generated text can be incomplete or wrong even when its citations check out: open the source.",
          "Not for time-critical decisions. No alerts or risk scores are produced."]

CRITERIA = {
    "c1_no_image_or_signal": "No medical image, IVD signal or waveform is accepted or analyzed (text documents and discrete results only).",
    "c2_displays_patient_information": "Output is a display of existing chart information (summary, answer, cited passages).",
    "c3_supports_hcp_no_directive": "Used by clinicians only; output is checked for treatment directives and disease risk probabilities.",
    "c4_independent_review_of_basis": "Each output lists its sources with links to the EHR record, states the method, and the user can verify without relying on the output.",
}

# imperative treatment verbs at the start of a sentence, or recommendation phrasing aimed at the reader
_DIRECTIVE = re.compile(
    r"(?:^|[.!?:;]\s+|\n\s*[-*\d.)]*\s*)(?:start|initiate|begin|restart|stop|discontinue|hold|increase|decrease|titrate|administer|give|prescribe|order|refer|transfuse|intubate|admit|"
    r"switch|add|taper|withhold)\b\s+(?!of\b|to\b|parameters?\b|the\s+(?:chart|record|note)\b)"
    r"|\b(?:you|the team|clinicians?|providers?|physicians?)\s+(?:should|must|need to|ought to)\b"
    r"|\bwe\s+recommend\b|\bit is recommended\b|\brecommend(?:ed|s)?\s+(?:that\s+)?(?:starting|stopping|initiating|adding|increasing|discontinuing)\b"
    r"|\bshould\s+be\s+(?:started|stopped|given|administered|discontinued|increased|decreased|held|initiated|transfused|ordered)\b"
    r"|\bconsider\s+(?:starting|stopping|adding|increasing|decreasing|initiating|discontinuing|switching)\b", re.I)
_RISK = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%\s+(?:risk|chance|probability|likelihood)\b|\b(?:risk|probability|likelihood)\s+of\s+[\w\s-]{3,40}?\s+(?:is|of|at)\s+\d|\brisk\s+score\b|\bhigh[- ]risk\s+of\b", re.I)


def lint(text: str) -> list[str]:
    """Problems that would take an output outside the CDS exemption: treatment directives and disease risk probabilities."""
    out = []
    m = _DIRECTIVE.search(text)
    if m:
        out.append(f'contains a treatment directive ("{m.group(0).strip(" .:;")[:40]}..."): describe what is documented instead of what to do')
    m = _RISK.search(text)
    if m:
        out.append(f'contains a risk probability or score ("{m.group(0)[:40]}"): report documented findings, not predictions')
    return out


def envelope(feature: str, sources: list[dict], *, generated: bool, reviewed_required: bool = False) -> dict:
    """The transparency block attached to every AI output (criterion 4)."""
    return {"intended_use": INTENDED_USE, "feature": feature, "ai_generated": generated, "for": "licensed health care professionals",
            "sources": sources, "method": METHOD, "limitations": LIMITS, "human_review_required": reviewed_required,
            "regulatory": {"classification": "non-device clinical decision support (FD&C Act 520(o)(1)(E))", "criteria": CRITERIA}}


ADMIN_ENVELOPE = {"classification": "administrative support software (FD&C Act 520(o)(1)(A)); a human reviews and signs every output", "ai_generated": True,
                  "human_review_required": True}
