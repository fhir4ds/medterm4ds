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

_SMALL_WORDS = frozenset({
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "in",
    "into",
    "nor",
    "of",
    "on",
    "or",
    "per",
    "the",
    "to",
    "via",
    "vs",
    "with",
    "without",
})

_STRONG_BOUNDARIES = frozenset({":", ";", ".", "?", "!", ",", "(", "[", "{"})


def format_patient_friendly_name(name: str) -> str:
    """Return a conservative smart-title-cased patient-friendly display name.

    Every punctuation or whitespace boundary starts a new word, but existing
    mixed-case terms, acronyms, and common clinical units are preserved. Short
    articles, prepositions, and conjunctions stay lowercase unless they start
    the label or follow a strong phrase boundary. Mostly systematic chemical
    strings are left unchanged because mechanical title casing makes them less
    readable.
    """
    if not name:
        return name
    if _looks_like_systematic_chemical_name(name):
        return name

    pieces: list[str] = []
    last_end = 0
    seen_word = False
    for match in _WORD_RE.finditer(name):
        delimiter = name[last_end:match.start()]
        pieces.append(delimiter)
        capitalize_small_word = not seen_word or any(ch in _STRONG_BOUNDARIES for ch in delimiter)
        pieces.append(_format_word(match.group(0), capitalize_small_word=capitalize_small_word))
        seen_word = True
        last_end = match.end()
    pieces.append(name[last_end:])
    return "".join(pieces)


def _format_word(word: str, *, capitalize_small_word: bool) -> str:
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
    if lower in _SMALL_WORDS and not capitalize_small_word:
        return lower
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


def join_limited(values, limit: int = 10, *, repr_values: bool = False) -> str:
    """Join values into a bounded diagnostic enumeration (QC-396/QC-399).

    Pre-fix, error paths embedded EVERY offending value verbatim — a
    10K-entry --sources filter produced a single 109KB one-line stderr
    error, and a 10K-entry relationship_types list a ~120KB ValueError.
    Sibling of the QC-217/QC-218/QC-235 length-cap family: cap the
    enumeration at *limit* entries and summarize the remainder.

    ``repr_values=True`` renders each entry with ``repr()`` (used where the
    values are identifiers whose empty/whitespace form matters to the
    diagnostic).
    """
    rendered = [repr(v) if repr_values else str(v) for v in values[:limit]]
    text = ", ".join(rendered)
    remainder = len(values) - limit
    if remainder > 0:
        text += f", and {remainder} more"
    return text
