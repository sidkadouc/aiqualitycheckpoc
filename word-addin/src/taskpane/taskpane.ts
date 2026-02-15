/**
 * OECD Style Checker — Task Pane entry point.
 *
 * Strategy for "near-real-time IntelliSense-like" behaviour:
 *  1. When real-time mode is ON, we listen for `Document.SelectionChanged`.
 *  2. Each time the cursor moves to a new paragraph (debounced 2 s), we:
 *     - Extract that paragraph's OOXML via Office.js
 *     - POST it to /api/check-paragraph
 *     - Render findings in the side panel & highlight in the document
 *  3. The user can also click "Check Document" for a full scan.
 *
 * Typical latency: 2-5 s per paragraph (LLM round-trip).  Not true
 * IntelliSense, but fast enough for a "style advisor" UX.
 */

import "./taskpane.css";
import { AddinFinding, AddinParagraphGroup, AddinResponse, CheckRequest } from "../models/types";
import { checkDocument, checkParagraph, healthCheck } from "../services/qualityService";
import {
  applyFix,
  clearAllHighlights,
  clearHighlight,
  getAllParagraphsOoxml,
  getCurrentParagraphOoxml,
  getSelectedParagraphsOoxml,
  highlightFinding,
  navigateToFinding,
} from "../services/documentService";

/* global Office, Word */

// ── State ─────────────────────────────────────────────────────────────

let currentResponse: AddinResponse | null = null;
let activeFindingId: string | null = null;
let realtimeEnabled = true;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let lastCheckedParagraphText = "";

/** Sequential display number for each finding (finding.id → 1-based #N). */
let findingNumbers: Map<string, number> = new Map();
/** Finding IDs invalidated because a sibling fix changed the shared text. */
let staleFindingIds: Set<string> = new Set();

const DEBOUNCE_MS = 2000; // 2 seconds after cursor stops moving

// ── DOM refs ──────────────────────────────────────────────────────────

const $statusBadge = () => document.getElementById("status-badge")!;
const $loading = () => document.getElementById("loading")!;
const $findingsContainer = () => document.getElementById("findings-container")!;
const $summaryBar = () => document.getElementById("summary-bar")!;
const $summaryTotal = () => document.getElementById("summary-total")!;
const $summaryMandatory = () => document.getElementById("summary-mandatory")!;
const $summaryRecommended = () => document.getElementById("summary-recommended")!;

// ── Initialisation ────────────────────────────────────────────────────

Office.onReady(async (info) => {
  if (info.host !== Office.HostType.Word) return;

  // Wire up buttons
  document.getElementById("btn-check-selection")!.addEventListener("click", onCheckSelection);
  document.getElementById("btn-check-all")!.addEventListener("click", onCheckDocument);
  document.getElementById("btn-clear")!.addEventListener("click", onClear);
  document.getElementById("chk-realtime")!.addEventListener("change", onRealtimeToggle);

  // Check API health
  try {
    const h = await healthCheck();
    setStatus("done", `${h.rules_loaded} rules`);
  } catch {
    setStatus("error", "API offline");
  }

  // Register selection change handler for real-time mode
  Office.context.document.addHandlerAsync(
    Office.EventType.DocumentSelectionChanged,
    onSelectionChanged
  );
});

// ── Status badge ──────────────────────────────────────────────────────

function setStatus(state: "idle" | "checking" | "done" | "error", text?: string) {
  const badge = $statusBadge();
  badge.className = `badge badge-${state}`;
  badge.textContent = text || state.charAt(0).toUpperCase() + state.slice(1);
}

function showLoading(show: boolean) {
  $loading().classList.toggle("hidden", !show);
}

// ── Real-time mode ────────────────────────────────────────────────────

function onRealtimeToggle() {
  realtimeEnabled = (document.getElementById("chk-realtime") as HTMLInputElement).checked;
}

