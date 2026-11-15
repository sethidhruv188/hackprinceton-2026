import { STATUS, host } from "./format.js";

// Status as text with a restrained color; the words always carry the meaning.
export function StatusText({ status, className = "" }) {
  const s = STATUS[status] || { cls: "st-grey", short: status };
  return <span className={`status-text ${s.cls} ${className}`}>{s.short}</span>;
}

// Quiet loading placeholder: grey bars, no spinner.
export function Loading({ what = "Loading" }) {
  return (
    <div className="skeleton" role="status" aria-label={what}>
      <span /><span /><span />
    </div>
  );
}

export function ErrorBox({ error }) {
  return (
    <p className="error" role="alert">
      Could not load this page: {String(error?.message || error)}. Is the dashboard server running?
    </p>
  );
}

// A one-line note under a 3px rule on the left; tone: "" | "warn".
export function Banner({ children, tone = "" }) {
  return <p className={`banner ${tone}`} role="note">{children}</p>;
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
