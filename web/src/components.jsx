import { AlertTriangle, Info } from "lucide-react";
import { host } from "./format.js";
import { StatusPill } from "./ui.jsx";
import { RobotStill } from "./robot/Robot.jsx";

// Status as a pill: icon + words, so meaning never depends on color alone.
export function StatusText({ status }) {
  return <StatusPill status={status} />;
}

// Quiet loading placeholder: grey bars, no spinner.
export function Loading({ what = "Loading" }) {
  return (
    <div role="status" aria-label={what} style={{ display: "grid", gridTemplateColumns: "44px minmax(0,1fr)", gap: 14, alignItems: "center" }}>
      <span style={{ width: 44, height: 44 }} aria-hidden="true"><RobotStill mood="thinking" /></span>
      <div className="skeleton"><span /><span /><span /></div>
    </div>
  );
}

// Errors are delivered by the agent, with a retry.
export function ErrorBox({ error }) {
  return (
    <div className="panel hud" role="alert" style={{ display: "grid", gridTemplateColumns: "72px minmax(0,1fr)", gap: 16, alignItems: "center" }}>
      <span style={{ width: 72, height: 72 }} aria-hidden="true"><RobotStill mood="alert" /></span>
      <div>
        <b>I couldn't load this page.</b>
        <p className="note" style={{ marginTop: 4 }}>{String(error?.message || error)}. Is the dashboard server running? Your data is unchanged.</p>
        <button type="button" className="btn" style={{ marginTop: 10 }} onClick={() => window.location.reload()}>Retry</button>
      </div>
    </div>
  );
}

// A callout; tone "warn" for things the buyer must not miss.
export function Banner({ children, tone = "" }) {
  const Icon = tone === "warn" ? AlertTriangle : Info;
  return <div className={`callout ${tone || "info"}`} role="note"><Icon className="ico" aria-hidden="true" /><div>{children}</div></div>;
}

export function GrossBanner({ text }) {
  return <Banner>{text || "Showing how much you need. Your current stock isn't included yet."}</Banner>;
}

export function Panel({ id, title, sub, actions, children }) {
  return (
    <section className="panel" aria-labelledby={id}>
      <header>
        <h2 id={id}>{title}</h2>
        {(sub || actions) && (
          <div className="panel-side">
            {sub && <p className="sub">{sub}</p>}
            {actions}
          </div>
        )}
      </header>
      {children}
    </section>
  );
}

export function Figure({ k, v, unit, d, status }) {
  return (
    <div className="figure">
      <div className="k">{k}</div>
      <div className="v">{v}{unit && <small>{unit}</small>}</div>
      {status && <div className={`status ${status[1]}`}>{status[0]}</div>}
      {d && <div className="d">{d}</div>}
    </div>
  );
}

export function Source({ url, label }) {
  if (!url) return <span className="muted">no source</span>;
  return <a href={url} target="_blank" rel="noreferrer noopener">{label || host(url)}</a>;
}

export function Tag({ children, tone = "" }) {
  return <span className={`tag ${tone}`}>{children}</span>;
}

// Scrollable, focusable table region with a sticky header; stacks into labeled rows when narrow (class "stack").
export function Table({ id, caption, children, stack = true, className = "" }) {
  return (
    <div className="tbl-wrap" role="region" aria-labelledby={`${id}-cap`} tabIndex={0}>
      <table className={`tbl ${stack ? "stack" : ""} ${className}`}>
        <caption id={`${id}-cap`}>{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function downloadCSV(filename, rows, columns) {
  const esc = (v) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [columns.map((c) => esc(c[1])).join(","), ...rows.map((r) => columns.map((c) => esc(r[c[0]])).join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// "12.3 CBM, 1 container (last one 81% full)"; tagged "estimated" when the case sizes are estimates.
export function ContainerLine({ o }) {
  if (o.cbm == null) return null;
  const est = o.cbm_estimated_pct != null && o.cbm_estimated_pct > 0;
  return (
    <span>
      {o.cbm.toLocaleString("en-US", { maximumFractionDigits: 1 })} CBM, {o.containers_needed} container{o.containers_needed === 1 ? "" : "s"}
      {o.last_container_fill_pct != null ? ` (last one ${Math.round(o.last_container_fill_pct)}% full)` : ""}
      {est && <> <Tag>estimated</Tag></>}
    </span>
  );
}

// Text filter + select row above a table.
export function Toolbar({ children }) {
  return <div className="toolbar">{children}</div>;
}
