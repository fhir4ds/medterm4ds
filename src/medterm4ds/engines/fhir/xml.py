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

import re
from typing import Any
from xml.sax.saxutils import escape

# XML 1.0 illegal characters (https://www.w3.org/TR/REC-xml/#NT-Char):
# 0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F. These cannot appear in XML documents
# in ANY encoding — not even as character references. xml.sax.saxutils.escape
# only handles & < > " '; without stripping, a client-supplied code like
# "a\x01b" echoed into a value attribute produces not-well-formed XML with
# HTTP 200 + application/fhir+xml. Mirrors the control-char sanitizer
# apps/fhir_api.py:_fhir_error applies to diagnostics. Found by QC-300
# (EC-13 EDGE_CASE, HIGH).
_ILLEGAL_XML_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _xml_escape(value: Any) -> str:
    """Escape a scalar value for safe inclusion as XML element text.

    Strips XML-1.0-illegal control characters BEFORE entity escaping so
    the output is always well-formed XML 1.0 (QC-300).
    """
    return escape(
        _ILLEGAL_XML_CHARS_RE.sub("", str(value)),
        {'"': "&quot;", "'": "&apos;"},
    )


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
    """Render a dict as <tag>...</tag> — the ONE element renderer.

    Consolidated in EC-13 remediation (QC-302/303/304): the dict branch
    previously inlined its own scalar rendering, which drifted from
    ``_render_value`` (Python None leaked as the literal string "None").
    All value rendering now delegates to ``_render_value``. Rules:

    1. QC-302: the ``url`` → attribute hoist applies ONLY when the element
       tag is ``extension`` (FHIR R4 https://hl7.org/fhir/R4/extensibility.html).
       Everywhere else ``url`` is a primitive child element
       (``<url value="..."/>``) — the prior unconditional hoist produced
       spec-invalid ``<implementation url="...">`` in CapabilityStatement
       and ``<resource url="...">`` for batch-embedded ValueSets.
    2. QC-303: a dict carrying ``resourceType`` is a contained FHIR resource
       (e.g. Bundle.entry.resource) — its content renders inside an element
       NAMED by the resourceType, and ``resourceType`` is NOT rendered as a
       child element (FHIR R4 https://hl7.org/fhir/R4/xml.html). The prior
       code emitted the spec-invalid ``<resourceType value="Parameters"/>``.
    3. QC-304: scalar values route through ``_render_value``, whose None
       guard omits the element instead of rendering ``value="None"``.
    """
    attr_parts: list[str] = []
    child_parts: list[str] = []
    inner_tag: str | None = None
    for k, v in payload.items():
        if k == "resourceType" and isinstance(v, str) and v:
            # Contained resource: the element name IS the resource type.
            inner_tag = v
            continue
        if (
            k == "url"
            and tag == "extension"
            and not isinstance(v, (dict, list))
        ):
            # url is an attribute on the PARENT element — <extension> only.
            attr_parts.append(f' url="{_xml_escape(v)}"')
            continue
        child_parts.append(_render_value(tag, k, v))
    body = "".join(child_parts)
    if inner_tag is not None:
        body = f"<{inner_tag}>{body}</{inner_tag}>"
    return f"<{tag}{''.join(attr_parts)}>{body}</{tag}>"


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
