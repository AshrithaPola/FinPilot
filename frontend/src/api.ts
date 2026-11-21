import { auth } from "./firebase";

// Single place the backend location is known. "/api" is proxied by Vite in
// dev (vite.config.ts) and by nginx in the Docker build (nginx.conf).
export const API_BASE = "/api";

// GET /me: who the token belongs to and whether they may use the app.
export interface Me {
  firebase_uid: string;
  set_up: boolean;
  reason: "unknown_user" | "no_org" | "inactive" | null;
  user_id?: string;
  org_id?: string;
  role?: "viewer" | "editor" | "admin";
  clearance?: number;
  team_ids?: string[];
  customer_id?: string | null;
  demo_today?: string; // the synthetic data's last day: the assistant's "today"
}

// Called when any API call gets a 403, so AuthGate can re-check /me (the
// account may have just been deactivated or moved out of its org).
let onForbidden: (() => void) | null = null;
export function setForbiddenHandler(handler: (() => void) | null) {
  onForbidden = handler;
}

/** fetch() against the backend, signed in: every call carries the user's
 * Firebase ID token. getIdToken() returns the cached token, or refreshes it
 * first when it expires within five minutes, so a long session keeps working. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const user = auth?.currentUser;
  if (user) headers.set("Authorization", `Bearer ${await user.getIdToken()}`);
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 403 && path !== "/me") onForbidden?.();
  return res;
}

export interface DocumentInfo {
  doc_id: string;
  filename: string;
  uploaded_at: string | null;
  chunks: number;
  team_id?: string | null;
  clearance?: number;
}

// A customer's sidebar: balances and the latest transactions (GET /accounts/summary).
export interface AccountsSummary {
  as_of: string;
  balances: { account_id: string; type: string; product: string; balance_cents: number; as_of: string | null }[];
  transactions: { txn_id: string; posted_on: string; amount_cents: number; merchant: string | null; description: string | null }[];
}

// One piece of evidence an answer cites: a document passage or a tool result.
// `label` is the source as a person reads it ("Your fees on Apr 6, 2026").
export type Citation =
  | { n: number; kind: "document"; step: string; chunk_id: string; filename: string; section: string; snippet: string; label?: string | null }
  | { n: number; kind: "tool"; step: string; tool: string; attribution: string | null; label?: string | null };

export interface PlanStep {
  id: string;
  agent: string;
  tool: string;
  arguments?: Record<string, unknown>;
  purpose: string;
  status?: "planned" | "done" | "failed";
  error?: string | null;
  evidence?: number[];
  round?: number;
  note?: string | null;
}

export interface Verdict {
  passed: boolean;
  reason: string;
  by: "code" | "llm";
}

// The finished answer: POST /ask's response, and the "done" event of /ask/stream.
export interface AskResult {
  answer: string;
  outcome: string; // answered | small_talk | out_of_scope | nothing_found | uncited | refused_unverified
  demo_today: string;
  plan: { intent: string; steps: PlanStep[]; dropped: string[] } | null;
  citations: Citation[];
  validation: Record<"gatekeeper" | "auditor" | "strategist", Verdict>[];
  llm_calls: number;
  llm_calls_by_role: Record<string, number>;
  timings: Record<string, number>;
  timeline: { stage: string; start_ms: number; ms: number }[];
  trace_id: string | null;
  trace?: { est_cost_usd: number; llm_ms: number; search_ms: number; tools_ms: number; retries: number };
  query_id: number | null; // null when the query log is off
}

// Events of POST /ask/stream (SSE). The answer arrives once, in "done",
// after the validators passed it.
export type AskEvent =
  | { type: "plan"; round: number; intent: string; steps: PlanStep[] }
  | { type: "stage"; stage: string; label: string; state: "start" | "done"; ms?: number }
  | {
      type: "step";
      id: string;
      label: string;
      agent_tool: string;
      round: number;
      state: "start" | "done" | "failed";
      ms?: number;
      error?: string | null;
      note?: string | null;
    }
  | ({ type: "done" } & AskResult)
  | { type: "error"; kind: string; message: string; retryable: boolean };
