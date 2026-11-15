import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { AlertTriangle, CheckCircle2, CircleSlash, Clock, Package, PauseCircle, Search } from "lucide-react";
import { useData } from "../useData.js";
import { useSettings } from "../settings.jsx";
import { getJSON } from "../api.js";
import { cases, niceDate, sentence } from "../format.js";
import { ErrorBox } from "../components.jsx";
import { EmptyState, Pill, SavedViews, Sparkline, Term, brandParts, fmt0, relDays, shortDate, toCSV, useQueryState } from "../ui.jsx";
import { Download } from "lucide-react";

// Urgency groups, most urgent first. Classification uses the same fields and wording as before; only the order changed.
const GROUPS = [
  ["expedite", "Expedite", "crit", AlertTriangle],
  ["order", "Order now / this month", "warn", Clock],
  ["healthy", "Healthy", "ok", CheckCircle2],
  ["hold", "On hold", "neutral", PauseCircle],
  ["dead", "Not in plan", "neutral", CircleSlash],
];
const RANK = Object.fromEntries(GROUPS.map(([k], i) => [k, i]));

// Same rules as the status text below. With stock data the engine's product status decides; without it (gross need),
// a product whose planned order date is this month or earlier counts as "order".
function classify(p, today, stockUsed) {
  if (p.on_hold) return "hold";
  if (!p.in_plan) return "dead";
  const s = String(p.status || "").toLowerCase();
  if (p.below_floor_now || s.includes("below 3-month floor") || p.floor_dip_date) return "expedite";
  if (p.order_now_cases > 0) return "order";
  if (!stockUsed && p.order_by_safe && p.order_by_safe.slice(0, 7) <= today.slice(0, 7)) return "order";
  return "healthy";
}

function statusOf(p) {
  if (p.on_hold) return "On hold";
  if (!p.in_plan) return "Not in the plan (no recent sales)";
  const s = String(p.status || "").toLowerCase();
  if (p.below_floor_now || s.includes("below 3-month floor")) return "Below 3 months of stock: expedite";
  if (p.floor_dip_date) return `Below 3 months of stock from ${niceDate(p.floor_dip_date, null, { relative: false })}`;
  if (p.order_now_cases > 0) return `Order now: ${cases(p.order_now_cases)}`;
  if (s.includes("annual harvest")) return "Annual harvest buy";
  if (p.ok_until) return `OK until ${niceDate(p.ok_until, null, { relative: false })}`;
  return p.status ? sentence(p.status) : "In the plan";
}

// Within a group: the soonest date first.
const when = (p, g) => (g === "expedite" ? (p.below_floor_now ? "0000" : p.floor_dip_date) : g === "order" ? p.order_by_safe
  : g === "healthy" ? p.ok_until || p.next_month : null) || "9999";

// Hover card: monthly sales history from the product endpoint, fetched once per product.
const cache = new Map();
function HoverCard({ p, top }) {
  const [d, setD] = useState(cache.get(p.product_id));
  useEffect(() => {
    if (cache.has(p.product_id)) { setD(cache.get(p.product_id)); return undefined; }
    let live = true;
    const t = setTimeout(() => getJSON(`/products/${encodeURIComponent(p.product_id)}`).then((x) => {
      const v = { hist: x.history.map((h) => h.cases), typical: x.profile?.typical_monthly_cases, months: x.history.length };
      cache.set(p.product_id, v);
      if (live) setD(v);
    }).catch(() => {}), 150);
    return () => { live = false; clearTimeout(t); };
  }, [p.product_id]);
  return (
    <div className="card" style={{ position: "absolute", right: 16, top: Math.max(4, top - 8), width: 260, padding: 12, zIndex: 5, pointerEvents: "none",
      boxShadow: "var(--shadow-lg)", animation: "pop-in 140ms var(--ease) both" }}>
      <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>Monthly sales{d ? `, last ${d.months} months` : ""}</div>
      {d ? <Sparkline values={d.hist} width={236} height={44} color="var(--accent)" /> : <span className="sk" style={{ height: 44 }} />}
      {d?.typical != null && <div className="small muted" style={{ marginTop: 6 }}>Typical month: {cases(d.typical)}</div>}
    </div>
  );
}

