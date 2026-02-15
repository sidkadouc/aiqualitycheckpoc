"""
STEP 5 — Extraction des règles normatives.

Deux modes disponibles (contrôlés par USE_LLM_EXTRACTION) :

  • Déterministe (défaut) — Construit les règles directement depuis la
    hiérarchie du document (sections, titres, Do/Don't) et le contenu
    des chunks. Aucun appel LLM, 0 $, instantané, reproductible.

  • LLM (USE_LLM_EXTRACTION=true) — Envoie chaque batch au GPT-4.1 pour
    classifier sévérité, extraire mots-clés, produire un résumé.
    Plus riche mais coûteux (~2-5 $ / 150 pages) et sujet au rate limiting.

Dans les deux cas, la sortie est un PolicyRuleSet identique, stocké
dans 05_extracted_rules.json puis indexé dans Cosmos DB / AI Search.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from openai import AzureOpenAI

from .config import AzureConfig, PipelineConfig
from .models import (
    Chunk,
    PolicyRule,
    PolicyRuleSet,
    ReferenceEntry,
    ReferenceTable,
    RuleSeverity,
    RuleType,
    SectionSummary,
)

logger = logging.getLogger(__name__)

# Unified system prompt: extracts both rules AND reference tables from any section
_SYSTEM_PROMPT = """Tu es un expert en analyse réglementaire et normative.
Tu analyses des extraits de documents officiels (publications OCDE, politiques internes, normes).

Ton rôle est d'extraire DEUX types de contenu de chaque extrait :

═══════════════════════════════════════
A) RÈGLES NORMATIVES
═══════════════════════════════════════

Une règle normative est un énoncé qui :
- Impose une obligation (DOIT, SHALL, MUST, est tenu de, il faut)
- Formule une recommandation (DEVRAIT, SHOULD, il est recommandé)
- Autorise une action (PEUT, MAY, est autorisé)
- Interdit une action (NE DOIT PAS, SHALL NOT, il est interdit)
- Définit une norme de qualité ou un standard à respecter

Pour chaque règle trouvée :
- "rule_text": le texte exact de la règle tel qu'il apparaît dans le document.
  EXCEPTION: pour les règles "dont", le rule_text DOIT commencer par "Don't: " suivi du texte original.
- "rule_summary": un résumé clair et concis (1-2 phrases)
- "severity": un parmi ["mandatory", "recommended", "optional", "informational"]
  - mandatory = obligation stricte (DOIT, SHALL, MUST)
  - recommended = recommandation forte (DEVRAIT, SHOULD)
  - optional = permission (PEUT, MAY)
  - informational = contexte ou définition normative
- "rule_type": un parmi ["do", "dont", "unspecified"]
  - do = la règle se trouve dans une section "Do" ou marquée ✓
  - dont = la règle se trouve dans une section "Don't" ou marquée ✗/x
  - unspecified = ni "Do" ni "Don't" explicite
- "keywords": liste de 3-5 mots-clés pertinents

═══════════════════════════════════════
B) TABLEAUX DE RÉFÉRENCE
═══════════════════════════════════════

Un tableau de référence est un contenu tabulaire NON normatif :
- Listes d'abréviations avec leurs formes complètes
- Glossaires ou définitions de termes
- Tables de correspondance (symboles, signes, keystroke, etc.)
- Exemples de formatage ou de citation (ex: "In-text citations", "Citing reports")
- Listes de titres contractés (Dr, Prof, Mr, Ms, etc.)
- Tables comparatives (ex: différences entre anglais et français)
- Tout contenu structuré clé-valeur qui sert de RÉFÉRENCE plutôt que de RÈGLE

Pour chaque tableau trouvé :
- "category": le nom/titre descriptif du tableau
- "entries": liste d'objets avec :
  - "key": la clé, le terme ou l'en-tête de ligne
  - "value": la valeur, la définition ou le contenu correspondant
  - "note": information supplémentaire optionnelle

