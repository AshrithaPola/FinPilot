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
const NOT_ANSWERED = new Set([
  "out_of_scope", "nothing_found", "uncited", "refused_unverified", "money_movement", "no_customer_data",
]);

function citationLabel(c: Citation): string {
  if (c.kind === "document") return c.label || `${c.filename.replace(/^public\//, "")} › ${c.section.split(" > ").pop()}`;
  const name = c.label || c.tool;
  return c.attribution ? `${name} (${c.attribution})` : name; // data-source credit stays visible
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
}

// Suggested questions, each with the kind of help it shows.
const CUSTOMER_EXAMPLES = [
  { topic: "A charge", question: "Why was I charged $15 on April 6, 2026?" },
  { topic: "Planning", question: "Can I afford $1,200 this month and stay above $500?" },
  { topic: "Spending", question: "How much did I spend on groceries in May 2026?" },
  { topic: "Investments", question: "What is VTI's expense ratio, and what is its latest price?" },
];
const STAFF_EXAMPLES = [
  { topic: "Policy", question: "How long can the bank hold a check deposit?" },
  { topic: "Fees", question: "What is the overdraft fee, and when is it charged?" },
  { topic: "Filings", question: "What did Coca-Cola say about its main risks in its last 10-K?" },
];

export default function Chat({ isCustomer }: Props) {
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
      <div className="message-list">
        {messages.length === 0 && (
          <div className="empty-state">
            <h1>{isCustomer ? "What would you like to know about your money?" : "What would you like to look up?"}</h1>
            <p className="empty-lede">
              {isCustomer
                ? "Ask about your balances, a charge, your spending or the bank's policies. Every answer shows where it came from."
                : "Ask about the bank's policies, fees, rates or the funds and filings it covers. Every answer shows where it came from."}
            </p>
            <div className="examples">
              {examples.map(({ topic, question }) => (
                <button key={question} className="example" onClick={() => ask(question)} disabled={isStreaming}>
                  <span className="example-topic">{topic}</span>
                  <span className="example-question">{question}</span>
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

                {/* Live progress while it works; afterwards the steps live in "How I got this". */}
                {msg.role === "assistant" && msg.streaming && msg.progress.length > 0 && (
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
                    <span className="sources-label">Sources</span>
                    {result.citations.map((c) => (
                      <span key={c.n} className="source-chip" title={c.kind === "document" ? c.snippet : undefined}>
                        [{c.n}] {citationLabel(c)}
                      </span>
                    ))}
                  </div>
                )}

                {result && (
                  <div className="answer-footer">
                    {result.query_id != null && <FeedbackControl key={result.query_id} queryId={result.query_id} />}
                    {(result.plan?.steps.length ?? 0) > 0 && <HowIGotThis result={result} />}
                  </div>
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
          placeholder={isCustomer ? "Ask about a charge, your balance or a bank policy" : "Ask about a policy, a fee or a fund"}
          aria-label="Your question"
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
