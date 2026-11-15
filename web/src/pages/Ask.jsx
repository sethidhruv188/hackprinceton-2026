import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON } from "../api.js";
import { useData } from "../useData.js";
import { useSettings } from "../settings.jsx";
import { ErrorBox, Loading, Source, Tag } from "../components.jsx";
import { brandParts } from "../ui.jsx";
import { brandsIn, useAgent } from "../agent.jsx";
import Robot from "../robot/Robot.jsx";

const BASE_SUGGESTIONS = [
  "Which brands will stock out before Diwali?",
  "What do I need to order this week?",
  "Expect 30% more moong in December, we're running a promo",
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

// Markdown-lite for agent replies: paragraphs, "- " lists, | tables |, **bold**, links; brand names become links.
function inline(text, brands) {
  const parts = [];
  const re = /(\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s)]+)/g;
  let last = 0, m;
  const pushText = (t) => {
    if (!brands.length) { parts.push(t); return; }
    const bre = new RegExp(`\\b(${brands.map((b) => b.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\b`, "g");
    let i = 0, x;
    while ((x = bre.exec(t))) {
      if (x.index > i) parts.push(t.slice(i, x.index));
      const b = brands.find((y) => y.label === x[1]);
      parts.push(<a key={`b${parts.length}`} href={`#/brand/${encodeURIComponent(b.name)}`}>{x[1]}</a>);
      i = x.index + x[1].length;
    }
    if (i < t.length) parts.push(t.slice(i));
  };
  while ((m = re.exec(text))) {
    if (m.index > last) pushText(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) parts.push(<b key={`s${parts.length}`}>{tok.slice(2, -2)}</b>);
    else if (tok.startsWith("[")) { const [, l, u] = tok.match(/\[([^\]]+)\]\(([^)]+)\)/); parts.push(<a key={`l${parts.length}`} href={u} target="_blank" rel="noreferrer noopener">{l}</a>); }
    else parts.push(<a key={`u${parts.length}`} href={tok} target="_blank" rel="noreferrer noopener">{tok.replace(/^https?:\/\/(www\.)?/, "").slice(0, 40)}</a>);
    last = m.index + tok.length;
  }
  if (last < text.length) pushText(text.slice(last));
  return parts;
}

function Rich({ text, brands }) {
  const blocks = [];
  const lines = String(text).split("\n");
  for (let i = 0; i < lines.length;) {
    const l = lines[i];
    if (/^\s*\|.*\|\s*$/.test(l)) {
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(lines[i]); i += 1; }
      const cells = rows.filter((r) => !/^\s*\|?\s*:?-{3,}/.test(r)).map((r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
      blocks.push(<table key={blocks.length}><thead><tr>{cells[0].map((c, j) => <th key={j}>{inline(c, brands)}</th>)}</tr></thead>
        <tbody>{cells.slice(1).map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c, brands)}</td>)}</tr>)}</tbody></table>);
    } else if (/^\s*[-*•]\s+/.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*•]\s+/, "")); i += 1; }
      blocks.push(<ul key={blocks.length}>{items.map((x, k) => <li key={k}>{inline(x, brands)}</li>)}</ul>);
    } else if (l.trim()) {
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*[-*•|]/.test(lines[i])) { para.push(lines[i].replace(/^#+\s*/, "")); i += 1; }
      blocks.push(<p key={blocks.length}>{inline(para.join(" "), brands)}</p>);
    } else i += 1;
  }
  return <>{blocks}</>;
}

// Reveals a reply that just arrived progressively (the API returns the whole answer at once).
function Reveal({ text, brands, onDone }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setN(text.length); onDone?.(); return undefined; }
    const step = Math.max(3, Math.ceil(text.length / 60));
    const t = setInterval(() => setN((x) => { const y = Math.min(text.length, x + step); if (y >= text.length) { clearInterval(t); onDone?.(); } return y; }), 16);
    return () => clearInterval(t);
  }, [text]);
  return <Rich text={text.slice(0, n)} brands={brands} />;
}