function onSelectionChanged() {
  if (!realtimeEnabled) return;

  // Debounce: wait DEBOUNCE_MS after the last cursor move
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => checkCurrentParagraph(), DEBOUNCE_MS);
}

async function checkCurrentParagraph() {
  try {
    const para = await getCurrentParagraphOoxml();

    // Skip if same paragraph as last check (avoid redundant calls)
    if (para.text === lastCheckedParagraphText) return;
    lastCheckedParagraphText = para.text;

    // Skip empty paragraphs
    if (!para.text.trim()) return;

    setStatus("checking", "Checking...");

    const response = await checkParagraph({
      docParagraphIndex: para.index,
      ooxml: para.ooxml,
      textPreview: para.text.substring(0, 100),
    });

    // Merge with existing results: replace findings for this paragraph
    mergeResponse(response, para.index);
    renderFindings();
    await applyHighlights(response);

    const total = currentResponse?.total_findings ?? 0;
    setStatus("done", total > 0 ? `${total} finding${total > 1 ? "s" : ""}` : "Clean");
  } catch (err) {
    console.error("Real-time check failed:", err);
    setStatus("error", "Check failed");
  }
}

// ── Check selected paragraphs ─────────────────────────────────────────

async function onCheckSelection() {
  try {
    showLoading(true);
    setStatus("checking", "Checking selection...");

    const paragraphs = await getSelectedParagraphsOoxml();

    if (paragraphs.length === 0) {
      setStatus("idle", "No text selected");
      return;
    }

    const req: CheckRequest = {
      documentInfo: {
        totalParagraphsInDoc: paragraphs.length,
        selectedParagraphs: paragraphs.length,
        timestamp: new Date().toISOString(),
      },
      paragraphs: paragraphs.map((p, i) => ({
        docParagraphIndex: p.index,
        selectionIndex: i + 1,
        textPreview: p.text.substring(0, 100),
        ooxmlLength: p.ooxml.length,
        ooxml: p.ooxml,
      })),
    };

    const response = await checkDocument(req);

    // Merge selection results with existing findings (don't replace all)
    for (const group of response.paragraphs) {
      mergeResponse(response, group.doc_paragraph_index);
    }
    if (!currentResponse) currentResponse = response;

    renderFindings();
    await applyHighlights(response);

    setStatus(
      "done",
      `${response.total_findings} finding${response.total_findings !== 1 ? "s" : ""} in ${paragraphs.length} para${paragraphs.length !== 1 ? "s" : ""}`
    );
  } catch (err) {
    console.error("Selection check failed:", err);
    setStatus("error", "Check failed");
  } finally {
    showLoading(false);
  }
}

// ── Full document check ───────────────────────────────────────────────

async function onCheckDocument() {
  try {
    showLoading(true);
    setStatus("checking", "Scanning...");

    const paragraphs = await getAllParagraphsOoxml();

    const req: CheckRequest = {
      documentInfo: {
        totalParagraphsInDoc: paragraphs.length,
        selectedParagraphs: paragraphs.length,
        timestamp: new Date().toISOString(),
      },
      paragraphs: paragraphs.map((p, i) => ({
        docParagraphIndex: p.index,
        selectionIndex: i + 1,
        textPreview: p.text.substring(0, 100),
        ooxmlLength: p.ooxml.length,
        ooxml: p.ooxml,
      })),
    };

    const response = await checkDocument(req);
    currentResponse = response;
    renderFindings();
    await applyHighlights(response);

    setStatus(
      "done",
      `${response.total_findings} finding${response.total_findings !== 1 ? "s" : ""}`
    );
  } catch (err) {
    console.error("Full check failed:", err);
    setStatus("error", "Check failed");
  } finally {
    showLoading(false);
  }
}

// ── Clear ─────────────────────────────────────────────────────────────

async function onClear() {
  currentResponse = null;
  activeFindingId = null;
  lastCheckedParagraphText = "";
  staleFindingIds.clear();

  renderFindings();
  await clearAllHighlights();
  setStatus("idle");
}

