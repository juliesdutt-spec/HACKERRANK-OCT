"""Decide whether a request actually asks for deletion.

Built from every distinct request text in the two request files (docs/dataset_findings.md):
* 453 requests use one of 12 English deletion sentences, followed by neutral scope and closing
  sentences; 85 use fixed deletion templates in Portuguese, French, Japanese, Spanish and Korean;
* the 32 samples are free-form but all ask for deletion or for a deletion check;
* 10 requests ask for something else (correction, opt-out, export, access, login help, invoice
  copies, email change) and each says so explicitly ("not a request to erase it", "Do not close
  the account", "please keep the account as it is").

So an explicit statement that deletion is not wanted, or that the account must stay, decides
NOT_APPLICABLE; otherwise a deletion term in any of the observed languages decides DELETION; a
text with neither is AMBIGUOUS and is never guessed. The audit engine audits an AMBIGUOUS request
as a deletion and flags it, because NOT_APPLICABLE needs evidence that deletion was not requested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DELETION = "DELETION"
NON_DELETION = "NON_DELETION"
AMBIGUOUS = "AMBIGUOUS"

# Statements that the person does not want deletion, or wants the account kept.
NON_DELETION_CUES = {
    "negated_deletion": re.compile(
        r"\bnot (?:a request|asking)(?: (?:to|for))? (?:erase|erasure|delet\w*|remov\w*|clos\w*)"
        r"|\b(?:don't|do not|without) (?:delet\w*|remov\w*|clos\w*|eras\w*|chang\w* anything)",
        re.IGNORECASE,
    ),
    "keep_account": re.compile(
        r"\b(?:keep|leave) (?:the|my) (?:account|profile)\b|\bleave the account active\b"
        r"|\bcontinue using\b|\bstill (?:use|need) (?:it|the account)\b",
        re.IGNORECASE,
    ),
    "marketing_only": re.compile(r"\bmarketing lists only\b|\bstop the promotional emails\b", re.IGNORECASE),
}

# Deletion vocabulary per language seen in the requests (plus German, a common EU language).
DELETION_CUES = {
    "en": re.compile(r"\b(?:delet(?:e|ed|es|ing|ion)|eras(?:e|ed|ure)|remov(?:e|ed|al)|clos(?:e|ed|ing)|closure)\b", re.IGNORECASE),
    # asking to confirm a deletion outcome ("confirm that the profile is gone")
    "en_outcome": re.compile(r"\b(?:account|profile)s? (?:is|was|are|were|has been|have been) gone\b", re.IGNORECASE),
    "es": re.compile(r"\b(?:elimin(?:ar|aci[oó]n|en|e)|borr(?:ar|ado|en))\b", re.IGNORECASE),
    "pt": re.compile(r"\b(?:exclus[aã]o|exclu(?:ir|am)|apag(?:ar|uem))\b", re.IGNORECASE),
    "fr": re.compile(r"\b(?:suppression|supprim(?:er|ez)|effac(?:er|ez))\b", re.IGNORECASE),
    "de": re.compile(r"\bl[oö]sch(?:en|ung)\b", re.IGNORECASE),
    "ja": re.compile(r"削除|消去"),
    "ko": re.compile(r"삭제|탈퇴"),
}


@dataclass(frozen=True)
class RequestIntent:
    intent: str
    cues: tuple[str, ...]  # "<cue name>:<matched text>"
    reason: str


def classify_request(text: str) -> RequestIntent:
    against = [f"{name}:{m.group(0)}" for name, rx in NON_DELETION_CUES.items() for m in rx.finditer(text)]
    if against:
        return RequestIntent(NON_DELETION, tuple(against), "the request explicitly declines deletion or asks to keep the account")
    found = [f"deletion_{lang}:{m.group(0)}" for lang, rx in DELETION_CUES.items() for m in rx.finditer(text)]
    if found:
        return RequestIntent(DELETION, tuple(found), "the request asks for deletion (or to verify one) in plain terms")
    return RequestIntent(AMBIGUOUS, (), "no deletion term and no explicit non-deletion statement")
