import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import AuthGate from "./AuthGate";
import Chat from "./Chat";
import Traces from "./Traces";
import { apiFetch, type AccountsSummary, type DocumentInfo, type Me } from "./api";
import "./index.css";

/** The customer's balances and latest transactions, or null if they can't be loaded. */
async function fetchSummary(): Promise<AccountsSummary | null> {
  try {
    const res = await apiFetch("/accounts/summary");
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

const usd = (cents: number) => (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });

/** "2026-06-30" as "Jun 30, 2026", without the browser's time zone moving the day. */
function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    year: "numeric", month: "short", day: "numeric", timeZone: "UTC",
  });
}

/** The document list, or null if the backend isn't reachable yet. */
async function fetchDocuments(): Promise<DocumentInfo[] | null> {
  try {
    const res = await apiFetch("/documents");
    if (!res.ok) return null;
    const data = await res.json();
    return data.documents ?? [];
  } catch {
    return null; // Backend not reachable yet — the list just stays as it is.
  }
}

export default function App() {
  return (
    <AuthGate>
      {(me, user, signOut) => <Workspace me={me} user={user} onSignOut={signOut} />}
    </AuthGate>
  );
}

function Workspace({ me, user, onSignOut }: { me: Me; user: User; onSignOut: () => void }) {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  // undefined: loading; null: couldn't be loaded.
  const [summary, setSummary] = useState<AccountsSummary | null | undefined>(undefined);
  const [view, setView] = useState<"chat" | "traces">("chat");
  const isAdmin = me.role === "admin";
  const isCustomer = !!me.customer_id;

  useEffect(() => {
    // setState only in the async callbacks, never in the effect body; skipped
    // if the component unmounted first.
    let active = true;
    fetchDocuments().then((docs) => {
      if (active && docs) setDocuments(docs);
    });
    if (isCustomer) {
      fetchSummary().then((s) => {
        if (active) setSummary(s);
      });
    }
    return () => {
      active = false;
    };
  }, [isCustomer]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>FinPilot</h1>
        <span className="tagline">Your banking assistant</span>
        <span className="user-badge" title={`Org ${me.org_id}, clearance ${me.clearance}`}>
          {user.email} · {me.customer_id ? "customer" : me.role}
        </span>
        {isAdmin && (
          <button className="link-btn" onClick={() => setView(view === "chat" ? "traces" : "chat")}>
            {view === "chat" ? "Traces" : "Chat"}
          </button>
        )}
        <button className="link-btn" onClick={onSignOut}>Sign out</button>
      </header>

      {view === "traces" && isAdmin ? (
        <main className="app-main traces-main">
          <Traces />
        </main>
      ) : (
        <main className="app-main">
          <aside className="upload-panel">
            {isCustomer && <AccountsPanel summary={summary} />}
            <DocumentList documents={documents} open={!isCustomer} />
          </aside>
          <Chat isCustomer={isCustomer} demoToday={me.demo_today} />
        </main>
      )}
    </div>
  );
}

const BALANCE_LABELS: Record<string, string> = { checking: "Checking", savings: "Savings", credit: "Card owed" };

/** Checking, savings and what's owed on the card, as of the demo date, and the 5 latest transactions. */
function AccountsPanel({ summary }: { summary: AccountsSummary | null | undefined }) {
  if (summary === undefined) return <p className="doc-hint">Loading your accounts…</p>;
  if (summary === null) return <p className="doc-hint">Couldn't load your accounts.</p>;
  const balances = summary.balances.filter((b) => b.type in BALANCE_LABELS);
  return (
    <section className="accounts">
      <h2>Your accounts</h2>
      <p className="doc-hint">As of {shortDate(summary.as_of)} (demo date)</p>
      <dl className="balances">
        {balances.map((b) => (
          <div key={b.account_id} className="balance-row">
            <dt>{b.type === "credit" && b.balance_cents > 0 ? "Card credit" : BALANCE_LABELS[b.type]}</dt>
            {/* A card balance is negative when money is owed: shown as a positive amount owed. */}
            <dd className={b.type !== "credit" && b.balance_cents < 0 ? "negative" : ""}>
              {usd(b.type === "credit" ? Math.abs(b.balance_cents) : b.balance_cents)}
            </dd>
          </div>
        ))}
      </dl>
      <h3>Recent transactions</h3>
      <ul className="txn-list">
        {summary.transactions.map((t) => (
          <li key={t.txn_id}>
            <span className="txn-what" title={t.description ?? undefined}>
              {t.merchant || t.description || "Transaction"}
              <span className="txn-date">{shortDate(t.posted_on)}</span>
            </span>
            <span className={t.amount_cents < 0 ? "" : "positive"}>{usd(t.amount_cents)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** What the assistant can read for this user (row-level security decides), in the version in force. */
function DocumentList({ documents, open }: { documents: DocumentInfo[]; open: boolean }) {
  return (
    <details className="doc-section" open={open}>
      <summary>Documents ({documents.length})</summary>
      {documents.length === 0 ? (
        <p className="doc-hint">No documents loaded yet.</p>
      ) : (
        <div className="doc-list">
          <p className="doc-hint">The documents FinPilot can read for you, in the version in force today.</p>
          {documents.map((doc) => (
            <div key={doc.doc_id} className="doc-item">
              <span className="doc-name" title={doc.filename}>
                {doc.filename.replace(/^public\//, "")}
              </span>
            </div>
          ))}
        </div>
      )}
    </details>
  );
}
