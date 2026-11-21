import { useState, useRef, useEffect } from "react";
import { apiFetch, type AskEvent, type AskResult, type Citation } from "./api";
import HowIGotThis from "./HowIGotThis";

// One line of live progress: an engine stage or an agent step.
interface Progress {
  key: string;
  label: string;
  state: "running" | "done" | "failed";
  ms?: number;
  note?: string | null;
}

interface Message {
  role: "user" | "assistant";
  text: string;
  question?: string; // the question this answer is for (kept for "Try again")
  progress: Progress[];
  result?: AskResult;
  error?: { message: string; retryable: boolean };
  streaming: boolean;
}

function FeedbackControl({ queryId }: { queryId: number }) {
  const [verdict, setVerdict] = useState<"up" | "down" | null>(null);
  const [comment, setComment] = useState("");
  const [commentSent, setCommentSent] = useState(false);

  // Fire-and-forget: feedback must never disrupt the chat, so errors are
  // swallowed (the backend logs its side of any failure).
  function send(thumbsUp: boolean, text?: string) {
    apiFetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query_id: queryId,
        thumbs_up: thumbsUp,
        comment: text || undefined,
      }),
    }).catch(() => {});
  }

  function clickThumb(thumbsUp: boolean) {
    setVerdict(thumbsUp ? "up" : "down");
    send(thumbsUp);
  }

  function submitComment() {
    if (!comment.trim()) return;
    // The comment box only exists after a thumbs-down; upserts onto the same row.
    send(false, comment.trim());
    setCommentSent(true);
  }

  return (
    <div className="feedback">
      <button
        className={`thumb${verdict === "up" ? " active" : ""}`}
        onClick={() => clickThumb(true)}
        title="Good answer"
        aria-label="Thumbs up"
      >
        👍
      </button>
      <button
        className={`thumb${verdict === "down" ? " active" : ""}`}
        onClick={() => clickThumb(false)}
        title="Bad answer"
        aria-label="Thumbs down"
      >
        👎
      </button>
      {verdict === "down" && !commentSent && (
        <span className="feedback-comment">
          <input
            type="text"
            placeholder="What went wrong? (optional)"
            value={comment}
            maxLength={2000}
            onChange={(e) => setComment(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitComment()}
          />
          <button onClick={submitComment} disabled={!comment.trim()}>
            Send
          </button>
        </span>
      )}
      {(verdict === "up" || commentSent) && (
        <span className="feedback-thanks">Thanks!</span>
      )}
    </div>
  );
}

// Outcomes that aren't an answer: shown in the refusal style.
const NOT_ANSWERED = new Set(["out_of_scope", "nothing_found", "uncited", "refused_unverified", "money_movement"]);

function citationLabel(c: Citation): string {
  if (c.kind === "document") return c.label || `${c.filename.replace(/^public\//, "")} › ${c.section.split(" > ").pop()}`;
  const name = c.label || c.tool;
  return c.attribution ? `${name} (${c.attribution})` : name; // data-source credit stays visible
}

/** "2026-06-30" as "June 30, 2026", without the browser's time zone moving the day. */
function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    year: "numeric", month: "long", day: "numeric", timeZone: "UTC",
  });
}

/** Apply one stream event to the progress list. */
function track(progress: Progress[], event: AskEvent): Progress[] {
  if (event.type !== "stage" && event.type !== "step") return progress;
  const prefix = event.type === "stage" ? `stage:${event.stage}:` : `step:${event.id}:`;
  if (event.state === "start") {
    return [...progress, { key: prefix + progress.length, label: event.label, state: "running" }];
  }
  // The latest running item of this stage or step finishes.
  const index = progress.findLastIndex((p) => p.key.startsWith(prefix) && p.state === "running");
  if (index === -1) return progress;
  const done: Progress = {
    ...progress[index],
    state: event.state === "failed" ? "failed" : "done",
    ms: event.ms,
    note: event.type === "step" ? event.note ?? event.error : undefined,
  };
  return [...progress.slice(0, index), done, ...progress.slice(index + 1)];
}

interface Props {
  isCustomer: boolean;
  demoToday?: string;
}

const CUSTOMER_EXAMPLES = [
  "Why was I charged $15 on April 6, 2026?",
  "Can I afford $1,200 this month and stay above $500?",
  "How much did I spend on groceries in May 2026?",
  "What is VTI's expense ratio, and what is its latest price?",
];
const STAFF_EXAMPLES = [
  "How long can the bank hold a check deposit?",
  "What is the overdraft fee, and when is it charged?",
  "What did Coca-Cola say about its main risks in its last 10-K?",
];