function Message({ m, brands = [], reveal, onRevealed }) {
  const who = m.who === "owner" ? "You" : m.who === "agent" ? "Agent" : "Problem";
  return (
    <div className={`msg ${m.who === "owner" ? "you" : m.who}`}>
      <div className="who">
        {who}
        {m.channel && <>{" "}<Tag>{CHANNEL[m.channel] || m.channel}</Tag></>}
        {m.at && <span className="muted" style={{ marginLeft: 8, textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>{when(m.at)}</span>}
      </div>
      <div className="body">{m.who === "agent"
        ? (reveal ? <Reveal text={m.text} brands={brands} onDone={onRevealed} /> : <Rich text={m.text} brands={brands} />)
        : m.text}</div>
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
  const [revealText, setRevealText] = useState(null);   // the reply to animate (only replies given on this page)
  const agent = useAgent();
  const end = useRef(null);
  const box = useRef(null);
  const { data: br } = useData("/brands");
  const brands = (br?.brands || []).map((b) => ({ name: b.brand, label: brandParts(b.brand).label })).filter((b) => b.label.length > 2);
  const urgent = br?.brands?.find((b) => b.next_order?.status === "Order now")?.brand;
  const SUGGESTIONS = urgent ? [BASE_SUGGESTIONS[0], `Why does ${brandParts(urgent).label} need an order?`, ...BASE_SUGGESTIONS.slice(1)] : BASE_SUGGESTIONS;

  useEffect(() => {                                    // "Adjust" in the order drawer opens this page with a draft
    try {
      const pre = sessionStorage.getItem("ask-prefill");
      if (pre) { setText(pre); sessionStorage.removeItem("ask-prefill"); setTimeout(() => box.current?.focus(), 50); }
    } catch { /* storage blocked */ }
  }, []);

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
    agent.flag("thinking", true);
    try {
      const r = await ask(t);
      if (r.error) setPending((p) => [...p, { who: "error", text: r.error }]);
      else {
        setDetails((d) => ({ ...d, [r.reply]: { tools: r.tools, sources: r.sources } }));
        setRevealText(r.reply); agent.flag("speaking", true); setPending([]);
        agent.point(brandsIn(r.reply, brands.map((b) => b.name)));
        agent.speak(r.reply);
      }
      await load();
    } catch (e) {
      setPending((p) => [...p, { who: "error", text: String(e.message || e) }]);
    } finally {
      setBusy(false);
      agent.flag("thinking", false);
      box.current?.focus();
    }
  };

  const messages = [...history.map((m) => (m.who === "agent" && details[m.text] ? { ...m, ...details[m.text] } : m)), ...pending];

  return (
    <div className="chat" data-ready="ask">
      <header className="page-head hero hud" style={{ gridTemplateColumns: "220px minmax(0,1fr)", padding: "8px 20px", marginBottom: 16 }}>
        <div style={{ height: 220 }}><Robot variant="hero" onClick={() => box.current?.focus()} /></div>
        <div>
          <p className="hero-title">Agent</p>
          <h2>Ask the agent</h2>
          <p className="sub muted small" style={{ margin: 0 }}>
            {status.available ? <>Model {status.model}. Web search {status.research?.web_search ? "on" : "off"}.</> : status.reason}
            {" "}One conversation with iMessage: texts you send the agent appear here too.
            {settings && <> Answers use the plan as of the date above.</>}
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
        {messages.map((m, i) => <Message key={`${m.at}-${i}`} m={m} brands={brands}
                                         reveal={m.who === "agent" && m.text === revealText && i === messages.length - 1}
                                         onRevealed={() => { setRevealText(null); agent.flag("speaking", false); }} />)}
        {busy && <div className="thinking" role="status"><span className="typing" aria-hidden="true"><i /><i /><i /></span>Working on it</div>}
        <div ref={end} />
      </div>

      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <div>
          <label className="sr-only" htmlFor="ask-text">Message</label>
          <textarea id="ask-text" ref={box} value={text} maxLength={2000} disabled={!status.available}
                    placeholder={status.available ? "Type a question. Enter to send, Shift+Enter for a new line." : "The agent is offline"}
                    onChange={(e) => setText(e.target.value)}
                    onFocus={() => agent.flag("listening", true)} onBlur={() => agent.flag("listening", false)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        </div>
        <div className="side">
          <button className="btn primary" type="submit" disabled={!status.available || busy || !text.trim()}>Send</button>
        </div>
      </form>
    </div>
  );
}