═══════════════════════════════════════
INSTRUCTIONS IMPORTANTES
═══════════════════════════════════════

- Un même extrait peut contenir À LA FOIS des règles ET des tableaux de référence
- Extrais TOUTES les règles ET TOUS les tableaux
- Ne rajoute pas de contenu qui n'est pas dans le texte
- Si un type n'est pas présent, retourne un tableau vide pour ce type
- Réponds en JSON uniquement

Format de réponse attendu:
{
  "rules": [
    {
      "rule_text": "...",
      "rule_summary": "...",
      "severity": "mandatory|recommended|optional|informational",
      "rule_type": "do|dont|unspecified",
      "keywords": ["...", "...", "..."]
    }
  ],
  "tables": [
    {
      "category": "...",
      "entries": [
        { "key": "...", "value": "...", "note": "" }
      ]
    }
  ]
}"""


def _get_openai_client(azure_cfg: AzureConfig) -> AzureOpenAI:
    """Create Azure OpenAI client."""
    if not azure_cfg.openai_endpoint:
        raise ValueError("AZURE_OPENAI_ENDPOINT must be set.")

    return AzureOpenAI(
        azure_endpoint=azure_cfg.openai_endpoint,
        api_key=azure_cfg.openai_key or None,
        api_version="2025-01-01-preview",
        # Uses DefaultAzureCredential if no key
        azure_ad_token_provider=(
            None
            if azure_cfg.openai_key
            else _get_token_provider()
        ),
    )


def _get_token_provider():
    """Get Azure AD token provider for managed identity auth."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    credential = DefaultAzureCredential()
    return get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )


# ═════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═════════════════════════════════════════════════════════════════════════════

def _detect_rule_type_from_section(section_title: str) -> RuleType:
    """Detect rule_type from the section title hierarchy."""
    parts = [p.strip().lower() for p in section_title.split(">")]
    for part in reversed(parts):
        if part in ("do", "do's"):
            return RuleType.DO
        if part in ("don't", "dont", "don'ts"):
            return RuleType.DONT
    for part in parts:
        if part in ("do", "do's"):
            return RuleType.DO
        if part in ("don't", "dont", "don'ts"):
            return RuleType.DONT
    return RuleType.UNSPECIFIED


def _detect_severity_from_text(text: str) -> RuleSeverity:
    """Heuristic severity detection from keywords in text."""
    t = text.lower()
    # Mandatory indicators
    mandatory_kw = (
        "must ", "must.", "shall ", "shall.", "always ", "never ",
        "is required", "are required", "obligatoire", "doit ",
        "il faut", "do not ", "don't ", "ne doit pas", "ne pas ",
        "interdit", "forbidden", "shall not",
    )
    for kw in mandatory_kw:
        if kw in t:
            return RuleSeverity.MANDATORY
    # Recommended indicators
    recommended_kw = (
        "should ", "should.", "devrait", "il est recommandé",
        "it is recommended", "preferably", "prefer ", "avoid ",
        "better to", "it is best", "try to",
    )
    for kw in recommended_kw:
        if kw in t:
            return RuleSeverity.RECOMMENDED
    # Optional indicators
    optional_kw = ("may ", "may.", "peut ", "can ", "is allowed", "optional")
    for kw in optional_kw:
        if kw in t:
            return RuleSeverity.OPTIONAL
    return RuleSeverity.INFORMATIONAL


def _extract_keywords_from_text(text: str, max_keywords: int = 5) -> list[str]:
    """Extract keywords from text using simple heuristics (no LLM)."""
    import re

    # Collect quoted terms and terms in bold markers
    quoted = re.findall(r'["\u201c\u201d]([^"\u201c\u201d]{2,40})["\u201c\u201d]', text)
    # Words fully in uppercase (likely acronyms or emphasis)
    upper = [w for w in text.split() if w.isupper() and 2 <= len(w) <= 12 and w.isalpha()]

    keywords: list[str] = []
    seen: set[str] = set()
    for w in quoted + upper:
        w_clean = w.strip().lower()
        if w_clean and w_clean not in seen:
            seen.add(w_clean)
            keywords.append(w.strip())
        if len(keywords) >= max_keywords:
            break
    return keywords


