"""Minimal FHIR R4 XML serializer for the conformance surface.

The terminology server emits FHIR resources as JSON by default. The FHIR
R4 terminology-service conformance spec (§4.7.1.1 item 1) requires that
both XML and JSON formats be supported. This module provides a small,
recursive XML serializer that handles the resource shapes the conformance
surface produces: CapabilityStatement, TerminologyCapabilities,
OperationOutcome, Bundle, and Parameters.

Scope:

- Designed for the conformance endpoints (/fhir/metadata and the
  CodeSystem/ValueSet/ConceptMap READ/SEARCH stubs). The full $lookup /
  $expand / $translate operation surfaces continue to emit JSON only;
  the CapabilityStatement accurately advertises this via the
  `format` element when extended XML support is not yet wired into
  every operation handler.
- No external dependencies. Uses xml.sax.saxutils.escape for entity
  safety.

The serializer follows FHIR R4 XML conventions (https://hl7.org/fhir/R4/xml.html):

- The root element is named after ``resourceType``.
- Primitive scalar values become child elements with a ``value`` attribute
  (e.g., ``<status value="active"/>``), per the FHIR XML representation
  rule for primitive types.
- Dicts become child elements. If a dict has a ``url`` key, that key is
  rendered as an XML attribute on the parent element (e.g.,
  ``<extension url="http://x">``) per the FHIR extension convention
  (https://hl7.org/fhir/R4/extensibility.html).
- Lists become repeated child elements.

Non-goal: round-tripping arbitrary FHIR XML. The serializer is a one-way
``dict → XML`` renderer for known resource shapes.
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape


def _xml_escape(value: Any) -> str:
    """Escape a scalar value for safe inclusion as XML element text."""
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _scalar_to_xml_attr(v: Any) -> str:
    """Render a Python scalar as the wire-form for a FHIR XML ``value`` attribute.

    FHIR R4 §3.4.1 mandates that the boolean primitive render as the lowercase
    literals ``true`` / ``false`` — NOT Python's ``str(True)``/``str(False)``
    which produce ``"True"``/``"False"`` (capitalized). Same root cause as the
    v0.0.1 A1 fix (``$extract`` POST boolean parsing) and TS-02's review of
    boolean echo paths: every place Python ``bool`` is rendered to a FHIR
    wire-form value MUST apply this lowercase mapping.

    Other scalar types (int, float, str) pass through ``_xml_escape`` unchanged.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    return _xml_escape(v)


def _is_list_of_dicts(value: Any) -> bool:
    return isinstance(value, list) and value and isinstance(value[0], dict)


def _render_dict_as_element(tag: str, payload: dict[str, Any]) -> str:
    """Render a dict as <tag>...</tag> with FHIR extension/url convention.

    FHIR R4 XML convention (https://hl7.org/fhir/R4/extensibility.html):
    when a dict has a ``url`` key, it is rendered as an XML **attribute**
    on the parent element, not as a child element. This applies primarily
    to ``<extension>`` elements:

        {"url": "http://x", "valueString": "v"}
        → <extension url="http://x"><valueString value="v"/></extension>

    Other primitive values are also rendered as attributes per FHIR's
    "XML representation" rules: scalars attached to value-typed keys
    (``value*``, ``url`` without siblings, etc.) become attributes.

    For simplicity and conformance with the resources this serializer
    supports, we apply two rules:
    1. ``url`` key → XML attribute on the parent.
    2. Other scalar values → child element with the scalar as a
       ``value="..."`` attribute (FHIR primitive element representation).
    """
    # Separate attribute keys (url only, per FHIR extension convention)
    # from child element keys.
    attr_parts: list[str] = []
    child_parts: list[str] = []
    for k, v in payload.items():
        if k == "url" and not isinstance(v, (dict, list)):
            # url is always an attribute on the parent element.
            attr_parts.append(f' url="{_xml_escape(v)}"')
        elif isinstance(v, (dict, list)):
            child_parts.append(_render_value(tag, k, v))
        else:
            # Primitive value → child element with value="..." attribute
            # (FHIR XML convention for primitive types). Use _scalar_to_xml_attr
            # so booleans render as lowercase true/false (FHIR R4 §3.4.1).
            child_parts.append(f'<{k} value="{_scalar_to_xml_attr(v)}"/>')
    return f"<{tag}{''.join(attr_parts)}>{''.join(child_parts)}</{tag}>"


def _render_value(parent_tag: str, key: str, value: Any) -> str:
    """Render a single key/value pair as XML child element(s)."""
    if value is None:
        return ""
    if isinstance(value, list):
        # Repeated element — render each item under the same tag name.
        return "".join(_render_value(parent_tag, key, item) for item in value)
    if isinstance(value, dict):
        # Object element — apply FHIR extension/url convention.
        return _render_dict_as_element(key, value)
    # Scalar element — render as <key value="..."/> (FHIR primitive element).
    # Use _scalar_to_xml_attr so booleans render as lowercase true/false
    # (FHIR R4 §3.4.1 — Python str(True) is "True", not "true").
    return f'<{key} value="{_scalar_to_xml_attr(value)}"/>'


def _render_child(parent_tag: str, key: str, value: Any) -> str:
    """Render a single child element (delegates to _render_value)."""
    return _render_value(parent_tag, key, value)


def to_fhir_xml(payload: dict[str, Any]) -> str:
    """Serialize a FHIR resource dict to an XML string.

    Args:
        payload: A FHIR resource dict with at least a ``resourceType`` key.

    Returns:
        A UTF-8 XML string with the resource as the root element.

    Raises:
        ValueError: If ``payload`` lacks ``resourceType``.
    """
    if "resourceType" not in payload:
        raise ValueError("FHIR XML serialization requires a 'resourceType' key.")
    root = payload["resourceType"]
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<{root} xmlns="http://hl7.org/fhir">',
    ]
    for key, value in payload.items():
        if key == "resourceType":
            continue
        parts.append(_render_value(root, key, value))
    parts.append(f"</{root}>")
    return "".join(parts)
