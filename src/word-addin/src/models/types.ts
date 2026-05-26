/**
 * TypeScript types matching the Python AddinResponse models.
 * Keep in sync with quality_agent/models.py.
 */

export interface AddinFinding {
  id: string;
  rule_id: string;
  rule_text: string;
  rule_type: "do" | "dont" | "unspecified";
  severity: "mandatory" | "recommended" | "informational";

  doc_paragraph_index: number;
  search_text: string;
  run_indices: number[];

  explanation: string;
  suggestion: string;
  confidence: number;

  /** Section in the OECD Style Guide PDF, e.g. "3 Capitalisation > General rules" */
  section_title: string;
  /** Page number in the OECD Style Guide PDF */
  page: number | null;

  fixable: boolean;
  fix_type: "remove_formatting" | "replace_text" | "apply_style" | "manual";
  fix_value: string;

  /** Rule ID that supersedes this finding (empty if active) */
  superseded_by: string;
  /** Human-readable reason for supersession */
  superseded_reason: string;

  highlight_color: string;
}

export interface AddinParagraphGroup {
  doc_paragraph_index: number;
  text_preview: string;
  finding_count: number;
  findings: AddinFinding[];
}

export interface AddinResponse {
  version: string;
  total_paragraphs: number;
  total_findings: number;
  summary: Record<string, number>;
  paragraphs: AddinParagraphGroup[];
  highlighted_ooxml: string;
}

export interface ParagraphCheckRequest {
  docParagraphIndex: number;
  ooxml: string;
  textPreview: string;
}

export interface CheckRequest {
  documentInfo: {
    totalParagraphsInDoc: number;
    selectedParagraphs: number;
    timestamp?: string;
  };
  paragraphs: Array<{
    docParagraphIndex: number;
    selectionIndex: number;
    textPreview: string;
    ooxmlLength: number;
    ooxml: string;
  }>;
}