def _deduplicate_rules(rules: list[PolicyRule]) -> list[PolicyRule]:
    """Remove near-duplicate rules based on text similarity."""
    if not rules:
        return rules
    unique: list[PolicyRule] = []
    seen_texts: set[str] = set()
    for rule in rules:
        normalized = rule.rule_text.lower().strip()
        fingerprint = normalized[:100]
        if fingerprint not in seen_texts:
            seen_texts.add(fingerprint)
            unique.append(rule)
    removed = len(rules) - len(unique)
    if removed > 0:
        logger.info("Deduplicated %d rules (%d removed)", len(rules), removed)
    return unique


def _build_section_summaries(
    all_rules: list[PolicyRule],
    all_tables: list[ReferenceTable],
    section_meta: dict[str, tuple[str, str]],  # section_id -> (section_title, parent)
) -> list[SectionSummary]:
    """Build section summaries from collected rules and tables."""
    summaries: dict[str, SectionSummary] = {}
    for sid, (title, parent) in section_meta.items():
        summaries[sid] = SectionSummary(
            section_id=sid,
            section_title=title,
            parent_section=parent,
            content_types=[],
        )
    for rule in all_rules:
        if rule.section_id in summaries:
            summaries[rule.section_id].rule_count += 1
            ct = f"rules:{rule.rule_type.value}"
            if ct not in summaries[rule.section_id].content_types:
                summaries[rule.section_id].content_types.append(ct)
    for tbl in all_tables:
        if tbl.section_id in summaries:
            summaries[tbl.section_id].reference_table_count += 1
            if "reference_table" not in summaries[tbl.section_id].content_types:
                summaries[tbl.section_id].content_types.append("reference_table")
    return list(summaries.values())


