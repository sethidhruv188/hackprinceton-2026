// The agent's shared state: what the robot shows, the briefing, voice, sound and "point at" events.
// Everything here is derived from the API; the robot never invents a number.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { getJSON } from "./api.js";
import { useSettings } from "./settings.jsx";
import { brandParts } from "./ui.jsx";

const Ctx = createContext(null);
const n0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const pref = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v === "1"; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, v ? "1" : "0"); } catch { /* storage blocked */ } };

// ---------------------------------------------------------------- sound: soft clicks and a confirmation tone (WebAudio)
let audio = null;
function tone(freq, ms, gain = 0.04, type = "sine") {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    const o = audio.createOscillator(), g = audio.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(gain, audio.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + ms / 1000);
    o.connect(g).connect(audio.destination);
    o.start(); o.stop(audio.currentTime + ms / 1000);
  } catch { /* no audio */ }
}

// ---------------------------------------------------------------- briefing from real data only
export function buildBriefing(summary, brands, questions) {
  if (!summary || !brands) return null;
  const urgentRank = { "Too late for full lead time: expedite": 0, "Order now": 1, "Order this month": 2, "Coming up": 3 };
  const withOrder = brands.brands.filter((b) => b.next_order);
  const top = [...withOrder].sort((a, b) => (urgentRank[a.next_order.status] ?? 9) - (urgentRank[b.next_order.status] ?? 9)
    || a.next_order.order_by_safe.localeCompare(b.next_order.order_by_safe))[0];
  const below = brands.brands.reduce((n, b) => n + (b.below_floor_now || 0), 0);
  const open = questions ? questions.questions.filter((q) => q.answer == null || q.answer === "").length : null;
  const parts = [];       // [{text}, {brand}] so brand names become links
  const s = summary;
  if (s.orders_now > 0) parts.push({ text: `${n0.format(s.orders_now)} brand order${s.orders_now === 1 ? "" : "s"} to place now, ${n0.format(s.orders_now_cases)} cases. ` });
  else parts.push({ text: "No brand orders are due right now. " });
  if (top) parts.push({ text: "Most urgent: " }, { brand: top.brand }, { text: `, ${n0.format(top.next_order.cases)} cases. ` });
  if (below > 0) parts.push({ text: `${n0.format(below)} product${below === 1 ? " is" : "s are"} below the 3-month floor. ` });
  if (s.orders_too_late > 0) parts.push({ text: `${n0.format(s.orders_too_late)} order${s.orders_too_late === 1 ? " is" : "s are"} already too late for sea freight. ` });
  if (open) parts.push({ text: `${n0.format(open)} question${open === 1 ? "" : "s"} need your answer.` });
  const spoken = parts.map((p) => p.text ?? brandParts(p.brand).label).join("");
  return { parts, spoken, alert: s.orders_now > 0 || s.orders_too_late > 0 || !!open };
}

export function AgentProvider({ children }) {
  const { rev } = useSettings();
  const [status, setStatus] = useState(null);          // /ask/status
  const [brief, setBrief] = useState(null);
  const [activity, setActivity] = useState({ listening: false, thinking: false, speaking: false });
  const [voice, setVoice] = useState(() => pref("agent-voice", false));
  const [sound, setSound] = useState(() => pref("ui-sound", false));
  const [skip, setSkip] = useState(false);             // any key skips the intro animations
  const counter = useRef({ listening: 0, thinking: 0, speaking: 0 });

  useEffect(() => {
    let live = true;
    Promise.allSettled([getJSON("/ask/status"), getJSON("/summary"), getJSON("/brands"), getJSON("/questions")]).then(([a, s, b, q]) => {
      if (!live) return;
      setStatus(a.status === "fulfilled" ? a.value : { available: false, reason: "The dashboard server is not reachable." });
      setBrief(buildBriefing(s.value, b.value, q.status === "fulfilled" ? q.value : null));
    });
    return () => { live = false; };
  }, [rev]);

  useEffect(() => { if (skip) window.__skipIntro = true; }, [skip]);
  useEffect(() => {
    const on = () => setSkip(true);
    window.addEventListener("keydown", on, { once: true });
    const t = setTimeout(() => setSkip(true), 4200);   // the intro never runs longer than about 4 seconds
    return () => { window.removeEventListener("keydown", on); clearTimeout(t); };
  }, []);

  // Pages report what the user / agent is doing; counters so overlapping callers don't cancel each other.
  const flag = useCallback((k, on) => {
    counter.current[k] = Math.max(0, counter.current[k] + (on ? 1 : -1));
    setActivity((x) => ({ ...x, [k]: counter.current[k] > 0 }));
  }, []);

  const speak = useCallback((text) => {
    if (!voice || !window.speechSynthesis || !text) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.03; u.pitch = 0.95;
    u.onstart = () => flag("speaking", true);
    u.onend = u.onerror = () => flag("speaking", false);
    window.speechSynthesis.speak(u);
  }, [voice, flag]);

  const click = useCallback(() => { if (sound) tone(1400, 40, 0.025, "triangle"); }, [sound]);
  const confirm = useCallback(() => { if (sound) { tone(660, 120); setTimeout(() => tone(990, 160), 110); } }, [sound]);

  // "Point at": highlight brands the agent mentioned on whatever page is showing them.
  const point = useCallback((names) => {
    if (names?.length) window.dispatchEvent(new CustomEvent("agent-point", { detail: { brands: names } }));
  }, []);

  const mood = !status ? "thinking" : !status.available ? "offline" : activity.thinking ? "thinking"
    : activity.speaking ? "speaking" : activity.listening ? "listening" : brief?.alert ? "alert" : "idle";

  const value = useMemo(() => ({
    status, brief, mood, skip, flag, speak, voice, sound, click, confirm, point,
    setVoice: (v) => { setVoice(v); save("agent-voice", v); if (!v) window.speechSynthesis?.cancel(); },
    setSound: (v) => { setSound(v); save("ui-sound", v); if (v) tone(880, 80); },
  }), [status, brief, mood, skip, flag, speak, voice, sound, click, confirm, point]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useAgent = () => useContext(Ctx);

// Listen for "agent-point" and return the brand names currently highlighted (cleared after a few seconds).
export function usePointed() {
  const [names, setNames] = useState([]);
  useEffect(() => {
    let t;
    const on = (e) => { setNames(e.detail.brands); clearTimeout(t); t = setTimeout(() => setNames([]), 6000); };
    window.addEventListener("agent-point", on);
    return () => { window.removeEventListener("agent-point", on); clearTimeout(t); };
  }, []);
  return names;
}

// Brand names mentioned in a reply (matched against the real brand list).
export function brandsIn(text, brandList) {
  const t = String(text || "");
  return (brandList || []).filter((b) => {
    const label = brandParts(b).label;
    return label.length > 2 && new RegExp(`\\b${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(t);
  });
}