// ── Merge real-time results ───────────────────────────────────────────

function mergeResponse(newResponse: AddinResponse, paragraphIndex: number) {
  if (!currentResponse) {
    currentResponse = newResponse;
    return;
  }

  // Clear stale markers for the paragraph being re-checked
  for (const g of currentResponse.paragraphs) {
    if (g.doc_paragraph_index === paragraphIndex) {
      for (const f of g.findings) staleFindingIds.delete(f.id);
    }
  }

  // Remove old findings for this paragraph
  currentResponse.paragraphs = currentResponse.paragraphs.filter(
    (g) => g.doc_paragraph_index !== paragraphIndex
  );

  // Add new findings for this paragraph
  for (const group of newResponse.paragraphs) {
    currentResponse.paragraphs.push(group);
  }

  // Sort by paragraph index
  currentResponse.paragraphs.sort((a, b) => a.doc_paragraph_index - b.doc_paragraph_index);

  // Recount
  currentResponse.total_findings = currentResponse.paragraphs.reduce(
    (sum, g) => sum + g.finding_count,
    0
  );

  // Rebuild summary
  const summary: Record<string, number> = {};
  for (const g of currentResponse.paragraphs) {
    for (const f of g.findings) {
      summary[f.severity] = (summary[f.severity] || 0) + 1;
    }
  }
  currentResponse.summary = summary;
}

// ── Numbering & grouping helpers ──────────────────────────────────────

/** Assign sequential 1-based numbers to every finding for display. */
function assignFindingNumbers(): void {
  findingNumbers.clear();
  if (!currentResponse) return;
  let n = 1;
  for (const group of currentResponse.paragraphs) {
    for (const f of group.findings) {
      findingNumbers.set(f.id, n++);
    }
  }
}

/** Cluster findings that flag the same text so they render as one group. */
function groupBySearchText(
  findings: AddinFinding[]
): Array<{ searchText: string; items: AddinFinding[] }> {
  const map = new Map<string, AddinFinding[]>();
  for (const f of findings) {
    const key = f.search_text || f.id;
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(f);
  }
  return Array.from(map.entries()).map(([searchText, items]) => ({
    searchText,
    items,
  }));
}

// ── Rendering ─────────────────────────────────────────────────────────