def _save_ruleset(
    rule_set: PolicyRuleSet, pipeline_cfg: PipelineConfig
) -> None:
    """Save the rule set to disk if configured."""
    if pipeline_cfg.save_intermediate_files:
        output_dir = Path(pipeline_cfg.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "05_extracted_rules.json"
        path.write_text(rule_set.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Extracted rules saved to: %s", path)


# ═════════════════════════════════════════════════════════════════════════════
# MODE A — Deterministic builder (no LLM)
# ═════════════════════════════════════════════════════════════════════════════

def _extract_rules_deterministic(
    chunks: list[Chunk],
    pipeline_cfg: PipelineConfig,
) -> PolicyRuleSet:
    """
    Build rules directly from chunks + section hierarchy. No LLM.

    Each chunk becomes one PolicyRule. The rule_type (do/dont/unspecified)
    comes from the section title hierarchy. Severity is detected via keyword
    heuristics. The rule_text is the verbatim chunk content.
    """
    logger.info("[deterministic] Building rules from %d chunks (no LLM)", len(chunks))

    all_rules: list[PolicyRule] = []
    all_tables: list[ReferenceTable] = []
    section_meta: dict[str, tuple[str, str]] = {}  # sid -> (title, parent)
    rule_counter = 0

    for chunk in chunks:
        sid = chunk.section_id
        title = chunk.section_title
        if sid not in section_meta:
            parts = [p.strip() for p in title.split(">")]
            parent = " > ".join(parts[:-1]) if len(parts) > 1 else ""
            section_meta[sid] = (title, parent)

        rule_type = _detect_rule_type_from_section(title)
        severity = _detect_severity_from_text(chunk.content)
        keywords = _extract_keywords_from_text(chunk.content)

        rule_counter += 1
        rule_text = chunk.content.strip()
        if rule_type == RuleType.DONT and not rule_text.lower().startswith("don't"):
            rule_text = f"Don't: {rule_text}"

        rule = PolicyRule(
            rule_id=f"rule_{rule_counter}",
            section_id=sid,
            section_title=title,
            rule_text=rule_text,
            rule_summary="",  # no LLM summary in deterministic mode
            severity=severity,
            rule_type=rule_type,
            keywords=keywords,
            page=chunk.page_start,
            source_chunk_id=chunk.chunk_id,
        )
        all_rules.append(rule)

    all_rules = _deduplicate_rules(all_rules)
    sections = _build_section_summaries(all_rules, all_tables, section_meta)

    rule_set = PolicyRuleSet(
        document_name=chunks[0].section_title.split(" > ")[0] if chunks else "unknown",
        total_rules=len(all_rules),
        total_reference_tables=len(all_tables),
        sections=sections,
        rules=all_rules,
        reference_tables=all_tables,
    )

    logger.info(
        "[deterministic] Done: %d rules (%d mandatory, %d recommended, %d optional, %d info), "
        "rule types: %d do / %d dont / %d unspecified",
        len(all_rules),
        sum(1 for r in all_rules if r.severity == RuleSeverity.MANDATORY),
        sum(1 for r in all_rules if r.severity == RuleSeverity.RECOMMENDED),
        sum(1 for r in all_rules if r.severity == RuleSeverity.OPTIONAL),
        sum(1 for r in all_rules if r.severity == RuleSeverity.INFORMATIONAL),
        sum(1 for r in all_rules if r.rule_type == RuleType.DO),
        sum(1 for r in all_rules if r.rule_type == RuleType.DONT),
        sum(1 for r in all_rules if r.rule_type == RuleType.UNSPECIFIED),
    )

    _save_ruleset(rule_set, pipeline_cfg)
    return rule_set


# ═════════════════════════════════════════════════════════════════════════════
# MODE B — LLM-based extraction
# ═════════════════════════════════════════════════════════════════════════════

def _extract_rules_llm(
    chunks: list[Chunk],
    azure_cfg: AzureConfig,
    pipeline_cfg: PipelineConfig,
) -> PolicyRuleSet:
    """
    Extract normative rules and reference tables via LLM (sequential calls).
    Activated when USE_LLM_EXTRACTION=true.
    """
    logger.info("[llm] Starting LLM-based rule extraction from %d chunks", len(chunks))

    client = _get_openai_client(azure_cfg)
    deployment = pipeline_cfg.rule_extraction_model
    batch_size = pipeline_cfg.rule_extraction_batch_size

    section_groups: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        key = chunk.section_id
        if key not in section_groups:
            section_groups[key] = []
        section_groups[key].append(chunk)

    all_rules: list[PolicyRule] = []
    all_tables: list[ReferenceTable] = []
    section_meta: dict[str, tuple[str, str]] = {}
    rule_counter = 0
    table_counter = 0

    total_groups = len(section_groups)
    processed = 0

    for section_id, section_chunks in section_groups.items():
        section_title = section_chunks[0].section_title
        detected_rule_type = _detect_rule_type_from_section(section_title)

        parts = [p.strip() for p in section_title.split(">")]
        parent = " > ".join(parts[:-1]) if len(parts) > 1 else ""
        section_meta[section_id] = (section_title, parent)

        for i in range(0, len(section_chunks), batch_size):
            batch = section_chunks[i : i + batch_size]
            combined_text = "\n\n---\n\n".join(
                f"[Section: {c.section_title}]\n{c.content}" for c in batch
            )

            try:
                response = client.chat.completions.create(
                    model=deployment,
                    temperature=pipeline_cfg.rule_extraction_temperature,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                "Analyse cet extrait et extrais toutes les règles normatives "
                                "ET tous les tableaux de référence :\n\n" + combined_text
                            ),
                        },
                    ],
                )

                raw_json = response.choices[0].message.content
                parsed = json.loads(raw_json)

                # --- Reference tables ---
                for tbl_data in parsed.get("tables", []):
                    table_counter += 1
                    entries = [
                        ReferenceEntry(
                            key=e.get("key", ""),
                            value=e.get("value", ""),
                            note=e.get("note", ""),
                        )
                        for e in tbl_data.get("entries", [])
                    ]
                    if entries:
                        all_tables.append(ReferenceTable(
                            table_id=f"table_{table_counter}",
                            section_id=section_id,
                            section_title=section_title,
                            category=tbl_data.get("category", section_title.split(" > ")[-1]),
                            entries=entries,
                            page=batch[0].page_start,
                            source_chunk_id=batch[0].chunk_id,
                        ))

                # --- Rules ---
                for rule_data in parsed.get("rules", []):
                    rule_counter += 1
                    severity = rule_data.get("severity", "informational")
                    try:
                        severity_enum = RuleSeverity(severity)
                    except ValueError:
                        severity_enum = RuleSeverity.INFORMATIONAL

                    raw_rt = rule_data.get("rule_type", "")
                    try:
                        rule_type_enum = RuleType(raw_rt)
                    except ValueError:
                        rule_type_enum = detected_rule_type
                    if rule_type_enum == RuleType.UNSPECIFIED and detected_rule_type != RuleType.UNSPECIFIED:
                        rule_type_enum = detected_rule_type

                    all_rules.append(PolicyRule(
                        rule_id=f"rule_{rule_counter}",
                        section_id=section_id,
                        section_title=section_title,
                        rule_text=rule_data.get("rule_text", ""),
                        rule_summary=rule_data.get("rule_summary", ""),
                        severity=severity_enum,
                        rule_type=rule_type_enum,
                        keywords=rule_data.get("keywords", []),
                        page=batch[0].page_start,
                        source_chunk_id=batch[0].chunk_id,
                    ))

            except Exception as e:
                logger.error("Error in section %s batch %d: %s", section_id, i, str(e))
                continue

        processed += 1
        if processed % 5 == 0 or processed == total_groups:
            logger.info("[llm] Processed %d/%d section groups", processed, total_groups)

    all_rules = _deduplicate_rules(all_rules)
    sections = _build_section_summaries(all_rules, all_tables, section_meta)

    rule_set = PolicyRuleSet(
        document_name=chunks[0].section_title.split(" > ")[0] if chunks else "unknown",
        total_rules=len(all_rules),
        total_reference_tables=len(all_tables),
        sections=sections,
        rules=all_rules,
        reference_tables=all_tables,
    )

    logger.info(
        "[llm] Done: %d rules (%d mandatory, %d recommended, %d optional), "
        "%d tables, types: %d do / %d dont / %d unspecified",
        len(all_rules),
        sum(1 for r in all_rules if r.severity == RuleSeverity.MANDATORY),
        sum(1 for r in all_rules if r.severity == RuleSeverity.RECOMMENDED),
        sum(1 for r in all_rules if r.severity == RuleSeverity.OPTIONAL),
        len(all_tables),
        sum(1 for r in all_rules if r.rule_type == RuleType.DO),
        sum(1 for r in all_rules if r.rule_type == RuleType.DONT),
        sum(1 for r in all_rules if r.rule_type == RuleType.UNSPECIFIED),
    )

    _save_ruleset(rule_set, pipeline_cfg)
    return rule_set


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point — dispatcher
# ═════════════════════════════════════════════════════════════════════════════

def extract_rules(
    chunks: list[Chunk],
    azure_cfg: AzureConfig,
    pipeline_cfg: PipelineConfig,
) -> PolicyRuleSet:
    """
    Extract rules from chunks.

    Dispatches to deterministic (default) or LLM mode based on
    ``pipeline_cfg.use_llm_extraction`` (env: USE_LLM_EXTRACTION).
    """
    if pipeline_cfg.use_llm_extraction:
        logger.info("USE_LLM_EXTRACTION=true → using LLM-based extraction")
        return _extract_rules_llm(chunks, azure_cfg, pipeline_cfg)

    logger.info("USE_LLM_EXTRACTION=false → using deterministic extraction (no LLM)")
    return _extract_rules_deterministic(chunks, pipeline_cfg)
