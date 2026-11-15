import { useEffect, useRef, useState } from "react";
import { Volume2, VolumeX } from "lucide-react";
import { useAgent } from "../agent.jsx";
import { brandParts } from "../ui.jsx";

// The robot's spoken-style briefing, typed out once (any key skips), brand names as links.
export default function Briefing() {
  const { brief, skip, speak, voice, setVoice, flag, status } = useAgent();
  const [n, setN] = useState(0);
  const spoke = useRef(false);
  const total = brief ? brief.spoken.length : 0;

  useEffect(() => {
    if (!brief) return undefined;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (skip || reduced) { setN(total); return undefined; }
    flag("speaking", true);
    const t = setInterval(() => setN((x) => { const y = Math.min(total, x + 2); if (y >= total) clearInterval(t); return y; }), 22);
    return () => { clearInterval(t); flag("speaking", false); };
  }, [brief, skip, total, flag]);
  useEffect(() => { if (n >= total && total) flag("speaking", false); }, [n, total, flag]);
  useEffect(() => { if (brief && voice && !spoke.current) { spoke.current = true; speak(brief.spoken); } }, [brief, voice, speak]);

  if (status && !status.available) {
    return <p className="briefing">The agent is offline. {status.reason} Every number on this page still comes straight from the engine.</p>;
  }
  if (!brief) return <p className="briefing"><span className="sk" style={{ width: "80%" }} /><span className="sk" style={{ width: "55%", marginTop: 8 }} /></p>;

  // Reveal the parts up to n characters; brand parts become links once fully revealed.
  let left = n;
  const out = brief.parts.map((p, i) => {
    const text = p.text ?? brandParts(p.brand).label;
    if (left <= 0) return null;
    const shown = text.slice(0, left);
    left -= text.length;
    return p.brand && shown.length === text.length
      ? <a key={i} href={`#/brand/${encodeURIComponent(p.brand)}`} className="agent-link">{shown}</a>
      : <span key={i}>{shown}</span>;
  });
  return (
    <div className="briefing-wrap">
      <p className="briefing" aria-live="polite">{out}{n < total && <span className="caret" aria-hidden="true" />}</p>
      <div className="btn-row">
        <button type="button" className="btn ghost" onClick={() => { if (!voice) { setVoice(true); setTimeout(() => speak(brief.spoken), 50); } else setVoice(false); }}
                aria-pressed={voice} title={voice ? "Mute the agent's voice" : "Hear the briefing"}>
          {voice ? <Volume2 className="ico-sm" /> : <VolumeX className="ico-sm" />}{voice ? "Voice on" : "Voice off"}
        </button>
      </div>
    </div>
  );
}
