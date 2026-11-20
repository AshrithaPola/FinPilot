import { useEffect, useState } from "react";
import { apiFetch } from "./api";

// Admins only: the org's recent /ask requests (GET /admin/traces) and one
// request's every span (GET /admin/traces/{id}). Row-level security on the
// server keeps both to the admin's own org.

interface TraceRow {
  trace_id: string;
  created_at: string;
  customer_id: string | null;
  question: string;
  outcome: string | null;
  total_ms: number | null;
  llm_calls: number | null;
  est_cost_usd: number | null;
  retries: number | null;
}

interface Span {
  kind: string;
  name: string;
  start_ms: number;
  ms: number;
  [detail: string]: unknown;
}

interface TraceDetail extends TraceRow {
  summary: Record<string, unknown>;
  spans: Span[];
}

const SHOWN = ["role", "step", "status", "attempts", "hits", "top_rerank", "weak", "passed", "by", "prompt_tokens",
  "output_tokens", "thinking_tokens", "est_tokens", "est_cost_usd", "error", "note", "reason"];

function details(span: Span): string {
  return SHOWN.filter((k) => span[k] != null && span[k] !== "")
    .map((k) => `${k}=${typeof span[k] === "object" ? JSON.stringify(span[k]) : String(span[k])}`)
    .join("  ");
}

function seconds(ms: number | null | undefined): string {
  return ms == null ? "" : `${(ms / 1000).toFixed(1)} s`;
}

function Detail({ id, onBack }: { id: string; onBack: () => void }) {
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    apiFetch(`/admin/traces/${id}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((t) => active && setTrace(t))
      .catch(() => active && setError("Couldn't load this trace."));
    return () => {
      active = false;
    };
  }, [id]);

  if (error) return <p className="doc-hint">{error}</p>;
  if (!trace) return <p className="doc-hint">Loading…</p>;
  const failures = trace.spans.flatMap((s) =>
    Array.isArray(s.failures) ? (s.failures as { attempt: number; error: string }[]).map((f) => ({ s, f })) : []);
  return (
    <div className="traces">
      <button className="link-btn" onClick={onBack}>← All traces</button>
      <h3>{trace.question}</h3>
      <p className="doc-hint">
        {new Date(trace.created_at).toLocaleString()} · {trace.customer_id ?? "staff"} · {trace.outcome} ·{" "}
        {seconds(trace.total_ms)} · {trace.llm_calls} LLM calls · est. ${Number(trace.est_cost_usd ?? 0).toFixed(5)}
      </p>
      <table className="trace-table">
        <thead>
          <tr><th>start</th><th>time</th><th>kind</th><th>name</th><th>details</th></tr>
        </thead>
        <tbody>
          {trace.spans.map((s, i) => (
            <tr key={i} className={`kind-${s.kind}`}>
              <td>{seconds(s.start_ms)}</td>
              <td>{seconds(s.ms)}</td>
              <td>{s.kind}</td>
              <td title={s.name}>{s.name}</td>
              <td>{details(s)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {failures.length > 0 && (
        <>
          <h4>Retries</h4>
          <ul>
            {failures.map(({ s, f }, i) => (
              <li key={i} className="doc-hint">{s.name} ({String(s.role ?? s.kind)}) attempt {f.attempt}: {f.error}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export default function Traces() {
  const [rows, setRows] = useState<TraceRow[] | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    apiFetch("/admin/traces?limit=50")
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((body) => active && setRows(body.traces))
      .catch(() => active && setError("Couldn't load traces (admins only)."));
    return () => {
      active = false;
    };
  }, [open]);

  if (open) return <Detail id={open} onBack={() => setOpen(null)} />;
  if (error) return <p className="doc-hint">{error}</p>;
  if (!rows) return <p className="doc-hint">Loading…</p>;
  return (
    <div className="traces">
      <h2>Recent requests</h2>
      {rows.length === 0 ? (
        <p className="doc-hint">No /ask requests in your org yet.</p>
      ) : (
        <table className="trace-table">
          <thead>
            <tr><th>when</th><th>who</th><th>question</th><th>outcome</th><th>time</th><th>LLM calls</th><th>est. cost</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.trace_id} className="clickable" onClick={() => setOpen(r.trace_id)}>
                <td>{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.customer_id ?? "staff"}</td>
                <td title={r.question}>{r.question}</td>
                <td>{r.outcome}</td>
                <td>{seconds(r.total_ms)}</td>
                <td>{r.llm_calls}{r.retries ? ` (${r.retries} retries)` : ""}</td>
                <td>${Number(r.est_cost_usd ?? 0).toFixed(5)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
