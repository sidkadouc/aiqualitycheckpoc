/**
 * API client — communicates with the Python FastAPI backend.
 */

import { AddinResponse, CheckRequest, ParagraphCheckRequest } from "../models/types";

/** Base URL of the quality-checker API — injected at build time via webpack DefinePlugin (env var API_BASE). */
const API_BASE: string = (process.env.API_BASE as string) || "http://localhost:8000";

/** Max retries for transient / rate-limit errors. */
const MAX_RETRIES = 3;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const resp = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (resp.ok) {
      return resp.json() as Promise<T>;
    }

    const text = await resp.text();

    // Retry on 429 (rate limit) or 503 (service unavailable)
    if ((resp.status === 429 || resp.status === 503) && attempt < MAX_RETRIES) {
      const delay = Math.min(2000 * Math.pow(2, attempt), 30000);
      console.warn(
        `API ${resp.status} on ${path} — retrying in ${delay}ms (attempt ${attempt + 1}/${MAX_RETRIES})`
      );
      await sleep(delay);
      lastError = new Error(`API ${resp.status}: ${text}`);
      continue;
    }

    throw new Error(`API ${resp.status}: ${text}`);
  }

  throw lastError || new Error("Request failed after retries");
}

/**
 * Full-document check — sends all selected paragraphs.
 */
export async function checkDocument(req: CheckRequest): Promise<AddinResponse> {
  return post<AddinResponse>("/api/check", req);
}

/**
 * Single-paragraph check — fast path for real-time feedback.
 */
export async function checkParagraph(req: ParagraphCheckRequest): Promise<AddinResponse> {
  return post<AddinResponse>("/api/check-paragraph", req);
}

/**
 * Health check — verify the API is reachable.
 */
export async function healthCheck(): Promise<{ status: string; rules_loaded: number }> {
  const resp = await fetch(`${API_BASE}/api/health`);
  if (!resp.ok) throw new Error(`Health check failed: ${resp.status}`);
  return resp.json();
}
