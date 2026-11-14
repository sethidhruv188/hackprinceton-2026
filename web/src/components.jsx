import { STATUS } from "./format.js";

export function StatusBadge({ status }) {
  const s = STATUS[status] || { cls: "coming", icon: "" };
  // Color is never the only signal: the status text is always shown.
  return <span className={`badge ${s.cls}`}><span aria-hidden="true">{s.icon}</span> {status}</span>;
}

export function Loading({ what = "Loading" }) {
  return <p className="muted" role="status">{what}…</p>;
}

export function ErrorBox({ error }) {
  return <p className="error" role="alert">Could not load this page: {String(error.message || error)}. Is the dashboard server running?</p>;
}

export function GrossBanner({ text }) {
  return <p className="banner" role="note">{text || "Showing how much you need. Your current stock isn't included yet."}</p>;
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

// "12.3 CBM, 1 container (81% full)"; tagged "estimated" when the case sizes are estimates.
export function ContainerLine({ o }) {
  if (o.cbm == null) return null;
  const est = o.cbm_estimated_pct != null && o.cbm_estimated_pct > 0;
  return (
    <span>
      {o.cbm.toLocaleString("en-US", { maximumFractionDigits: 1 })} CBM · {o.containers_needed} container{o.containers_needed === 1 ? "" : "s"}
      {o.last_container_fill_pct != null ? ` (last one ${Math.round(o.last_container_fill_pct)}% full)` : ""}
      {est && <span className="tag" title="Case sizes estimated from weight; add real sizes in data/raw/case_cbm.csv"> estimated</span>}
    </span>
  );
}
