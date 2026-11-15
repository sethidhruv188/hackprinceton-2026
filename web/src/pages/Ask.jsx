import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON } from "../api.js";
import { useData } from "../useData.js";
import { useSettings } from "../settings.jsx";
import { ErrorBox, Loading, Source, Tag } from "../components.jsx";

const SUGGESTIONS = [
  "What do I need to order this week?",
  "Which brands are below three months of stock?",
  "Expect 30% more moong in December, we're running a promo",
  "Which harvest buys are coming up?",
];
const CHANNEL = { imessage: "iMessage", dashboard: "Dashboard" };
const REFRESH_MS = 15000;          // picks up messages sent from iMessage while this page is open

const fmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const when = (s) => { const d = new Date(s); return Number.isNaN(d.getTime()) ? "" : fmt.format(d); };

async function ask(text) {
  const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  let body = null;
  try { body = await r.json(); } catch { /* not json */ }
  if (!r.ok) throw new Error(body?.detail || `The agent did not answer (${r.status})`);
  return body;
}

function Message({ m }) {
  const who = m.who === "owner" ? "You" : m.who === "agent" ? "Agent" : "Problem";
  return (
    <div className={`msg ${m.who === "owner" ? "you" : m.who}`}>
      <div className="who">
        {who}
        {m.channel && <>{" "}<Tag>{CHANNEL[m.channel] || m.channel}</Tag></>}
        {m.at && <span className="muted" style={{ marginLeft: 8, textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>{when(m.at)}</span>}
      </div>
      <div className="body">{m.text}</div>
      {(m.tools?.length > 0 || m.sources?.length > 0) && (
        <details>
          <summary>How this was worked out</summary>
          {m.tools?.length > 0 && <div>Tools used: {m.tools.map((t) => t.name || t).join(", ")}</div>}
          {m.sources?.length > 0 && <ul>{m.sources.map((s) => <li key={s}><Source url={s} /></li>)}</ul>}
        </details>
      )}
    </div>
  );
}

export default function Ask() {
  const { data: status, error } = useData("/ask/status");
  const { settings, rev } = useSettings();
  const [history, setHistory] = useState(null);
  const [pending, setPending] = useState([]);       // messages sent from this page and not yet in the history
  const [details, setDetails] = useState({});       // reply text -> {tools, sources}, for replies given on this page
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef(null);
  const box = useRef(null);

  const load = useCallback(() => getJSON("/ask/history?limit=60").then((h) => setHistory(h.messages)).catch(() => setHistory((x) => x || [])), []);
  useEffect(() => { load(); }, [load, rev]);
  useEffect(() => {
    if (busy) return undefined;
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [busy, load]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [history, pending, busy]);

  if (error) return <ErrorBox error={error} />;
  if (!status || history === null) return <Loading what="Loading the conversation" />;

  const send = async (q) => {
    const t = (q ?? text).trim();
    if (!t || busy) return;
    setText("");
    setPending([{ who: "owner", channel: "dashboard", text: t, at: new Date().toISOString() }]);
    setBusy(true);
    try {
      const r = await ask(t);
      if (r.error) setPending((p) => [...p, { who: "error", text: r.error }]);
      else { setDetails((d) => ({ ...d, [r.reply]: { tools: r.tools, sources: r.sources } })); setPending([]); }
      await load();
    } catch (e) {
      setPending((p) => [...p, { who: "error", text: String(e.message || e) }]);
    } finally {
      setBusy(false);
      box.current?.focus();
    }
  };

  const messages = [...history.map((m) => (m.who === "agent" && details[m.text] ? { ...m, ...details[m.text] } : m)), ...pending];

  return (
    <div className="chat" data-ready="ask">
      <header className="page-head">
        <div>
          <h2>Ask the agent</h2>
          <p className="sub muted small" style={{ margin: 0 }}>
            {status.available ? <>Model {status.model}. Web search {status.research?.web_search ? "on" : "off"}.</> : status.reason}
            {" "}One conversation with iMessage: texts you send the agent appear here too.
            {settings && <> Answers use the {settings.mode === "demo" ? "demo" : "real"} data as of the plan date.</>}
          </p>
        </div>
      </header>

      <div className="chat-log" aria-live="polite" aria-busy={busy}>
        {messages.length === 0 && (
          <div className="ask-empty">
            <p className="note">
              Ask about orders, stock, festivals, harvests or suppliers, or tell the agent how demand will change
              ("30% more moong in December"). It reads changes back and applies them only after you say yes.
            </p>
            {status.available && (
              <>
                <div className="field-label" style={{ marginTop: 16 }}>Try</div>
                <div className="suggest">
                  {SUGGESTIONS.map((s) => <button key={s} type="button" className="btn" onClick={() => send(s)}>{s}</button>)}
                </div>
              </>
            )}
          </div>
        )}
        {messages.map((m, i) => <Message key={`${m.at}-${i}`} m={m} />)}
        {busy && <div className="thinking" role="status">Working on it</div>}
        <div ref={end} />
      </div>

      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <div>
          <label className="sr-only" htmlFor="ask-text">Message</label>
          <textarea id="ask-text" ref={box} value={text} maxLength={2000} disabled={!status.available}
                    placeholder={status.available ? "Type a question. Enter to send, Shift+Enter for a new line." : "The agent is offline"}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        </div>
        <div className="side">
          <button className="btn primary" type="submit" disabled={!status.available || busy || !text.trim()}>Send</button>
        </div>
      </form>
    </div>
  );
}
