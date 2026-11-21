import type { AskResult, PlanStep, Verdict } from "./api";

// "How I got this": the plan, each step and what it found, the checks the
// answer passed (and who decided: code or the model), where the time went,
// and the model calls it took. Everything here comes from the /ask result.

const AGENTS: Record<string, string> = {
  financial_data: "Financial data",
  documents: "Documents",
  calculation: "Calculation",
};
const VALIDATORS: Record<string, string> = {
  gatekeeper: "Answers the question",
  auditor: "Every figure and claim is in the evidence",
  strategist: "No advice, no money moved, nothing about anyone else",
};
const STAGES: Record<string, string> = {
  validate: "Read the question",
  route: "Route",
  plan: "Plan",
  execute: "Run the steps",
  replan: "Re-plan",
  summarize: "Write the answer",
  check: "Check the answer",
};

function seconds(ms: number | undefined): string {
  return ms == null ? "" : `${(ms / 1000).toFixed(1)} s`;
}

function Step({ step }: { step: PlanStep }) {
  const args = step.arguments && Object.keys(step.arguments).length > 0 ? JSON.stringify(step.arguments) : "";
  const mark = step.status === "done" ? "✓" : step.status === "failed" ? "✗" : "·";
  return (
    <li className={`hig-step ${step.status ?? ""}`}>
      <span className="hig-mark">{mark}</span>
      <div>
        <div>
          <strong>{AGENTS[step.agent] ?? step.agent}</strong> · <code>{step.tool}</code>
          {step.round === 2 && <span className="hig-tag">re-plan</span>}
          {step.evidence && step.evidence.length > 0 && (
            <span className="hig-evidence"> → evidence {step.evidence.map((n) => `[${n}]`).join("")}</span>
          )}
        </div>
        {step.purpose && <div className="hig-muted">{step.purpose}</div>}
        {args && <code className="hig-args">{args}</code>}
        {step.note && <div className="hig-muted">{step.note}</div>}
        {step.error && <div className="hig-error">{step.error}</div>}
      </div>
    </li>
  );
}

function Checks({ attempts }: { attempts: Record<string, Verdict>[] }) {
  return (
    <>
      {attempts.map((verdicts, i) => (
        <div key={i} className="hig-checks">
          {attempts.length > 1 && (
            <div className="hig-muted">{i === 0 ? "First answer" : "Rewritten answer"}</div>
          )}
          <ul>
            {Object.entries(verdicts).map(([name, v]) => (
              <li key={name} className={v.passed ? "done" : "failed"}>
                <span className="hig-mark">{v.passed ? "✓" : "✗"}</span>
                <span>
                  {VALIDATORS[name] ?? name}{" "}
                  <span className="hig-tag">{v.by === "code" ? "checked in code" : "checked by the model"}</span>
                  {!v.passed && <div className="hig-error">{v.reason}</div>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  );
}

function Timeline({ result }: { result: AskResult }) {
  const total = result.timings.total_ms || Math.max(...result.timeline.map((t) => t.start_ms + t.ms), 1);
  return (
    <div className="hig-timeline">
      {result.timeline.map((t, i) => (
        <div key={i} className="hig-bar-row">
          <span className="hig-bar-label">{STAGES[t.stage] ?? t.stage}</span>
          <span className="hig-bar-track">
            <span
              className="hig-bar"
              style={{ left: `${(t.start_ms / total) * 100}%`, width: `${Math.max((t.ms / total) * 100, 0.8)}%` }}
            />
          </span>
          <span className="hig-bar-ms">{seconds(t.ms)}</span>
        </div>
      ))}
    </div>
  );
}

export default function HowIGotThis({ result }: { result: AskResult }) {
  const steps = result.plan?.steps ?? [];
  const roles = Object.entries(result.llm_calls_by_role);
  const trace = result.trace;
  return (
    <details className="hig">
      <summary>How I got this</summary>
      <p className="hig-overview">
        Answered in {seconds(result.timings.total_ms)} with {result.llm_calls} model call
        {result.llm_calls === 1 ? "" : "s"}
        {result.outcome !== "answered" ? `; result: ${result.outcome.replace(/_/g, " ")}` : ""}.
      </p>

      {steps.length > 0 && (
        <section>
          <h4>The plan{result.plan?.intent ? `: ${result.plan.intent}` : ""}</h4>
          <ol className="hig-steps">
            {steps.map((s) => (
              <Step key={s.id} step={s} />
            ))}
          </ol>
          {result.plan && result.plan.dropped.length > 0 && (
            <div className="hig-muted">Left out of the plan: {result.plan.dropped.join("; ")}</div>
          )}
        </section>
      )}

      {result.validation.length > 0 && (
        <section>
          <h4>Checks before you saw it</h4>
          <Checks attempts={result.validation} />
        </section>
      )}

      {result.timeline.length > 0 && (
        <section>
          <h4>Where the time went · {seconds(result.timings.total_ms)}</h4>
          <Timeline result={result} />
          {trace && (
            <div className="hig-muted">
              The language model {seconds(trace.llm_ms)}, document search {seconds(trace.search_ms)}, your data{" "}
              {seconds(trace.tools_ms)}
              {trace.retries > 0 ? `; ${trace.retries} retr${trace.retries === 1 ? "y" : "ies"} while the model was busy` : ""}.
            </div>
          )}
        </section>
      )}

      <section>
        <h4>Model calls · {result.llm_calls}</h4>
        <div className="hig-muted">
          {roles.length > 0 ? roles.map(([role, n]) => `${role.replace(/_/g, " ")} ${n}`).join(", ") : "none"}
          {trace && trace.est_cost_usd > 0 && ` · about $${trace.est_cost_usd.toFixed(4)} at list price (free tier here)`}
        </div>
        {result.trace_id && <div className="hig-muted hig-id">Trace {result.trace_id}</div>}
      </section>
    </details>
  );
}
