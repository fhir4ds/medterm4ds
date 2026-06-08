"""Display normalization helpers."""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")

_ACRONYM_CANONICAL = {
    "aids": "AIDS",
    "cdc": "CDC",
    "cns": "CNS",
    "covid": "COVID",
    "csf": "CSF",
    "dna": "DNA",
    "fda": "FDA",
    "hba1c": "HbA1c",
    "hiv": "HIV",
    "mrna": "mRNA",
    "rna": "RNA",
    "usp": "USP",
}

_UNIT_CANONICAL = {
    "cm": "cm",
    "dl": "dL",
    "g": "g",
    "iu": "IU",
    "kg": "kg",
    "l": "L",
    "mcg": "mcg",
    "meq": "mEq",
    "mg": "mg",
    "ml": "mL",
    "mmhg": "mmHg",
    "mmol": "mmol",
    "mol": "mol",
    "ng": "ng",
    "u": "U",
}

_CHEMICAL_MORPHEMES = (
    "aza",
    "benz",
    "bicyclo",
    "diox",
    "ethyl",
    "hydroxy",
    "methyl",
    "octane",
    "oxy",
    "phenyl",
    "propyl",
)


def format_patient_friendly_name(name: str) -> str:
    """Return a conservative title-cased patient-friendly display name.

    Every punctuation or whitespace boundary starts a new word, but existing
    mixed-case terms, acronyms, and common clinical units are preserved. Mostly
    systematic chemical strings are left unchanged because mechanical title
    casing makes them less readable.
    """
    if not name:
        return name
    if _looks_like_systematic_chemical_name(name):
        return name
    return _WORD_RE.sub(lambda match: _format_word(match.group(0)), name)


def _format_word(word: str) -> str:
    lower = word.lower()
    if lower in _UNIT_CANONICAL:
        return _UNIT_CANONICAL[lower]
    if lower in _ACRONYM_CANONICAL:
        return _ACRONYM_CANONICAL[lower]
    if not any(ch.isalpha() for ch in word):
        return word
    if any(ch.isdigit() for ch in word):
        return word
    if word.isupper():
        return word
    if any(ch.isupper() for ch in word):
        return word
    return word[:1].upper() + word[1:]


def _looks_like_systematic_chemical_name(name: str) -> bool:
    stripped = name.strip()
    if " " in stripped:
        return False
    lower = stripped.lower()
    punctuation_count = sum(lower.count(ch) for ch in "-(),[]")
    if punctuation_count < 4:
        return False
    if not re.search(r"(?:^|[-(,])\d+(?:,\d+)*(?:[-,)]|$)", lower):
        return False
    morpheme_hits = sum(1 for morpheme in _CHEMICAL_MORPHEMES if morpheme in lower)
    return morpheme_hits >= 2
