import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import AuthGate from "./AuthGate";
import Chat from "./Chat";
import Traces from "./Traces";
import { apiFetch, type DocumentInfo, type Me } from "./api";
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

export default function App() {
  return (
    <AuthGate>
      {(me, user, signOut) => <Workspace me={me} user={user} onSignOut={signOut} />}
    </AuthGate>
  );
}

function Workspace({ me, user, onSignOut }: { me: Me; user: User; onSignOut: () => void }) {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [view, setView] = useState<"chat" | "traces">("chat");
  const isAdmin = me.role === "admin";

  useEffect(() => {
    // setState only in the async callback, never in the effect body; skipped
    // if the component unmounted first.
    let active = true;
    fetchDocuments().then((docs) => {
      if (active && docs) setDocuments(docs);
    });
    return () => {
      active = false;
    };
  }, []);

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
          <DocumentList documents={documents} />
          <Chat isCustomer={!!me.customer_id} demoToday={me.demo_today} />
        </main>
      )}
    </div>
  );
}

/** What the assistant can read for this user (row-level security decides). */
function DocumentList({ documents }: { documents: DocumentInfo[] }) {
  return (
    <div className="upload-panel">
      <h2>Documents</h2>
      {documents.length === 0 ? (
        <p className="doc-hint">No documents loaded yet.</p>
      ) : (
        <div className="doc-list">
          <p className="doc-hint">The documents FinPilot can read for you, in the version in force.</p>
          {documents.map((doc) => (
            <div key={doc.doc_id} className="doc-item">
              <span className="doc-name" title={doc.filename}>
                {doc.filename.replace(/^public\//, "")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
