/**
 * Document interaction service — Office.js operations for:
 *  - Extracting paragraph OOXML
 *  - Highlighting text in the document
 *  - Applying automatic fixes
 *  - Navigating to a finding's paragraph
 */

import { AddinFinding } from "../models/types";

/* global Word */

// ── Paragraph extraction ──────────────────────────────────────────────

/**
 * Extract OOXML for all paragraphs in the document.
 */
export async function getAllParagraphsOoxml(): Promise<
  Array<{ index: number; text: string; ooxml: string }>
> {
  return Word.run(async (context) => {
    const body = context.document.body;
    const paragraphs = body.paragraphs;
    paragraphs.load("text");
    await context.sync();

    const results: Array<{ index: number; text: string; ooxml: string }> = [];

    for (let i = 0; i < paragraphs.items.length; i++) {
      const p = paragraphs.items[i];
      const ooxml = p.getOoxml();
      await context.sync();
      results.push({ index: i, text: p.text, ooxml: ooxml.value });
    }

    return results;
  });
}

/**
 * Extract OOXML for the paragraph at the current cursor position.
 */
export async function getCurrentParagraphOoxml(): Promise<{
  index: number;
  text: string;
  ooxml: string;
}> {
  return Word.run(async (context) => {
    const selection = context.document.getSelection();
    const paragraph = selection.paragraphs.getFirst();
    paragraph.load("text");
    const ooxml = paragraph.getOoxml();

    // Get the paragraph index by loading all paragraphs and finding the match
    const allParagraphs = context.document.body.paragraphs;
    allParagraphs.load("text");

    await context.sync();

    // Find the index of the current paragraph
    let idx = 0;
    for (let i = 0; i < allParagraphs.items.length; i++) {
      if (allParagraphs.items[i] === paragraph) {
        idx = i;
        break;
      }
    }

    // Fallback: match by text if reference comparison fails
    if (idx === 0 && allParagraphs.items.length > 1) {
      for (let i = 0; i < allParagraphs.items.length; i++) {
        if (allParagraphs.items[i].text === paragraph.text) {
          idx = i;
          break;
        }
      }
    }

    return { index: idx, text: paragraph.text, ooxml: ooxml.value };
  });
}

/**
 * Extract OOXML for paragraphs covered by the current selection.
 * If the user selected a range spanning multiple paragraphs, all of
 * them are returned.  Falls back to the single cursor paragraph.
 */
export async function getSelectedParagraphsOoxml(): Promise<
  Array<{ index: number; text: string; ooxml: string }>
> {
  return Word.run(async (context) => {
    const selection = context.document.getSelection();
    const selectedParagraphs = selection.paragraphs;
    selectedParagraphs.load("text");

    // Also load all body paragraphs so we can compute indices
    const allParagraphs = context.document.body.paragraphs;
    allParagraphs.load("text");

    await context.sync();

    // Build a lookup of text → first occurrence index from body paragraphs
    // (used as fallback when reference comparison fails)
    const textToIndex = new Map<string, number[]>();
    for (let i = 0; i < allParagraphs.items.length; i++) {
      const t = allParagraphs.items[i].text;
      if (!textToIndex.has(t)) textToIndex.set(t, []);
      textToIndex.get(t)!.push(i);
    }

    const results: Array<{ index: number; text: string; ooxml: string }> = [];
    const usedIndices = new Set<number>();

    for (let s = 0; s < selectedParagraphs.items.length; s++) {
      const sp = selectedParagraphs.items[s];
      const ooxml = sp.getOoxml();
      await context.sync();

      // Try to find the index in the full body
      let idx = -1;

      // Reference comparison
      for (let i = 0; i < allParagraphs.items.length; i++) {
        if (allParagraphs.items[i] === sp) {
          idx = i;
          break;
        }
      }

      // Fallback: text match (pick first unused index with same text)
      if (idx < 0) {
        const candidates = textToIndex.get(sp.text) || [];
        for (const c of candidates) {
          if (!usedIndices.has(c)) {
            idx = c;
            break;
          }
        }
      }

      if (idx < 0) idx = s; // last resort
      usedIndices.add(idx);

      if (sp.text.trim()) {
        results.push({ index: idx, text: sp.text, ooxml: ooxml.value });
      }
    }

    return results;
  });
}

// ── Highlighting ──────────────────────────────────────────────────────

/** Map our color strings to Word highlight color names. */
function toHighlightColor(color: string): string {
  switch (color.toLowerCase()) {
    case "red":
      return "Red";
    case "yellow":
      return "Yellow";
    case "green":
      return "Green";
    case "blue":
      return "Blue";
    case "pink":
      return "Pink";
    default:
      return "Yellow";
  }
}

/**
 * Highlight a finding's text in the document using body.search().
 */
