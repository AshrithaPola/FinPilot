import { useEffect, useRef, useState } from "react";
import type { User } from "firebase/auth";
import AuthGate from "./AuthGate";
import Chat from "./Chat";
import LogoMark from "./Logo";
import Traces from "./Traces";
import { apiFetch, type AccountsSummary, type DocumentInfo, type Me } from "./api";
import "./index.css";

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

type Theme = "light" | "dark";

/** The theme index.html set before the first paint; a choice is remembered on this device. */
function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.dataset.theme === "dark" ? "dark" : "light"
  );
  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("finpilot-theme", next);
    } catch {
      // Storage blocked (private window): the choice lasts for this visit.
    }
    setTheme(next);
  }
  return [theme, toggle];
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
  const [theme, toggleTheme] = useTheme();
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

  const firstName = summary?.first_name ?? null;

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <LogoMark />
          <span className="brand-name">FinPilot</span>
        </div>
        <div className="header-tools">
          {me.demo_today && (
            <span
              className="demo-badge"
              title="This demo's data ends on this day, so FinPilot treats it as today."
            >
              Demo date {shortDate(me.demo_today)}
            </span>
          )}
          <button
            className="icon-btn"
            onClick={toggleTheme}
            aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"}
            title={theme === "dark" ? "Light theme" : "Dark theme"}
          >
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>
          <AccountMenu
            greeting={firstName ? `Hi, ${firstName}` : "Hi there"}
            initial={(firstName ?? user.email ?? "?").charAt(0).toUpperCase()}
            email={user.email ?? ""}
            roleLabel={isCustomer ? "Customer" : isAdmin ? "Staff, admin" : "Staff"}
            onTraces={isAdmin ? () => setView(view === "chat" ? "traces" : "chat") : undefined}
            tracesLabel={view === "chat" ? "Request traces" : "Back to chat"}
            onSignOut={onSignOut}
          />
        </div>
      </header>

      {view === "traces" && isAdmin ? (
        <main className="app-main traces-main">
          <Traces />
        </main>
      ) : (
        <main className="app-main">
          <aside className="side-panel">
            {isCustomer && <AccountsPanel summary={summary} />}
            <DocumentList documents={documents} open={!isCustomer} />
          </aside>
          <Chat isCustomer={isCustomer} />
        </main>
      )}
    </div>
  );
}

function AccountMenu(props: {
  greeting: string; initial: string; email: string; roleLabel: string;
  onTraces?: () => void; tracesLabel: string; onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function close(e: MouseEvent | KeyboardEvent) {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  return (
    <div className="account-menu" ref={ref}>
      <button className="account-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="greeting">{props.greeting}</span>
        <span className="avatar" aria-hidden="true">{props.initial}</span>
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-who">
            <span>{props.email}</span>
            <span className="muted">{props.roleLabel}</span>
          </div>
          {props.onTraces && (
            <button role="menuitem" onClick={() => { setOpen(false); props.onTraces!(); }}>
              {props.tracesLabel}
            </button>
          )}
          <button role="menuitem" onClick={props.onSignOut}>Sign out</button>
        </div>
      )}
    </div>
  );
}

const PHONE = window.matchMedia("(max-width: 720px)"); // the layout's breakpoint (index.css)

// Checking, then savings, then the card: how people read their money.
const ACCOUNT_ORDER = ["checking", "savings", "credit"];
const ACCOUNT_NAMES: Record<string, string> = { checking: "Checking", savings: "Savings", credit: "Credit card" };

/** Checking, savings and what's owed on the card, and the 5 latest transactions. */
function AccountsPanel({ summary }: { summary: AccountsSummary | null | undefined }) {
  if (summary === undefined) return <p className="panel-note">Loading your accounts…</p>;
  if (summary === null) return <p className="panel-note">Your accounts couldn't be loaded. Reload the page to try again.</p>;
  const balances = summary.balances
    .filter((b) => ACCOUNT_ORDER.includes(b.type))
    .sort((a, b) => ACCOUNT_ORDER.indexOf(a.type) - ACCOUNT_ORDER.indexOf(b.type));
  const checking = balances.find((b) => b.type === "checking");
  return (
    // Open beside the chat; folded on a phone, so the chat comes first.
    <details className="accounts" open={!PHONE.matches}>
      <summary>
        <h2>Your accounts</h2>
        {checking && <span className="summary-balance">Checking {usd(checking.balance_cents)}</span>}
      </summary>
      <dl className="balances">
        {balances.map((b) => {
          const card = b.type === "credit";
          // A card balance is negative when money is owed: shown as the amount owed.
          const label = card ? (b.balance_cents > 0 ? "Card credit" : "Card balance owed") : ACCOUNT_NAMES[b.type];
          return (
            <div key={b.account_id} className={`balance-row ${b.type}`}>
              <dt>
                {ACCOUNT_NAMES[b.type]}
                {card && <span className="balance-note">{label}</span>}
              </dt>
              <dd className={!card && b.balance_cents < 0 ? "negative" : ""}>
                {usd(card ? Math.abs(b.balance_cents) : b.balance_cents)}
              </dd>
            </div>
          );
        })}
      </dl>
      <h3>Recent activity</h3>
      <ul className="txn-list">
        {summary.transactions.map((t) => (
          <li key={t.txn_id}>
            <span className="txn-what" title={t.description ?? undefined}>
              <span className="txn-name">{t.merchant || t.description || "Transaction"}</span>
              <span className="txn-date">
                {shortDate(t.posted_on)}
                {t.category === "card_payment" ? ", paid from checking" : ""}
              </span>
            </span>
            <span className={`txn-amount${t.amount_cents > 0 ? " positive" : ""}`}>
              {t.amount_cents > 0 ? "+" : "−"}
              {usd(Math.abs(t.amount_cents))}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** What the assistant can read for this user (row-level security decides), in the version in force. */
function DocumentList({ documents, open }: { documents: DocumentInfo[]; open: boolean }) {
  return (
    <details className="doc-section" open={open}>
      <summary>Bank documents ({documents.length})</summary>
      {documents.length === 0 ? (
        <p className="panel-note">No documents loaded yet.</p>
      ) : (
        <div className="doc-list">
          <p className="panel-note">The current versions FinPilot reads to answer you.</p>
          {documents.map((doc) => (
            <div key={doc.doc_id} className="doc-item">
              <span className="doc-name" title={doc.filename}>
                {doc.filename.replace(/^(public|internal|rates|api)\//, "")}
              </span>
            </div>
          ))}
        </div>
      )}
    </details>
  );
}

function SunIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  );
}
