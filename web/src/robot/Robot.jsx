// The robot as used by pages: lazy-loads the 3D body (pages are usable before it arrives), falls back to a static
// image when WebGL is unavailable, and shows the state as words (never colour alone).
import { lazy, Suspense, useMemo } from "react";
import { useAgent } from "../agent.jsx";

const Robot3D = lazy(() => import("./Robot3D.jsx"));
export const MOOD_LABEL = {
  idle: "Ready", listening: "Listening", thinking: "Thinking", speaking: "Speaking", alert: "Needs your attention", offline: "Offline",
};
const MOOD_COLOR = { idle: "#22d3ee", listening: "#38bdf8", thinking: "#a78bfa", speaking: "#2dd4bf", alert: "#fb923c", offline: "#52525b" };

function hasWebGL() {
  try {
    const c = document.createElement("canvas");
    return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl")));
  } catch { return false; }
}

// Static image: the same robot drawn flat (used while loading and without WebGL).
export function RobotStill({ mood = "idle" }) {
  const c = MOOD_COLOR[mood];
  return (
    <svg viewBox="0 0 200 200" width="100%" height="100%" role="img" aria-label={`Agent: ${MOOD_LABEL[mood]}`}>
      <defs>
        <linearGradient id="rm" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#e4e6ea" /><stop offset="1" stopColor="#8d929b" /></linearGradient>
        <radialGradient id="rg"><stop offset="0" stopColor={c} stopOpacity="0.9" /><stop offset="1" stopColor={c} stopOpacity="0" /></radialGradient>
      </defs>
      <ellipse cx="100" cy="186" rx="52" ry="6" fill="#000" opacity="0.25" />
      <rect x="58" y="112" width="84" height="66" rx="18" fill="url(#rm)" />
      <circle cx="100" cy="142" r="14" fill="url(#rg)" /><circle cx="100" cy="142" r="5" fill={c} />
      <rect x="92" y="98" width="16" height="16" rx="4" fill="#26292f" />
      <rect x="54" y="40" width="92" height="64" rx="18" fill="url(#rm)" />
      <rect x="62" y="56" width="76" height="30" rx="10" fill="#070a10" />
      <rect x="74" y="68" width="18" height="5" rx="2.5" fill={c} /><rect x="108" y="68" width="18" height="5" rx="2.5" fill={c} />
      <rect x="44" y="60" width="10" height="24" rx="4" fill="#3a3e46" /><rect x="146" y="60" width="10" height="24" rx="4" fill="#3a3e46" />
    </svg>
  );
}

export function Waveform({ on }) {
  return (
    <span className={`wave${on ? " on" : ""}`} aria-hidden="true">
      {Array.from({ length: 5 }, (_, i) => <i key={i} style={{ animationDelay: `${i * 90}ms` }} />)}
    </span>
  );
}

export default function Robot({ variant = "hero", onClick, showLabel = true }) {
  const { mood } = useAgent();
  const gl = useMemo(hasWebGL, []);
  const open = onClick || (() => { window.location.hash = "/ask"; });
  return (
    <div className={`robot robot-${variant} mood-${mood}`}>
      <div className="robot-stage" onClick={gl ? undefined : open} role={gl ? undefined : "button"} tabIndex={gl ? undefined : 0}
           onKeyDown={(e) => { if (e.key === "Enter") open(); }} aria-label="Open the agent">
        {gl ? (
          <Suspense fallback={<RobotStill mood={mood} />}>
            <Robot3D mood={mood} onClick={open} camera={variant === "dock" ? [0, 0.9, 4.2] : [0, 0.4, 5.2]} fov={variant === "dock" ? 30 : 34} />
          </Suspense>
        ) : <RobotStill mood={mood} />}
      </div>
      {showLabel && (
        <div className="robot-state" role="status">
          <span className="robot-dot" aria-hidden="true" />{MOOD_LABEL[mood]}<Waveform on={mood === "speaking"} />
        </div>
      )}
    </div>
  );
}