export async function highlightFinding(finding: AddinFinding): Promise<void> {
  const searchText = sanitizeSearchText(finding.search_text);
  if (!searchText) return;

  await Word.run(async (context) => {
    const results = context.document.body.search(searchText, {
      matchCase: true,
      matchWholeWord: false,
    });
    results.load("font");
    await context.sync();

    for (const item of results.items) {
      item.font.highlightColor = toHighlightColor(finding.highlight_color) as any;
    }
    await context.sync();
  });
}

/**
 * Remove highlight from a finding's text in the document.
 */
export async function clearHighlight(finding: AddinFinding): Promise<void> {
  const searchText = sanitizeSearchText(finding.search_text);
  if (!searchText) return;

  await Word.run(async (context) => {
    const results = context.document.body.search(searchText, {
      matchCase: true,
      matchWholeWord: false,
    });
    results.load("font");
    await context.sync();

    for (const item of results.items) {
      item.font.highlightColor = "NoHighlight" as any;
    }
    await context.sync();
  });
}

/**
 * Clear all highlights applied by the add-in (reset document).
 * We iterate paragraphs rather than setting body.font.highlightColor
 * because the latter throws InvalidArgument when protected ranges or
 * content controls are present.
 */
export async function clearAllHighlights(): Promise<void> {
  await Word.run(async (context) => {
    const paragraphs = context.document.body.paragraphs;
    paragraphs.load("font");
    await context.sync();

    for (const para of paragraphs.items) {
      try {
        para.font.highlightColor = "NoHighlight" as any;
      } catch {
        // skip protected / read-only paragraphs
      }
    }
    await context.sync();
  });
}

// ── Navigation ────────────────────────────────────────────────────────

/**
 * Scroll to and select the text matching a finding.
 */
export async function navigateToFinding(finding: AddinFinding): Promise<void> {
  const searchText = sanitizeSearchText(finding.search_text);
  if (!searchText) return;

  await Word.run(async (context) => {
    const results = context.document.body.search(searchText, {
      matchCase: true,
      matchWholeWord: false,
    });
    results.load();
    await context.sync();

    if (results.items.length > 0) {
      results.items[0].select();
      await context.sync();
    }
  });
}

// ── Auto-fix ──────────────────────────────────────────────────────────

/**
 * Sanitise search text for Word's body.search() API.
 * Word search has a 255-char limit and chokes on newlines / certain
 * control characters.  We truncate and strip problematic chars.
 */
function sanitizeSearchText(text: string): string {
  // Strip newlines, tabs, zero-width chars
  let clean = text.replace(/[\r\n\t\u200B-\u200D\uFEFF]/g, " ").trim();
  // Word search max is 255 characters
  if (clean.length > 255) clean = clean.slice(0, 255);
  return clean;
}

/**
 * Apply an automatic fix for a finding.
 * Returns true if the fix was applied, false if not applicable.
 */
export async function applyFix(finding: AddinFinding): Promise<boolean> {
  if (!finding.fixable || !finding.search_text) return false;

  const searchText = sanitizeSearchText(finding.search_text);
  if (!searchText) return false;

  try {
    return await Word.run(async (context) => {
      const results = context.document.body.search(searchText, {
        matchCase: true,
        matchWholeWord: false,
      });
      results.load("font");
      await context.sync();

      if (results.items.length === 0) return false;
      const range = results.items[0];

      switch (finding.fix_type) {
        case "remove_formatting": {
          const formattings = finding.fix_value.split(",").map((f) => f.trim());
          for (const fmt of formattings) {
            switch (fmt) {
              case "bold":
                range.font.bold = false;
                break;
              case "italic":
                range.font.italic = false;
                break;
              case "underline":
                range.font.underline = "None" as any;
                break;
              case "strikethrough":
                range.font.strikeThrough = false;
                break;
            }
          }
          break;
        }

        case "replace_text": {
          if (finding.fix_value) {
            // Re-search to get a fresh range — a prior sibling fix may
            // have invalidated the original range reference.
            const fresh = context.document.body.search(searchText, {
              matchCase: true,
              matchWholeWord: false,
            });
            fresh.load("text");
            await context.sync();
            if (fresh.items.length > 0) {
              fresh.items[0].insertText(finding.fix_value, Word.InsertLocation.replace);
            } else {
              return false;
            }
          }
          break;
        }

        case "apply_style": {
          if (finding.fix_value) {
            const paragraph = range.paragraphs.getFirst();
            paragraph.style = finding.fix_value;
          }
          break;
        }

        default:
          return false;
      }

      // Clear the highlight after fix — "No Color" removes highlight
      // without triggering InvalidArgument (null is not accepted).
      try {
        range.font.highlightColor = "NoHighlight" as any;
        await context.sync();
      } catch {
        // If range became stale after replace_text, that's fine — the
        // highlight was on the old text which is already gone.
      }
      return true;
    });
  } catch (err) {
    console.error(`applyFix failed for "${finding.id}":`, err);
    return false;
  }
}