export default function Products() {
  const [qUrl, setQUrl] = useQueryState("q", "");
  const [q, setQ] = useState(qUrl);
  const [term, setTerm] = useState(qUrl);
  const [tab, setTab] = useQueryState("tab", "all");
  const [showDead, setShowDead] = useState(false);
  const [hover, setHover] = useState(null);
  useEffect(() => { const t = setTimeout(() => { setTerm(q.trim()); setQUrl(q.trim()); }, 200); return () => clearTimeout(t); }, [q]); // eslint-disable-line react-hooks/exhaustive-deps
  const { data, error, loading } = useData(`/products?show=all&limit=5000&q=${encodeURIComponent(term)}`);
  const { settings } = useSettings();
  const today = settings?.as_of || new Date().toISOString().slice(0, 10);   // the plan date (time travel aware)

  const { byGroup, counts } = useMemo(() => {
    const out = Object.fromEntries(GROUPS.map(([k]) => [k, []]));
    for (const p of data?.products || []) out[classify(p, today, data.stock_used)].push(p);
    for (const [k] of GROUPS) out[k].sort((a, b) => when(a, k).localeCompare(when(b, k)) || String(a.name).localeCompare(String(b.name)));
    return { byGroup: out, counts: Object.fromEntries(GROUPS.map(([k]) => [k, out[k].length])) };
  }, [data, today]);

  const rows = useMemo(() => {
    if (tab !== "all") return byGroup[tab] || [];
    return GROUPS.filter(([k]) => k !== "dead" || showDead).flatMap(([k]) => byGroup[k]);
  }, [byGroup, tab, showDead]);

  const parent = useRef(null);
  const v = useVirtualizer({ count: rows.length, getScrollElement: () => parent.current, estimateSize: () => 56, overscan: 12 });
  useEffect(() => { parent.current?.scrollTo({ top: 0 }); setHover(null); }, [tab, term, showDead]);

  if (error) return <ErrorBox error={error} />;
  const go = (p) => { window.location.hash = `/product/${encodeURIComponent(p.product_id)}`; };
  const attention = (counts.expedite || 0) + (counts.order || 0);

  return (
    <div data-ready={data ? "products" : undefined}>
      <div className="page-head">
        <div>
          <h2>Products</h2>
          <p>{data ? `${fmt0(data.total)} products in the catalogue; ${fmt0(attention)} need attention.` : "Loading the catalogue"} Sorted by urgency:
            below the <Term k="floor">3-month floor</Term> first, then orders due, then healthy items. Items not in the plan are hidden until you ask.</p>
        </div>
        <div className="panel-side">
          <label className="search"><Search className="ico" aria-hidden="true" />
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, item code or brand" aria-label="Search products" /></label>
          <SavedViews id="products" />
          <button type="button" className="btn" disabled={!data} onClick={() => toCSV(`products-${today}.csv`, rows, [["product_id", "Item code"], ["name", "Product"],
            [(p) => brandParts(p.brand).label, "Brand"], ["category", "Category"], [(p) => statusOf(p), "Status"], ["on_hand_cases", "On hand (cases)"],
            [(p) => (data.stock_used ? (p.below_floor_now ? "now" : p.floor_dip_date || p.ok_until || "") : p.order_by_safe || ""), data?.stock_used ? "Stock OK until" : "Order by"],
            ["next_month", "Next month"], ["next_cases", "Next month (cases)"]])}><Download className="ico-sm" />CSV</button>
          <span className="small muted" role="status" aria-live="polite">
            {!data ? "Loading" : `${fmt0(data.matched)} match${data.matched === 1 ? "" : "es"}${loading ? "…" : ""}${data.matched > data.products.length ? `, first ${fmt0(data.products.length)} shown` : ""}`}
          </span>
        </div>
      </div>

      <div className="tabs" role="tablist" aria-label="Status">
        <button type="button" role="tab" aria-selected={tab === "all"} onClick={() => setTab("all")}>All by urgency<span className="badge">{fmt0(data ? data.products.length - (showDead ? 0 : counts.dead || 0) : 0)}</span></button>
        {GROUPS.map(([k, label, tone, Icon]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
            <Icon className="ico-sm" aria-hidden="true" />{label}<span className={`badge ${k === "expedite" && counts[k] ? "crit" : k === "order" && counts[k] ? "warn" : ""}`}>{fmt0(counts[k] || 0)}</span>
          </button>
        ))}
      </div>

      {!data ? <div className="skeleton"><span /><span /><span /></div> : rows.length === 0 ? (
        <div className="panel"><EmptyState icon={Package} title="Nothing here">{term ? "No product matches this search in this group." : "No products in this group."}</EmptyState></div>
      ) : (
        <div className="vlist" role="table" aria-label="Products" aria-rowcount={rows.length}>
          <div className="vlist-head" role="row">
            <span role="columnheader">Product</span>
            <span role="columnheader" className="opt">Brand</span>
            <span role="columnheader">Status</span>
            <span role="columnheader" className="r opt">On hand</span>
            <span role="columnheader" className="opt">{data?.stock_used ? "Stock OK until" : "Order by"}</span>
            <span role="columnheader" className="r">Next month</span>
          </div>
          <div className="vlist-body" ref={parent} onMouseLeave={() => setHover(null)}>
            <div style={{ height: v.getTotalSize(), position: "relative" }}>
              {v.getVirtualItems().map((it) => {
                const p = rows[it.index];
                const g = classify(p, today, data.stock_used);
                const [, , tone, Icon] = GROUPS[RANK[g]];
                const b = brandParts(p.brand);
                return (
                  <div key={p.product_id} role="row" tabIndex={0} className={`vrow${g === "dead" ? " dead" : ""}`} style={{ transform: `translateY(${it.start}px)` }}
                       onClick={() => go(p)} onKeyDown={(e) => { if (e.key === "Enter") go(p); }}
                       onMouseEnter={() => setHover({ p, top: it.start })}>
                    <span className="pn" role="cell"><b>{p.name}</b><span>{p.product_id}{p.category ? ` · ${p.category}` : ""}</span></span>
                    <span role="cell" className="opt">{p.brand ? (b.code ? <span className="chip">{b.label}</span> : <span className="tag">{b.label}</span>) : <span className="muted">-</span>}</span>
                    <span role="cell"><Pill tone={tone} icon={Icon}>{statusOf(p)}</Pill></span>
                    <span role="cell" className="r opt">{p.on_hand_cases != null ? fmt0(p.on_hand_cases) : <span className="muted">-</span>}</span>
                    <span role="cell" className="opt small">{(() => {
                      if (data.stock_used && p.below_floor_now) return <span className="st-red" style={{ fontWeight: 600 }}>Now</span>;
                      const d = data.stock_used ? (p.floor_dip_date || p.ok_until) : p.order_by_safe;   // with stock: when it stops being OK
                      return d ? <>{shortDate(d, true)}<div className="muted">{relDays(d, today)}</div></> : <span className="muted">-</span>;
                    })()}</span>
                    <span role="cell" className="r small">{p.next_month ? <>{fmt0(p.next_cases)}<div className="muted">{shortDate(`${p.next_month}-01`)}</div></> : <span className="muted">-</span>}</span>
                  </div>
                );
              })}
            </div>
            {hover && <HoverCard p={hover.p} top={hover.top} />}
          </div>
        </div>
      )}
      {tab === "all" && data && (counts.dead || 0) > 0 && (
        <div style={{ marginTop: 12 }}>
          <button type="button" className="btn ghost" onClick={() => setShowDead(!showDead)}>
            <CircleSlash className="ico-sm" />{showDead ? "Hide" : "Show"} {fmt0(counts.dead)} products not in the plan (no recent sales)
          </button>
        </div>
      )}
    </div>
  );
}