function renderFindings() {
  assignFindingNumbers();
  const container = $findingsContainer();
  const summaryBar = $summaryBar();

  if (!currentResponse || currentResponse.total_findings === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p>No findings yet.</p>
        <p class="hint">Click <strong>Check Selection</strong> or enable <strong>Real-time mode</strong> to start.</p>
      </div>
    `;
    summaryBar.classList.add("hidden");
    return;
  }

  summaryBar.classList.remove("hidden");

  // Summary
  $summaryTotal().textContent = `${currentResponse.total_findings} findings`;
  $summaryMandatory().textContent = `${currentResponse.summary["mandatory"] || 0} mandatory`;
  $summaryRecommended().textContent = `${currentResponse.summary["recommended"] || 0} recommended`;

  // Build findings HTML
  const html = currentResponse.paragraphs
    .map((group) => renderParagraphGroup(group))
    .join("");

  container.innerHTML = html;

  // Attach event listeners
  container.querySelectorAll("[data-finding-id]").forEach((card) => {
    const findingId = (card as HTMLElement).dataset.findingId!;
    const finding = findFinding(findingId);
    if (!finding) return;

    card.addEventListener("click", () => onFindingClick(finding, card as HTMLElement));

    const fixBtn = card.querySelector(".btn-fix");
    if (fixBtn) {
      fixBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        onFixClick(finding, card as HTMLElement);
      });
    }

    const dismissBtn = card.querySelector(".btn-dismiss");
    if (dismissBtn) {
      dismissBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        onDismissClick(finding, card as HTMLElement);
      });
    }

    // Read more toggle for long rule text
    const readMoreBtn = card.querySelector(".read-more-toggle");
    if (readMoreBtn) {
      readMoreBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const ruleTextEl = card.querySelector(".finding-rule-text") as HTMLElement;
        const expanded = ruleTextEl.classList.toggle("expanded");
        ruleTextEl.childNodes[0].textContent = expanded
          ? ruleTextEl.dataset.full! + " "
          : ruleTextEl.dataset.short! + " ";
        (readMoreBtn as HTMLElement).textContent = expanded ? "Show less" : "Read more";
      });
    }
  });
}

function renderParagraphGroup(group: AddinParagraphGroup): string {
  const textGroups = groupBySearchText(group.findings);
  const innerHtml = textGroups
    .map((tg) => {
      if (tg.items.length === 1) {
        return renderFindingCard(tg.items[0]);
      }
      return renderTextGroup(tg.searchText, tg.items);
    })
    .join("");

  return `
    <div class="paragraph-group">
      <div class="paragraph-header">
        Paragraph ${group.doc_paragraph_index + 1} — ${escapeHtml(group.text_preview)}
      </div>
      ${innerHtml}
    </div>
  `;
}

function renderTextGroup(searchText: string, findings: AddinFinding[]): string {
  const numbers = findings
    .map((f) => findingNumbers.get(f.id) || 0)
    .filter((n) => n > 0);
  const badges = numbers.map((n) => `<span class="finding-number">#${n}</span>`).join(" ");
  const preview =
    searchText.length > 60 ? searchText.slice(0, 60) + "…" : searchText;
  const cardsHtml = findings.map((f) => renderFindingCard(f)).join("");

  return `
    <div class="text-group">
      <div class="text-group-header">
        <div class="tg-badges">${badges}</div>
        <div class="tg-text">"${escapeHtml(preview)}"</div>
      </div>
      ${cardsHtml}
    </div>
  `;
}

function renderFindingCard(finding: AddinFinding): string {
  const isActive = finding.id === activeFindingId;
  const isStale = staleFindingIds.has(finding.id);
  const num = findingNumbers.get(finding.id) || 0;
  const TRUNCATE_LEN = 120;
  const ruleText = escapeHtml(finding.rule_text);
  const needsTruncation = finding.rule_text.length > TRUNCATE_LEN;
  const truncated = needsTruncation
    ? escapeHtml(finding.rule_text.slice(0, TRUNCATE_LEN)) + "…"
    : ruleText;

  // Build reference line (section + page)
  const refParts: string[] = [];
  if (finding.section_title) {
    refParts.push(`<span class="ref-section">§ ${escapeHtml(finding.section_title)}</span>`);
  }
  if (finding.page != null) {
    refParts.push(`<span class="ref-page">p.\u00A0${finding.page}</span>`);
  }
  const referenceHtml = refParts.length
    ? `<div class="finding-reference">${refParts.join('<span class="ref-sep"> · </span>')}</div>`
    : "";

  // Actions: disable fix button when stale (text changed by a sibling fix)
  let actionsHtml: string;
  if (isStale) {
    actionsHtml = `
      <div class="finding-actions">
        <span class="stale-notice">⚠ Text changed by another fix</span>
        <button class="btn-dismiss" title="Dismiss this finding">Dismiss</button>
      </div>`;
  } else {
    actionsHtml = `
      <div class="finding-actions">
        ${finding.fixable
          ? `<button class="btn-fix" title="Apply automatic fix">&#x2713; Fix</button>`
          : ""
        }
        <button class="btn-dismiss" title="Dismiss this finding">Dismiss</button>
      </div>`;
  }

  return `
    <div class="finding-card ${isActive ? "active" : ""} ${isStale ? "finding-stale" : ""}"
         data-finding-id="${finding.id}">
      <div class="finding-header">
        <span class="finding-number">#${num}</span>
        <span class="finding-rule">${escapeHtml(finding.rule_id)}</span>
        <span class="finding-severity ${finding.severity}">${finding.severity}</span>
      </div>
      ${referenceHtml}
      <div class="finding-rule-text" data-full="${ruleText}" data-short="${truncated}">
        ${truncated}
        ${needsTruncation ? `<button class="read-more-toggle" type="button">Read more</button>` : ""}
      </div>
      <div class="finding-explanation">${escapeHtml(finding.explanation)}</div>
      ${
        finding.suggestion
          ? `<div class="finding-suggestion">${escapeHtml(finding.suggestion)}</div>`
          : ""
      }
      <div class="finding-confidence">
        Confidence: ${Math.round(finding.confidence * 100)}%
      </div>
      ${actionsHtml}
    </div>
  `;
}

// ── Interactions ──────────────────────────────────────────────────────

async function onFindingClick(finding: AddinFinding, card: HTMLElement) {
  // Deselect previous
  document.querySelectorAll(".finding-card.active").forEach((el) => el.classList.remove("active"));

  activeFindingId = finding.id;
  card.classList.add("active");

  // Navigate to the text in document and highlight
  await navigateToFinding(finding);
  await highlightFinding(finding);
}

async function onFixClick(finding: AddinFinding, card: HTMLElement) {
  const applied = await applyFix(finding);
  if (applied) {
    // If the fix replaced text, sibling findings targeting the same
    // search_text can no longer be fixed reliably — mark them stale.
    if (finding.fix_type === "replace_text" && finding.search_text && currentResponse) {
      for (const group of currentResponse.paragraphs) {
        if (group.doc_paragraph_index !== finding.doc_paragraph_index) continue;
        for (const f of group.findings) {
          if (f.id !== finding.id && f.search_text === finding.search_text) {
            staleFindingIds.add(f.id);
          }
        }
      }
    }

    removeFinding(finding.id);
    renderFindings(); // re-render to show stale state on siblings
  }
}

async function onDismissClick(finding: AddinFinding, card: HTMLElement) {
  await clearHighlight(finding);
  card.remove();
  removeFinding(finding.id);
}

function removeFinding(findingId: string) {
  if (!currentResponse) return;

  for (const group of currentResponse.paragraphs) {
    group.findings = group.findings.filter((f) => f.id !== findingId);
    group.finding_count = group.findings.length;
  }

  // Remove empty groups
  currentResponse.paragraphs = currentResponse.paragraphs.filter((g) => g.finding_count > 0);
  currentResponse.total_findings = currentResponse.paragraphs.reduce(
    (sum, g) => sum + g.finding_count,
    0
  );

  // Update summary bar
  if (currentResponse.total_findings === 0) {
    renderFindings();
    setStatus("done", "Clean");
  } else {
    // Rebuild summary counts
    const summary: Record<string, number> = {};
    for (const g of currentResponse.paragraphs) {
      for (const f of g.findings) {
        summary[f.severity] = (summary[f.severity] || 0) + 1;
      }
    }
    currentResponse.summary = summary;
    $summaryTotal().textContent = `${currentResponse.total_findings} findings`;
    $summaryMandatory().textContent = `${summary["mandatory"] || 0} mandatory`;
    $summaryRecommended().textContent = `${summary["recommended"] || 0} recommended`;
  }
}

// ── Highlighting ──────────────────────────────────────────────────────

async function applyHighlights(response: AddinResponse) {
  for (const group of response.paragraphs) {
    for (const finding of group.findings) {
      try {
        await highlightFinding(finding);
      } catch (err) {
        console.warn(`Highlight failed for ${finding.id}:`, err);
      }
    }
  }
}

// ── Helpers ───────────────────────────────────────────────────────────

function findFinding(id: string): AddinFinding | undefined {
  if (!currentResponse) return undefined;
  for (const group of currentResponse.paragraphs) {
    for (const finding of group.findings) {
      if (finding.id === id) return finding;
    }
  }
  return undefined;
}

function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
