// Shared UI building blocks for the redesign. Presentation only: every number passed in comes from the API.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertOctagon, AlertTriangle, CalendarClock, CheckCircle2, CircleSlash, Clock, Info, PauseCircle, X,
} from "lucide-react";

const n0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const n1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
export const fmt0 = (x) => (x == null || Number.isNaN(x) ? "-" : n0.format(x));
export const fmt1 = (x) => (x == null || Number.isNaN(x) ? "-" : n1.format(x));

// ---------------------------------------------------------------- count-up number (200-700ms, eases out)
export function useCountUp(target, ms = 650) {
  const [v, setV] = useState(typeof target === "number" ? 0 : target);
  useEffect(() => {
    if (typeof target !== "number" || Number.isNaN(target)) { setV(target); return undefined; }
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setV(target); return undefined; }
    let raf, t0;
    const step = (t) => {
      t0 = t0 ?? t;
      const p = Math.min(1, (t - t0) / ms);
      setV(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

export function CountUp({ value, format = fmt0 }) {
  const v = useCountUp(value, window.__skipIntro ? 1 : 650);
  return <>{typeof value === "number" ? format(v) : value}</>;
}

// ---------------------------------------------------------------- KPI card; clickable when onClick is given
export function KPI({ icon: Icon, k, v, unit, d, tone = "", onClick, pressed, big, tip }) {
  const body = (
    <>
      <div className="k">
        {Icon && <span className="ico-wrap"><Icon className="ico" aria-hidden="true" /></span>}
        <span>{k}</span>
        {tip && <Tip text={tip} />}
      </div>
      <div className="v"><CountUp value={v} />{unit && <small>{unit}</small>}</div>
      {d && <div className="d">{d}</div>}
    </>
  );
  const cls = `kpi tilt ${tone} ${big ? "big" : ""}`;
  return onClick
    ? <TiltButton className={cls} onClick={onClick} pressed={pressed}>{body}</TiltButton>
    : <TiltDiv className={cls}>{body}</TiltDiv>;
}

function TiltButton({ className, onClick, pressed, children }) {
  const ref = useTilt();
  return <button ref={ref} type="button" className={className} onClick={onClick} aria-pressed={!!pressed}>{children}</button>;
}
function TiltDiv({ className, children }) {
  const ref = useTilt();
  return <div ref={ref} className={className}>{children}</div>;
}

// ---------------------------------------------------------------- status pills: icon + text, never color alone
const PILLS = {
  "Order now": ["crit", AlertOctagon, "Order now"],
  "Order this month": ["warn", Clock, "This month"],
  "Coming up": ["info", CalendarClock, "Coming up"],
  "Too late for full lead time: expedite": ["crit", AlertTriangle, "Too late: expedite"],
  "On hold": ["neutral", PauseCircle, "On hold"],
  "On track": ["ok", CheckCircle2, "On track"],
  "Not in plan": ["neutral", CircleSlash, "Not in plan"],
};
export function Pill({ tone = "neutral", icon: Icon, children, title }) {
  return (
    <span className={`pill ${tone}`} title={title}>
      {Icon && <Icon className="ico-sm" aria-hidden="true" />}{children}
    </span>
  );
}
export function StatusPill({ status, label }) {
  const [tone, Icon, text] = PILLS[status] || ["neutral", Info, status];
  return <Pill tone={tone} icon={Icon}>{label || text}</Pill>;
}

// ---------------------------------------------------------------- brands: "RM (item code)" is a code, not a name
export function brandParts(name) {
  const s = String(name ?? "");
  const m = s.match(/^(.*?)\s*\((item code|code)\)\s*$/i);
  return m ? { label: m[1], code: true } : { label: s, code: false };
}
export function BrandName({ name, avatar = true, sub, href }) {
  const { label, code } = brandParts(name);
  const inner = (
    <span className="brand-cell">
      {avatar && <span className="avatar" aria-hidden="true">{code ? "#" : label.slice(0, 2).toUpperCase()}</span>}
      <span style={{ minWidth: 0 }}>
        {code
          ? <span className="chip" title="No brand name in the data: grouped by item-code prefix">{label}</span>
          : <span className="nm">{label}</span>}
        {sub && <div className="sub2">{sub}</div>}
      </span>
    </span>
  );
  return href ? <a href={href} onClick={(e) => e.stopPropagation()}>{inner}</a> : inner;
}

// ---------------------------------------------------------------- stock runway: months of stock vs the 3-month floor
export function RunwayBar({ months, floor = 3, max = 12, label = true }) {
  if (months == null || Number.isNaN(months)) return <span className="muted small">no stock data</span>;
  const w = Math.min(100, (Math.max(months, 0) / max) * 100);
  const tone = months < floor ? "fill-crit" : months < floor + 1.5 ? "fill-warn" : "fill-ok";
  return (
    <div className="runway" title={`${fmt1(months)} months of stock; the floor is ${floor} months`}>
      {label && <div className="lbl"><b>{fmt1(months)} mo</b><span>floor {floor}</span></div>}
      <div className="track">
        <div className={`fill ${tone}`} style={{ width: `${w}%` }} />
        <div className="floor" style={{ left: `${(floor / max) * 100}%` }} aria-hidden="true" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- SKU risk split: below floor now / soon / rest
export function RiskBar({ now = 0, soon = 0, total = 0, compact = false }) {
  const t = Math.max(total, now + soon, 1);
  const healthy = Math.max(t - now - soon, 0);
  return (
    <div className="risk" title={`${now} below the 3-month floor now, ${soon} soon, ${healthy} healthy`}>
      <div className="stack-bar" aria-hidden="true">
        <span className="fill-crit" style={{ width: `${(now / t) * 100}%` }} />
        <span className="fill-warn" style={{ width: `${(soon / t) * 100}%` }} />
        <span className="fill-ok" style={{ width: `${(healthy / t) * 100}%`, opacity: 0.55 }} />
      </div>
      {!compact && (
        <div className="lbl">
          {now > 0 && <span><b className="st-red">{fmt0(now)}</b> now</span>}
          {soon > 0 && <span><b className="st-amber">{fmt0(soon)}</b> soon</span>}
          {now === 0 && soon === 0 && <span className="st-green">none at risk</span>}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- containers: icons with the last one's fill level
export function ContainerFill({ n, lastPct, cbm, estimated }) {
  if (!n) return <span className="muted">-</span>;
  const shown = Math.min(n, 6);
  return (
    <span className="containers" title={`${n} container${n === 1 ? "" : "s"}${lastPct != null ? `, last one ${Math.round(lastPct)}% full` : ""}${cbm != null ? `, ${fmt1(cbm)} CBM` : ""}${estimated ? " (case sizes estimated)" : ""}`}>
      {Array.from({ length: shown }, (_, i) => {
        const last = i === n - 1;
        const pct = last && lastPct != null ? Math.max(4, Math.min(100, lastPct)) : 100;
        return <span key={i} className="ctr" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span>;
      })}
      {n > shown && <span className="more">+{n - shown}</span>}
      <span className="cap">{n}{lastPct != null ? ` · last ${Math.round(lastPct)}%` : ""}</span>
    </span>
  );
}

// ---------------------------------------------------------------- tooltip for terms
export const TERMS = {
  floor: "3-month floor: the minimum stock we want on the shelf, three months of expected sales. Below it, a late ship or a sales spike means empty shelves.",
  cbm: "CBM: cubic metres, the volume a shipment takes in a container. A 40-ft container holds about 67 CBM.",
  safe: "Safe order date: the last day to order if shipping takes the slow end, 130 days. The latest date assumes 105 days.",
  runway: "Months of stock on hand at the recent sales pace (last 6 months).",
  gross: "Gross need: how much you need, without subtracting stock already in the warehouse.",
};
export function Tip({ text, children }) {
  return (
    <span className="tip-wrap" tabIndex={children ? undefined : 0}>
      {children || <Info className="ico-sm info-i" aria-label="What is this?" />}
      <span className="tip-bubble" role="tooltip">{text}</span>
    </span>
  );
}
export function Term({ k, children }) {
  return <Tip text={TERMS[k]}><span className="term" tabIndex={0}>{children}</span></Tip>;
}

// ---------------------------------------------------------------- drawer (right side), closes on Esc / scrim
export function Drawer({ open, onClose, title, children, footer, labelledBy = "drawer-title" }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.activeElement;
    ref.current?.focus({ preventScroll: true });
    ref.current?.querySelector(".drawer-body")?.scrollTo(0, 0);
    const on = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", on);
    return () => { window.removeEventListener("keydown", on); prev?.focus?.(); };
  }, [open, onClose]);
  if (!open) return null;
  // Portal to <body>: page transitions create stacking contexts that would otherwise trap the drawer under the dock.
  return createPortal(
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby={labelledBy} ref={ref} tabIndex={-1}>
        <div className="drawer-head">
          <h2 id={labelledBy}>{title}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X className="ico" /></button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="drawer-foot">{footer}</div>}
      </aside>
    </>,
    document.body,
  );
}

// ---------------------------------------------------------------- toasts
const ToastCtx = createContext(() => {});
export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((text) => {
    const id = Math.random().toString(36).slice(2);
    setItems((x) => [...x, { id, text }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => <div key={t.id} className="toast"><CheckCircle2 className="ico" aria-hidden="true" /><span>{t.text}</span></div>)}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ---------------------------------------------------------------- empty state
export function EmptyState({ icon: Icon = Info, title, children }) {
  return (
    <div className="empty">
      <span className="ico-big"><Icon className="ico" aria-hidden="true" /></span>
      {title && <b>{title}</b>}
      {children && <span>{children}</span>}
    </div>
  );
}

export function SkeletonCards({ n = 4 }) {
  return <div className="kpis" aria-hidden="true">{Array.from({ length: n }, (_, i) => <span key={i} className="sk sk-card" />)}</div>;
}

// ---------------------------------------------------------------- sparkline (tiny SVG, no axes)
export function Sparkline({ values, width = 120, height = 28, color = "var(--chart-1)" }) {
  const v = (values || []).filter((x) => x != null && !Number.isNaN(x));
  if (v.length < 2) return <span className="muted small">no history</span>;
  const max = Math.max(...v, 1), min = Math.min(...v, 0);
  const x = (i) => (i / (v.length - 1)) * (width - 2) + 1;
  const y = (n) => height - 2 - ((n - min) / (max - min || 1)) * (height - 4);
  const d = v.map((n, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(n).toFixed(1)}`).join(" ");
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={`${d} L${x(v.length - 1)},${height} L1,${height} Z`} fill={color} opacity="0.08" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ---------------------------------------------------------------- relative days: "today", "in 12 days", "3 days ago"
export function relDays(iso, today) {
  if (!iso || !today) return "";
  const d = Math.round((Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) - Date.parse(`${today.slice(0, 10)}T00:00:00Z`)) / 86400000);
  if (d === 0) return "today";
  if (d === 1) return "tomorrow";
  if (d === -1) return "yesterday";
  return d > 0 ? `in ${n0.format(d)} days` : `${n0.format(-d)} days ago`;
}
export function shortDate(iso, withYear = false) {
  if (!iso) return "-";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(withYear ? { year: "numeric" } : {}), timeZone: "UTC" });
}

// ================================================================ second pass helpers

// Card tilts slightly toward the pointer with a sheen (CSS vars; off under reduced motion).
export function useTilt(max = 5) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return undefined;
    const move = (e) => {
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      el.style.setProperty("--ry", `${(x - 0.5) * max}deg`); el.style.setProperty("--rx", `${(0.5 - y) * max}deg`);
      el.style.setProperty("--mx", `${x * 100}%`); el.style.setProperty("--my", `${y * 100}%`);
    };
    const leave = () => { el.style.setProperty("--rx", "0deg"); el.style.setProperty("--ry", "0deg"); };
    el.addEventListener("pointermove", move); el.addEventListener("pointerleave", leave);
    return () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerleave", leave); };
  }, [max]);
  return ref;
}

// State kept in the URL hash query (#/orders?f=now&q=sun), so filters, sort, tab, search and open drawer survive reloads
// and can be shared. Values are strings; defaults are omitted from the URL.
export function useQueryState(key, dflt = "") {
  const read = () => new URLSearchParams((window.location.hash.split("?")[1] || "")).get(key) ?? dflt;
  const [v, setV] = useState(read);
  useEffect(() => {
    const on = () => setV(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = useCallback((nv) => {
    const [path, qs] = window.location.hash.split("?");
    const p = new URLSearchParams(qs || "");
    const val = typeof nv === "function" ? nv(read()) : nv;
    if (val == null || val === "" || val === dflt) p.delete(key); else p.set(key, val);
    const next = `${path}${p.toString() ? `?${p}` : ""}`;
    history.replaceState(null, "", next || "#/");
    setV(val ?? dflt);
  }, [key, dflt]); // eslint-disable-line react-hooks/exhaustive-deps
  return [v, set];
}

// Radial gauge: months of stock against the 3-month floor (tick), coloured like the status pills.
export function Gauge({ months, floor = 3, max = 12, size = 64 }) {
  if (months == null || Number.isNaN(months)) return <span className="muted small">no stock data</span>;
  const r = size / 2 - 6, c = 2 * Math.PI * r, f = Math.min(1, Math.max(0, months) / max);
  const col = months < floor ? "var(--crit-fill)" : months < floor + 1.5 ? "var(--warn-fill)" : "var(--ok-fill)";
  const fa = (floor / max) * 2 * Math.PI;
  return (
    <span className="gauge" style={{ width: size, height: size }} title={`${fmt1(months)} months of stock; floor ${floor} months`}>
      <svg width={size} height={size} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth="5" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={col} strokeWidth="5" strokeLinecap="round"
                strokeDasharray={`${c * f} ${c}`} style={{ transition: "stroke-dasharray 600ms var(--ease)", filter: `drop-shadow(0 0 4px ${col})` }} />
        <line x1={size / 2 + (r - 7) * Math.cos(fa)} y1={size / 2 + (r - 7) * Math.sin(fa)} x2={size / 2 + (r + 7) * Math.cos(fa)} y2={size / 2 + (r + 7) * Math.sin(fa)}
              stroke="var(--ink)" strokeWidth="2" />
      </svg>
      <b>{fmt1(months)}</b><small>months</small>
    </span>
  );
}

// "Why?" popover: the inputs the engine used for a recommendation, presented by the agent.
export function Why({ items, title = "Why this recommendation" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const on = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, [open]);
  return (
    <span className="why" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button type="button" className="btn ghost" style={{ height: 24, padding: "0 8px", fontSize: 12 }} onClick={() => setOpen(!open)} aria-expanded={open}>Why?</button>
      {open && (
        <span className="why-pop" role="dialog" aria-label={title}>
          <b style={{ color: "var(--agent)" }}>Agent:</b> {title}, from the engine's inputs.
          <dl>{items.filter(([, v]) => v != null && v !== "").flatMap(([k, v]) => [<dt key={`k${k}`}>{k}</dt>, <dd key={`v${k}`}>{v}</dd>])}</dl>
        </span>
      )}
    </span>
  );
}

// Column visibility menu + density toggle for tables; remembered per table in the browser.
export function useTablePrefs(id, columns) {
  const key = `tbl-${id}`;
  const [p, setP] = useState(() => { try { return JSON.parse(localStorage.getItem(key)) || {}; } catch { return {}; } });
  const save = (n) => { setP(n); try { localStorage.setItem(key, JSON.stringify(n)); } catch { /* blocked */ } };
  const hidden = new Set(p.hidden || []);
  return {
    show: (c) => !hidden.has(c), compact: !!p.compact,
    toggle: (c) => save({ ...p, hidden: hidden.has(c) ? [...hidden].filter((x) => x !== c) : [...hidden, c] }),
    setCompact: (v) => save({ ...p, compact: v }), columns,
  };
}
export function TableControls({ prefs }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const on = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, [open]);
  return (
    <span className="rel" ref={ref} style={{ display: "inline-flex", gap: 8 }}>
      <div className="seg" role="group" aria-label="Row density">
        <button type="button" aria-pressed={!prefs.compact} onClick={() => prefs.setCompact(false)}>Comfortable</button>
        <button type="button" aria-pressed={prefs.compact} onClick={() => prefs.setCompact(true)}>Compact</button>
      </div>
      <button type="button" className="btn" onClick={() => setOpen(!open)} aria-expanded={open}>Columns</button>
      {open && (
        <span className="colmenu" role="menu">
          {prefs.columns.map(([k, label]) => (
            <label key={k}><input type="checkbox" checked={prefs.show(k)} onChange={() => prefs.toggle(k)} />{label}</label>
          ))}
        </span>
      )}
    </span>
  );
}

// Saved views (browser only): name -> current query string of the page.
export function SavedViews({ id }) {
  const key = `views-${id}`;
  const [views, setViews] = useState(() => { try { return JSON.parse(localStorage.getItem(key)) || []; } catch { return []; } });
  const persist = (v) => { setViews(v); try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* blocked */ } };
  const current = window.location.hash.split("?")[1] || "";
  const apply = (q) => { const path = window.location.hash.split("?")[0]; window.location.hash = `${path.slice(1)}${q ? `?${q}` : ""}`; };
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
      <select aria-label="Saved views" value="" onChange={(e) => { if (e.target.value === "__del") persist([]); else if (e.target.value !== "") apply(e.target.value); }}>
        <option value="">Saved views{views.length ? ` (${views.length})` : ""}</option>
        {views.map((v) => <option key={v.name} value={v.q}>{v.name}</option>)}
        {views.length > 0 && <option value="__del">Clear saved views</option>}
      </select>
      <button type="button" className="btn ghost" disabled={!current} title={current ? "Save the current filters as a view" : "Change a filter first"}
              onClick={() => { const name = window.prompt("Name this view"); if (name) persist([...views.filter((v) => v.name !== name), { name, q: current }]); }}>Save view</button>
    </span>
  );
}

// CSV of exactly what the table shows (current filters and sort).
export function toCSV(filename, rows, columns) {
  const esc = (v) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const text = [columns.map((c) => esc(c[1])).join(","), ...rows.map((r) => columns.map((c) => esc(typeof c[0] === "function" ? c[0](r) : r[c[0]])).join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}
