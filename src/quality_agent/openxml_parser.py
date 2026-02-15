"""
OpenXML parser — extract paragraphs with formatting metadata.

Handles the ``word/document.xml`` part of a .docx file, as well as the
``pkg:package`` clipboard format (which bundles themes, styles, settings
and other parts that are irrelevant for rule-checking).

Deterministic cleaning removes rsid / tracking attributes to reduce tokens.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Sequence

from .models import (
    DocumentCheckRequest,
    FormattingFlags,
    ParsedParagraph,
    StyleInfo,
    TextRun,
)

# Type alias: style_id → StyleInfo
StyleMap = dict[str, StyleInfo]

logger = logging.getLogger(__name__)

# ── namespace constants ────────────────────────────────────────────────
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{W_NS}}}"
PKG_NS = "http://schemas.microsoft.com/office/2006/xmlPackage"
_PKG = f"{{{PKG_NS}}}"

# Pre-register so ET serialisation avoids ``ns0:`` prefixes
ET.register_namespace("w", W_NS)
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")


def _t(local: str) -> str:
    """Build a Clark-notation tag for the ``w:`` namespace."""
    return f"{_W}{local}"


# ── pkg:package extraction ─────────────────────────────────────────────

def extract_document_from_pkg(ooxml: str) -> str:
    """Extract ``<w:document>`` XML from a ``pkg:package`` wrapper.

    The Word clipboard / Office Open XML format wraps the real content
    in multiple ``<pkg:part>`` sections.  We only need the one whose
    ``pkg:name`` is ``/word/document.xml``.

    If *ooxml* is already a bare ``<w:document>`` or ``<w:body>``,
    it is returned unchanged.

    Returns
    -------
    str
        The clean ``<w:document>`` XML — typically 95-99 % smaller than
        the full ``pkg:package``.
    """
    stripped = ooxml.strip()
    # Fast check: if it doesn't look like a pkg:package, return as-is
    if not stripped.startswith("<?xml") and not "<pkg:package" in stripped:
        return ooxml

    # Strip XML processing instructions so ET can parse the root element
    xml_str = re.sub(r"<\?[^?]+\?>", "", stripped).strip()
    if not xml_str.startswith("<pkg:package") and "<pkg:package" not in xml_str:
        return ooxml

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        # If even the cleaned version fails, try the raw ooxml
        try:
            root = ET.fromstring(ooxml)
        except ET.ParseError:
            logger.warning("Cannot parse pkg:package — returning raw OOXML")
            return ooxml

    # Register pkg namespace to navigate
    ET.register_namespace("pkg", PKG_NS)

    # Find /word/document.xml part
    for part in root.iter(f"{_PKG}part"):
        name = part.get(f"{_PKG}name") or part.get("pkg:name") or ""
        if name == "/word/document.xml":
            xml_data = part.find(f"{_PKG}xmlData")
            if xml_data is not None and len(xml_data) > 0:
                doc_elem = xml_data[0]  # <w:document>
                return ET.tostring(doc_elem, encoding="unicode")

    # Fallback: extract via regex (handles namespace prefix variations)
    m = re.search(
        r"(<w:document\b[^>]*>.*?</w:document>)",
        ooxml,
        re.DOTALL,
    )
    if m:
        return m.group(1)

    logger.warning("No /word/document.xml found in pkg:package — returning raw OOXML")
    return ooxml


def _measure_ooxml_reduction(original: str, extracted: str) -> None:
    """Log how much we saved by stripping the pkg:package wrapper."""
    orig_len = len(original)
    ext_len = len(extracted)
    if orig_len > ext_len:
        pct = (1 - ext_len / orig_len) * 100
        logger.info(
            "OOXML stripped: %d → %d chars (%.0f%% reduction, ~%d tokens saved)",
            orig_len, ext_len, pct, (orig_len - ext_len) // 4,
        )


# ── style extraction ───────────────────────────────────────────────────

def extract_styles_from_pkg(ooxml: str) -> StyleMap:
    """Extract style definitions from ``/word/styles.xml`` in a ``pkg:package``.

    Returns a mapping of style-ID → :class:`StyleInfo` containing the
    human-readable style name and any formatting defaults defined by
    the style (bold, italic, font, etc.).
    """
    stripped = ooxml.strip()
    if "<pkg:package" not in stripped and not stripped.startswith("<?xml"):
        return {}

    xml_str = re.sub(r"<\?[^?]+\?>", "", stripped).strip()
    if "<pkg:package" not in xml_str:
        return {}

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        try:
            root = ET.fromstring(ooxml)
        except ET.ParseError:
            logger.warning("Cannot parse pkg:package for styles")
            return {}

    ET.register_namespace("pkg", PKG_NS)

    for part in root.iter(f"{_PKG}part"):
        name = part.get(f"{_PKG}name") or part.get("pkg:name") or ""
        if name == "/word/styles.xml":
            xml_data = part.find(f"{_PKG}xmlData")
            if xml_data is not None and len(xml_data) > 0:
                return _parse_style_definitions(xml_data[0])

    # Fallback: try regex extraction
    m = re.search(
        r"(<w:styles\b[^>]*>.*?</w:styles>)",
        ooxml,
        re.DOTALL,
    )
    if m:
        try:
            styles_root = ET.fromstring(m.group(1))
            return _parse_style_definitions(styles_root)
        except ET.ParseError:
            pass

    return {}


def extract_styles_from_xml(styles_xml: str) -> StyleMap:
    """Parse a standalone ``<w:styles>`` XML string into a :data:`StyleMap`."""
    try:
        root = ET.fromstring(styles_xml)
        return _parse_style_definitions(root)
    except ET.ParseError:
        logger.warning("Cannot parse styles XML")
        return {}


def _parse_style_definitions(styles_elem: ET.Element) -> StyleMap:
    """Build a StyleMap from a ``<w:styles>`` element."""
    style_map: StyleMap = {}

    for style_el in styles_elem.findall(_t("style")):
        style_id = style_el.get(f"{_W}styleId") or style_el.get("styleId") or ""
        if not style_id:
            continue

        style_type = (
            style_el.get(f"{_W}type") or style_el.get("type") or "paragraph"
        )

        name_el = style_el.find(_t("name"))
        name = ""
        if name_el is not None:
            name = name_el.get(f"{_W}val") or name_el.get("val") or ""

        # Extract base formatting from <w:rPr> inside the style
        fmt = FormattingFlags()
        rPr = style_el.find(_t("rPr"))
        if rPr is None:
            # Try inside <w:pPr><w:rPr> (paragraph-level run defaults)
            pPr = style_el.find(_t("pPr"))
            if pPr is not None:
                rPr = pPr.find(_t("rPr"))

        if rPr is not None:
            fmt = FormattingFlags(
                bold=_has_tag(rPr, "b"),
                italic=_has_tag(rPr, "i"),
                underline=_has_tag(rPr, "u"),
                strikethrough=_has_tag(rPr, "strike"),
                superscript=_vert_align(rPr, "superscript"),
                subscript=_vert_align(rPr, "subscript"),
                all_caps=_has_tag(rPr, "caps"),
                small_caps=_has_tag(rPr, "smallCaps"),
                font_name=_attr(rPr, "rFonts", "ascii"),
                font_size_half_pts=_int_attr(rPr, "sz", "val"),
                font_color=_attr(rPr, "color", "val"),
                highlight_color=_attr(rPr, "highlight", "val"),
            )

        style_map[style_id] = StyleInfo(
            style_id=style_id,
            name=name,
            style_type=style_type,
            base_formatting=fmt,
        )

    logger.info("Parsed %d style definitions", len(style_map))
    return style_map


def _merge_style_formatting(
    explicit: FormattingFlags,
    style_fmt: FormattingFlags,
) -> FormattingFlags:
    """Merge style-inherited formatting into a run's explicit formatting.

    Explicit properties always win — style values only fill in where the
    run had no explicit setting.
    """
    return FormattingFlags(
        bold=explicit.bold or style_fmt.bold,
        italic=explicit.italic or style_fmt.italic,
        underline=explicit.underline or style_fmt.underline,
        strikethrough=explicit.strikethrough or style_fmt.strikethrough,
        superscript=explicit.superscript or style_fmt.superscript,
        subscript=explicit.subscript or style_fmt.subscript,
        all_caps=explicit.all_caps or style_fmt.all_caps,
        small_caps=explicit.small_caps or style_fmt.small_caps,
        font_name=explicit.font_name or style_fmt.font_name,
        font_size_half_pts=(
            explicit.font_size_half_pts or style_fmt.font_size_half_pts
        ),
        font_color=explicit.font_color or style_fmt.font_color,
        highlight_color=explicit.highlight_color or style_fmt.highlight_color,
    )


# ── public API ─────────────────────────────────────────────────────────

def parse_openxml(
    xml_content: str,
    style_map: StyleMap | None = None,
) -> DocumentCheckRequest:
    """Parse an OpenXML ``<w:document>`` or ``<w:body>`` fragment.

    Also accepts the full ``pkg:package`` clipboard format — the wrapper
    is stripped automatically.

    Parameters
    ----------
    xml_content:
        Raw XML string — ``<pkg:package>``, ``<w:document>`` or just
        ``<w:body>``.
    style_map:
        Optional pre-parsed style definitions.  If *None* and the input
        is a ``pkg:package``, styles are extracted automatically.

    Returns
    -------
    DocumentCheckRequest
        Parsed paragraphs with formatting metadata + a cleaned XML string.
    """
    # ── extract styles from pkg:package (before we strip it) ───────────
    if style_map is None:
        style_map = extract_styles_from_pkg(xml_content)

    # ── strip pkg:package wrapper if present ────────────────────────────
    doc_xml = extract_document_from_pkg(xml_content)
    _measure_ooxml_reduction(xml_content, doc_xml)

    root = ET.fromstring(doc_xml)

    body = root.find(_t("body"))
    if body is None:
        body = root  # caller passed <w:body> directly

    paragraphs: list[ParsedParagraph] = []
    for idx, p_elem in enumerate(body.findall(_t("p"))):
        parsed = _parse_paragraph(p_elem, idx, style_map)
        if parsed.plain_text.strip():
            paragraphs.append(parsed)

    cleaned = _clean_xml(doc_xml)

    return DocumentCheckRequest(paragraphs=paragraphs, cleaned_xml=cleaned)


def parse_json_request(json_content: str | dict[str, Any]) -> DocumentCheckRequest:
    """Parse the JSON request format produced by the Word add-in.

    Expected structure::

        {
          "documentInfo": { "totalParagraphsInDoc": 12, … },
          "paragraphs": [
            {
              "docParagraphIndex": 7,
              "selectionIndex": 1,
              "textPreview": "…",
              "ooxmlLength": 48683,
              "ooxml": "<pkg:package …>…</pkg:package>"
            }
          ]
        }

    Each paragraph's ``ooxml`` is a ``pkg:package`` wrapper around a
    ``<w:document>`` with one or more ``<w:p>`` elements.

    Returns
    -------
    DocumentCheckRequest
        Merged paragraphs from all entries.
    """
    if isinstance(json_content, str):
        data = json.loads(json_content)
    else:
        data = json_content

    doc_info = data.get("documentInfo", {})
    logger.info(
        "JSON request: %d paragraphs selected (of %d in document)",
        doc_info.get("selectedParagraphs", "?"),
        doc_info.get("totalParagraphsInDoc", "?"),
    )

    all_paragraphs: list[ParsedParagraph] = []
    style_map: StyleMap = {}
    total_raw = 0
    total_stripped = 0

    for entry in data.get("paragraphs", []):
        raw_ooxml = entry.get("ooxml", "")
        if not raw_ooxml:
            continue

        total_raw += len(raw_ooxml)

        # Extract styles from this pkg:package (first entry wins)
        if not style_map:
            style_map = extract_styles_from_pkg(raw_ooxml)

        doc_xml = extract_document_from_pkg(raw_ooxml)
        total_stripped += len(doc_xml)

        # Parse the extracted <w:document> fragment
        try:
            root = ET.fromstring(doc_xml)
        except ET.ParseError:
            logger.warning(
                "Cannot parse OOXML for paragraph index %s — skipping",
                entry.get("docParagraphIndex", "?"),
            )
            continue

        body = root.find(_t("body"))
        if body is None:
            body = root

        doc_para_idx = entry.get("docParagraphIndex", 0)
        for sub_idx, p_elem in enumerate(body.findall(_t("p"))):
            parsed = _parse_paragraph(p_elem, doc_para_idx + sub_idx, style_map)
            if parsed.plain_text.strip():
                all_paragraphs.append(parsed)

    if total_raw > 0:
        pct = (1 - total_stripped / total_raw) * 100
        logger.info(
            "Total OOXML: %d → %d chars (%.0f%% reduction, ~%d tokens saved)",
            total_raw, total_stripped, pct, (total_raw - total_stripped) // 4,
        )

    # Build a well-formed <w:document><w:body>…</w:body></w:document> for
    # downstream re-parsing by OpenXMLParserExecutor.
    W_DOC_NS = f'xmlns:w="{W_NS}"'
    cleaned_parts = [p.original_xml for p in all_paragraphs]
    inner = "\n".join(cleaned_parts)
    cleaned_xml = (
        f'<w:document {W_DOC_NS}><w:body>{inner}</w:body></w:document>'
    )

    return DocumentCheckRequest(paragraphs=all_paragraphs, cleaned_xml=cleaned_xml)


def extract_docx_xml(docx_path: str) -> str:
    """Extract ``word/document.xml`` from a ``.docx`` file (convenience helper)."""
    import zipfile

    with zipfile.ZipFile(docx_path, "r") as zf:
        return zf.read("word/document.xml").decode("utf-8")


def extract_docx_styles(docx_path: str) -> StyleMap:
    """Extract style definitions from a ``.docx`` file."""
    import zipfile

    with zipfile.ZipFile(docx_path, "r") as zf:
        if "word/styles.xml" in zf.namelist():
            styles_xml = zf.read("word/styles.xml").decode("utf-8")
            return extract_styles_from_xml(styles_xml)
    return {}


# ── paragraph parsing ──────────────────────────────────────────────────

def _parse_paragraph(
    p_elem: ET.Element,
    p_index: int,
    style_map: StyleMap | None = None,
) -> ParsedParagraph:
    # paragraph style
    style: str | None = None
    style_name: str | None = None
    style_fmt: FormattingFlags | None = None
    pPr = p_elem.find(_t("pPr"))
    if pPr is not None:
        pStyle = pPr.find(_t("pStyle"))
        if pStyle is not None:
            style = pStyle.get(f"{_W}val") or pStyle.get("val")

    # Resolve style name and inherited formatting from style_map
    if style and style_map and style in style_map:
        sinfo = style_map[style]
        style_name = sinfo.name or style
        style_fmt = sinfo.base_formatting

    # runs — merge style-inherited formatting
    runs: list[TextRun] = []
    for r_idx, r_elem in enumerate(p_elem.findall(_t("r"))):
        text = _run_text(r_elem)
        if not text:
            continue
        explicit_fmt = _parse_run_formatting(r_elem)

        # Also check for rStyle-level formatting
        rPr = r_elem.find(_t("rPr"))
        if rPr is not None and style_map:
            rStyle = rPr.find(_t("rStyle"))
            if rStyle is not None:
                rs_id = rStyle.get(f"{_W}val") or rStyle.get("val") or ""
                if rs_id in style_map:
                    rs_info = style_map[rs_id]
                    explicit_fmt = _merge_style_formatting(
                        explicit_fmt, rs_info.base_formatting
                    )

        # Merge paragraph-style inherited formatting
        if style_fmt is not None:
            fmt = _merge_style_formatting(explicit_fmt, style_fmt)
        else:
            fmt = explicit_fmt

        runs.append(TextRun(text=text, formatting=fmt, run_index=r_idx))

    plain_text = "".join(r.text for r in runs)
    fmt_summary = _build_formatting_summary(runs)
    original_xml = _clean_element(p_elem)

    return ParsedParagraph(
        paragraph_index=p_index,
        style=style,
        style_name=style_name,
        runs=runs,
        plain_text=plain_text,
        formatting_summary=fmt_summary,
        original_xml=original_xml,
    )


def _run_text(r_elem: ET.Element) -> str:
    parts: list[str] = []
    for t_elem in r_elem.findall(_t("t")):
        if t_elem.text:
            parts.append(t_elem.text)
    return "".join(parts)


# ── run formatting ─────────────────────────────────────────────────────

def _parse_run_formatting(r_elem: ET.Element) -> FormattingFlags:
    rPr = r_elem.find(_t("rPr"))
    if rPr is None:
        return FormattingFlags()

    return FormattingFlags(
        bold=_has_tag(rPr, "b"),
        italic=_has_tag(rPr, "i"),
        underline=_has_tag(rPr, "u"),
        strikethrough=_has_tag(rPr, "strike"),
        superscript=_vert_align(rPr, "superscript"),
        subscript=_vert_align(rPr, "subscript"),
        all_caps=_has_tag(rPr, "caps"),
        small_caps=_has_tag(rPr, "smallCaps"),
        font_name=_attr(rPr, "rFonts", "ascii"),
        font_size_half_pts=_int_attr(rPr, "sz", "val"),
        font_color=_attr(rPr, "color", "val"),
        highlight_color=_attr(rPr, "highlight", "val"),
    )


def _has_tag(rPr: ET.Element, local: str) -> bool:
    elem = rPr.find(_t(local))
    if elem is None:
        return False
    # <w:b/> means on;  <w:b w:val="false"/> means off
    val = elem.get(f"{_W}val") or elem.get("val")
    return val is None or val.lower() not in ("false", "0", "off")


def _vert_align(rPr: ET.Element, value: str) -> bool:
    va = rPr.find(_t("vertAlign"))
    if va is None:
        return False
    return (va.get(f"{_W}val") or va.get("val", "")) == value


def _attr(rPr: ET.Element, tag_local: str, attr_local: str) -> str | None:
    elem = rPr.find(_t(tag_local))
    if elem is None:
        return None
    return elem.get(f"{_W}{attr_local}") or elem.get(attr_local)


def _int_attr(rPr: ET.Element, tag_local: str, attr_local: str) -> int | None:
    v = _attr(rPr, tag_local, attr_local)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


# ── formatting summary ─────────────────────────────────────────────────

def _build_formatting_summary(runs: Sequence[TextRun]) -> str:
    flags: set[str] = set()
    for r in runs:
        flags.update(r.formatting.summary().split("+"))
    flags.discard("plain")
    return "+".join(sorted(flags)) if flags else "plain"


# ── XML cleaning ───────────────────────────────────────────────────────

_RSID_RE = re.compile(r'\s+\w+:rsid\w*="[^"]*"', re.IGNORECASE)
_W14_RE = re.compile(r'\s+w14:\w+="[^"]*"')
_W15_RE = re.compile(r'\s+w15:\w+="[^"]*"')
_MC_RE = re.compile(r'\s+mc:\w+="[^"]*"')
_XMLSPACE_RE = re.compile(r'\s+xml:space="[^"]*"')


def _clean_xml(xml_content: str) -> str:
    """Strip revision / tracking attributes to save tokens."""
    out = _RSID_RE.sub("", xml_content)
    out = _W14_RE.sub("", out)
    out = _W15_RE.sub("", out)
    out = _MC_RE.sub("", out)
    out = _XMLSPACE_RE.sub("", out)
    return re.sub(r">\s+<", "><", out)


def _clean_element(elem: ET.Element) -> str:
    raw = ET.tostring(elem, encoding="unicode")
    return _clean_xml(raw)


# ── highlighting helper (used by aggregator) ───────────────────────────

def highlight_violated_runs(
    paragraph_xml: str,
    run_indices: Sequence[int],
    color: str = "yellow",
) -> str:
    """Return a copy of *paragraph_xml* with ``<w:highlight>`` injected
    into the ``<w:rPr>`` of each violated run.

    Parameters
    ----------
    paragraph_xml:
        Cleaned XML of a single ``<w:p>`` element.
    run_indices:
        0-based indices of ``<w:r>`` children to highlight.
    color:
        OpenXML highlight colour name (default ``yellow``).
    """
    if not run_indices:
        return paragraph_xml

    try:
        p_elem = ET.fromstring(paragraph_xml)
    except ET.ParseError:
        return paragraph_xml

    runs = p_elem.findall(_t("r"))
    for idx in run_indices:
        if 0 <= idx < len(runs):
            r = runs[idx]
            rPr = r.find(_t("rPr"))
            if rPr is None:
                rPr = ET.SubElement(r, _t("rPr"))
                # rPr should be first child of <w:r>
                r.remove(rPr)
                r.insert(0, rPr)
            # remove existing highlight if any
            old_hl = rPr.find(_t("highlight"))
            if old_hl is not None:
                rPr.remove(old_hl)
            hl = ET.SubElement(rPr, _t("highlight"))
            hl.set(f"{_W}val", color)

    return ET.tostring(p_elem, encoding="unicode")
