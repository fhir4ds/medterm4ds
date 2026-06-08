from __future__ import annotations

from medterm4ds.core.display import format_patient_friendly_name
from medterm4ds.core.models import CodeRef, FriendlyNameResult


def test_patient_friendly_name_title_cases_after_punctuation_boundaries():
    assert (
        format_patient_friendly_name("estrogens, conjugated (USP) Injectable Product")
        == "Estrogens, Conjugated (USP) Injectable Product"
    )


def test_patient_friendly_name_preserves_units_acronyms_and_mixed_case():
    assert (
        format_patient_friendly_name("magnesium sulfate 20 mg/mL hba1c covid vaccine")
        == "Magnesium Sulfate 20 mg/mL HbA1c COVID Vaccine"
    )


def test_patient_friendly_name_keeps_small_words_lowercase():
    assert (
        format_patient_friendly_name(
            "removal of cancer skin growth of face, accessed through the skin"
        )
        == "Removal of Cancer Skin Growth of Face, Accessed Through the Skin"
    )
    assert (
        format_patient_friendly_name("non-antibody testing for syphilis")
        == "Non-Antibody Testing for Syphilis"
    )


def test_patient_friendly_name_preserves_systematic_chemical_strings():
    name = "5-hydroxymethyl(methyleneoxy)-1-aza-3,7-dioxabicyclo(3,3,0)octane"
    assert format_patient_friendly_name(name) == name


def test_friendly_name_result_formats_name_only():
    result = FriendlyNameResult(
        code=CodeRef(source="CHV", code="0001"),
        name="abnormal csf",
        friendly_source="CHV",
        match_type="broader",
        technical_name="abnormal csf",
    )

    assert result.name == "Abnormal CSF"
    assert result.technical_name == "abnormal csf"
