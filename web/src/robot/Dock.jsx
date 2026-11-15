// The docked robot in the corner of every page except Overview and Ask. Click to expand into a compact chat with the
// same agent (same conversation as Ask and iMessage).
import { useEffect, useRef, useState } from "react";
import { Maximize2, Send, X } from "lucide-react";
import { getJSON } from "../api.js";
import { brandsIn, useAgent } from "../agent.jsx";
import Robot, { MOOD_LABEL } from "./Robot.jsx";

export default function Dock() {
  const { status, mood, flag, point, speak, click } = useAgent();
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [brands, setBrands] = useState([]);
  const end = useRef(null);

  useEffect(() => {
    if (!open) return;
    getJSON("/ask/history?limit=6").then((h) => setMsgs(h.messages)).catch(() => {});
    getJSON("/brands").then((d) => setBrands(d.brands.map((b) => b.brand))).catch(() => {});
  }, [open]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs, busy]);

  const send = async () => {
    const t = text.trim();
    if (!t || busy || !status?.available) return;
    click();
    setText(""); flag("listening", false);
    setMsgs((m) => [...m, { who: "owner", channel: "dashboard", text: t }]);
    setBusy(true); flag("thinking", true);
    try {
      const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t }) });
      const b = await r.json().catch(() => ({}));
      const reply = r.ok ? (b.error || b.reply) : (b.detail || `The agent did not answer (${r.status})`);
      setMsgs((m) => [...m, { who: r.ok && !b.error ? "agent" : "error", text: reply }]);
      if (r.ok && !b.error) { point(brandsIn(reply, brands)); speak(reply); }
    } catch (e) {
      setMsgs((m) => [...m, { who: "error", text: String(e.message || e) }]);
    } finally { setBusy(false); flag("thinking", false); }
  };

  return (
    <div className={`dock${open ? " open" : ""}`}>
      {open && (
        <section className="dock-panel glass hud" aria-label="Agent chat">
          <header>
            <b>Agent</b><span className="small muted">{MOOD_LABEL[mood]}</span>
            <span style={{ flex: 1 }} />
            <a className="icon-btn" href="#/ask" title="Open full conversation" onClick={() => setOpen(false)}><Maximize2 className="ico-sm" /></a>
            <button type="button" className="icon-btn" onClick={() => setOpen(false)} aria-label="Close"><X className="ico-sm" /></button>
          </header>
          <div className="dock-log" aria-live="polite">
            {msgs.length === 0 && <p className="note">Ask about orders, stock, festivals or suppliers. Same conversation as iMessage.</p>}
            {msgs.map((m, i) => <div key={i} className={`dmsg ${m.who}`}>{m.text}</div>)}
            {busy && <div className="dmsg agent"><span className="typing" aria-hidden="true"><i /><i /><i /></span></div>}
            <div ref={end} />
          </div>
          <form onSubmit={(e) => { e.preventDefault(); send(); }}>
            <input type="text" value={text} disabled={!status?.available} aria-label="Message the agent"
                   placeholder={status?.available ? "Ask the agent" : "The agent is offline"}
                   onFocus={() => flag("listening", true)} onBlur={() => flag("listening", false)} onChange={(e) => setText(e.target.value)} />
            <button className="btn primary" type="submit" disabled={busy || !text.trim() || !status?.available} aria-label="Send"><Send className="ico-sm" /></button>
          </form>
        </section>
      )}
      <button type="button" className="dock-bot" onClick={() => { click(); setOpen(!open); }} aria-label={open ? "Hide the agent" : "Talk to the agent"} aria-expanded={open}>
        <Robot variant="dock" showLabel={false} onClick={() => { click(); setOpen((o) => !o); }} />
        {mood === "alert" && !open && <span className="dock-ping" aria-hidden="true" />}
      </button>
    </div>
  );
}