export default function Chat({ isCustomer, demoToday }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Immutably update the in-flight assistant message (always the last one).
  function patchLast(patch: (last: Message) => Message) {
    setMessages((prev) => [...prev.slice(0, -1), patch(prev[prev.length - 1])]);
  }

  /** Ask `question`. With `retry`, the failed answer it replaces is dropped first. */
  async function ask(question: string, retry = false) {
    if (!question || isStreaming) return;
    setIsStreaming(true);
    const pending: Message = { role: "assistant", text: "", question, progress: [], streaming: true };
    setMessages((prev) =>
      retry
        ? [...prev.slice(0, -1), pending]
        : [...prev, { role: "user", text: question, progress: [], streaming: false }, pending]
    );

    function fail(message: string, retryable: boolean) {
      patchLast((m) => ({ ...m, error: { message, retryable }, streaming: false }));
    }

    try {
      const res = await apiFetch("/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (!res.ok || !res.body) {
        fail("Something went wrong on our side while answering. Please try again.", true);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      // SSE events can be split across reads, so buffer until "\n\n".
      let buffer = "";
      let finished = false;

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";

        for (const raw of events) {
          if (!raw.startsWith("data: ")) continue;
          const event = JSON.parse(raw.slice(6)) as AskEvent;
          if (event.type === "done") {
            finished = true;
            patchLast((m) => ({ ...m, text: event.answer, result: event, streaming: false }));
          } else if (event.type === "error") {
            finished = true;
            fail(event.message, event.retryable);
          } else {
            patchLast((m) => ({ ...m, progress: track(m.progress, event) }));
          }
        }
      }
      if (!finished) fail("The connection closed before the answer arrived. Please try again.", true);
    } catch {
      fail("Couldn't reach FinPilot. Check your connection and try again.", true);
    } finally {
      setIsStreaming(false);
    }
  }

  function sendInput() {
    const question = input.trim();
    if (!question || isStreaming) return;
    setInput("");
    ask(question);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendInput();
    }
  }

  const examples = isCustomer ? CUSTOMER_EXAMPLES : STAFF_EXAMPLES;

  return (
    <div className="chat-panel">
      <h2>Ask FinPilot</h2>
      <p className="subtitle">
        {isCustomer ? "Your accounts, the bank's documents and the markets" : "The bank's documents and the markets"}
        {" "}— planned, cited and checked before you see it.
        {demoToday && (
          <span className="demo-date" title="The synthetic data ends on this day, so it's the assistant's today.">
            {" "}Today is {longDate(demoToday)} (demo date).
          </span>
        )}
      </p>

      <div className="message-list">
        {messages.length === 0 && (
          <div className="empty-state">
            <p>Try one of these, or ask your own:</p>
            <div className="examples">
              {examples.map((q) => (
                <button key={q} className="example" onClick={() => ask(q)} disabled={isStreaming}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => {
          const result = msg.result;
          const refused = !!msg.error || (!!result && NOT_ANSWERED.has(result.outcome));
          return (
            <div key={i} className={`message ${msg.role}`}>
              <div className={`bubble${refused ? " refusal" : ""}`}>
                <span className="role-label">{msg.role === "user" ? "You" : "FinPilot"}</span>

                {msg.role === "assistant" && msg.progress.length > 0 && (
                  <div className="stage-row" aria-live="polite">
                    {msg.progress.map((p) => (
                      <span
                        key={p.key}
                        className={`stage-chip ${p.state}`}
                        title={[p.ms != null ? `${(p.ms / 1000).toFixed(1)} s` : "", p.note ?? ""]
                          .filter(Boolean).join(" · ")}
                      >
                        {p.state === "done" ? "✓ " : p.state === "failed" ? "✗ " : ""}
                        {p.label}
                        {p.state === "running" ? "…" : ""}
                      </span>
                    ))}
                  </div>
                )}

                {msg.error ? (
                  <div className="error-box" role="alert">
                    <p>{msg.error.message}</p>
                    {msg.error.retryable && msg.question && i === messages.length - 1 && (
                      <button className="retry-btn" onClick={() => ask(msg.question!, true)} disabled={isStreaming}>
                        Try again
                      </button>
                    )}
                  </div>
                ) : (
                  <p>
                    {msg.text}
                    {msg.streaming && <span className="cursor">▌</span>}
                  </p>
                )}

                {result && result.citations.length > 0 && (
                  <div className="sources">
                    <span className="sources-label">Sources:</span>
                    {result.citations.map((c) => (
                      <span key={c.n} className="source-chip" title={c.kind === "document" ? c.snippet : undefined}>
                        [{c.n}] {citationLabel(c)}
                      </span>
                    ))}
                  </div>
                )}

                {result && (
                  <span className="meta-line">
                    {refused ? `${result.outcome.replace(/_/g, " ")} · ` : ""}
                    {((result.timings.total_ms ?? 0) / 1000).toFixed(1)} s · {result.llm_calls} model call
                    {result.llm_calls === 1 ? "" : "s"}
                  </span>
                )}

                {result && (result.plan?.steps.length ?? 0) > 0 && <HowIGotThis result={result} />}

                {result && result.query_id != null && (
                  <FeedbackControl key={result.query_id} queryId={result.query_id} />
                )}
              </div>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>

      <div className="input-row">
        <input
          type="text"
          placeholder={isCustomer ? "Ask about your accounts, fees or investments…" : "Ask about the bank's documents…"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isStreaming}
          className="chat-input"
        />
        <button onClick={sendInput} disabled={!input.trim() || isStreaming} className="send-btn">
          {isStreaming ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
