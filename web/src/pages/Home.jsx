import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertOctagon, AlertTriangle, CheckCircle2, Download, PackageCheck, PauseCircle, Search, Ship, ShoppingCart, SlidersHorizontal } from "lucide-react";
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useData } from "../useData.js";
import { getJSON } from "../api.js";
import { cases, monthLabel, shortMonth, weight } from "../format.js";
import { downloadCSV, ErrorBox } from "../components.jsx";
import {
  BrandName, ContainerFill, Drawer, EmptyState, KPI, Pill, RiskBar, RunwayBar, SkeletonCards, StatusPill, TableControls, Term, Tip, Why,
  brandParts, fmt0, fmt1, relDays, shortDate, toCSV, useQueryState, useTablePrefs, useToast,
} from "../ui.jsx";
import { useAgent, usePointed } from "../agent.jsx";
import { FileText, Undo2 } from "lucide-react";

const ORDER = ["Order now", "Order this month", "Coming up"];
const LATE = "Too late for full lead time: expedite";
const CSV = [["brand", "Brand"], ["status", "Status"], ["order_by_safe", "Order by (safe)"], ["order_by_latest", "Order by (latest)"],
  ["covers", "Covers"], ["cases", "Cases"], ["kg", "kg"], ["cbm", "CBM"], ["containers_needed", "Containers"], ["products", "Products"],
  ["reason", "Why"]];
const DAY = 86400000;
const t = (iso) => Date.parse(`${String(iso).slice(0, 10)}T00:00:00Z`);

// Filters behind the KPI cards (presentation only: the same brands the API returns, narrowed).
const FILTERS = {
  all: () => true,
  now: (b) => b.next_order.status === "Order now",
  decision: (b) => b.next_order.status === "Order now" || b.next_order.status === "Order this month",
  hold: (b) => (b.next_order.products_on_hold || 0) > 0,
  late: (b) => (b.late_orders || 0) > 0 || b.next_order.status === LATE,
};

// "Approve" only records, in this browser, that the buyer reviewed the order. Nothing is sent or ordered.
function useApprovals() {
  const [a, setA] = useState(() => { try { return JSON.parse(localStorage.getItem("approved-orders") || "{}"); } catch { return {}; } });
  const [last, setLast] = useState([]);              // undo stack (this session)
  const write = (n) => { try { localStorage.setItem("approved-orders", JSON.stringify(n)); } catch { /* storage blocked */ } return n; };
  const approve = useCallback((id) => { setA((x) => write({ ...x, [id]: new Date().toISOString() })); setLast((l) => [...l, id]); }, []);
  const undo = useCallback(() => {
    setLast((l) => {
      const id = l[l.length - 1];
      if (id) setA((x) => { const n = { ...x }; delete n[id]; return write(n); });
      return l.slice(0, -1);
    });
  }, []);
  return [a, approve, undo, last.length > 0];
}

// ---------------------------------------------------------------- shipping timeline (Gantt)
function ShippingTimeline({ rows, today, onOpen }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, 10);
  if (!shown.length) return null;
  const t0 = Math.min(t(today), ...shown.map((b) => t(b.next_order.order_by_safe))) - 10 * DAY;
  // Coverage: with stock data, an order "arrives by" safe date + 130 days and lasts about 3 months after arrival (the
  // engine's ordering rule); without stock data, first_month..last_month are the months it covers.
  const cover = (o) => (String(o.covers || "").startsWith("arrives")
    ? [t(o.order_by_safe) + 130 * DAY, t(o.order_by_safe) + 130 * DAY + 91 * DAY]
    : [t(o.first_month), t(o.last_month || o.first_month) + 31 * DAY]);
  const ends = shown.map((b) => cover(b.next_order)[1]);
  const t1 = Math.min(Math.max(...ends), t0 + 520 * DAY);
  const pct = (x) => `${Math.max(0, Math.min(100, ((x - t0) / (t1 - t0)) * 100))}%`;
  const width = (a, b) => `${Math.max(0.6, ((Math.min(b, t1) - Math.max(a, t0)) / (t1 - t0)) * 100)}%`;
  const ticks = [];
  for (let d = new Date(t0); d.getTime() <= t1; d.setUTCMonth(d.getUTCMonth() + 1, 1)) {
    const x = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
    if (x >= t0) ticks.push(x);
  }
  const tone = (s) => (s === "Order now" || s === LATE ? "var(--crit-fill)" : s === "Order this month" ? "var(--warn-fill)" : "var(--info-fill)");
  return (
    <section className="panel" aria-labelledby="tl-h">
      <header>
        <div>
          <h2 id="tl-h">Shipping timeline</h2>
          <p className="sub">Order by the <Term k="safe">safe date</Term>, ship 105 to 130 days, then about 3 months of stock</p>
        </div>
        <div className="legend-dots">
          <span><i style={{ background: "var(--crit-fill)", borderRadius: "50%" }} />Order-by date</span>
          <span><i style={{ background: "var(--info-bg)", border: "1px dashed var(--info-fill)" }} />At sea (105 to 130 days)</span>
          <span><i style={{ background: "var(--ok-bg)", border: "1px solid var(--ok-line)" }} />Months the order covers</span>
        </div>
      </header>
      <div className="gantt" role="img" aria-label="Order, shipping and coverage window per brand">
        <div className="gantt-scale" aria-hidden="true">
          {ticks.map((x, i) => <span key={x} className={i % 3 ? "minor" : ""} style={{ left: pct(x) }}>{shortMonth(new Date(x).toISOString().slice(0, 7))}</span>)}
        </div>
        <div className="gantt-body" style={{ paddingTop: 22 }}>
          <div className="gantt-grid" aria-hidden="true">
            {ticks.map((x, i) => <i key={x} className={i % 3 ? "minor" : ""} style={{ left: pct(x) }} />)}
            <span className="g-today" style={{ left: pct(t(today)) }}><span className="g-today-label">Today</span></span>
          </div>
          {shown.map((b) => {
            const o = b.next_order;
            const ob = t(o.order_by_safe);
            const [coverA, coverB] = cover(o);
            return (
              <div className="gantt-row" key={b.brand}>
                <button type="button" className="nm" onClick={() => onOpen(b.brand)} title={`Open ${b.brand}`}>{b.brand.replace(/\s*\(item code\)$/i, "")}</button>
                <div className="gantt-lane">
                  {ob > t(today) && <span className="g-seg g-wait" style={{ left: pct(t(today)), width: width(t(today), ob) }} />}
                  <span className="g-seg g-ship" style={{ left: pct(ob + 105 * DAY), width: width(ob + 105 * DAY, ob + 130 * DAY) }}
                        title={`At sea: arrives between ${shortDate(new Date(ob + 105 * DAY).toISOString())} and ${shortDate(new Date(ob + 130 * DAY).toISOString())}`} />
                  <span className="g-seg g-cover" style={{ left: pct(coverA), width: width(coverA, coverB) }}
                        title={String(o.covers || "").startsWith("arrives") ? `${o.covers}; lasts about 3 months after arrival` : `Covers ${o.covers}`} />
                  <span className="g-seg g-order" style={{ left: pct(ob), background: tone(o.status) }}
                        title={`Order by ${shortDate(o.order_by_safe, true)} (${o.status})`} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {rows.length > 10 && (
        <div style={{ marginTop: 12 }}><button type="button" className="btn ghost" onClick={() => setAll(!all)}>{all ? "Show the 10 most urgent" : `Show all ${rows.length} brands`}</button></div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- brand drawer
function BrandDrawer({ brand, today, onClose, approved, onApprove }) {
  const [d, setD] = useState(null);
  const [proj, setProj] = useState(null);
  const [err, setErr] = useState(null);
  const toast = useToast();
  useEffect(() => {
    if (!brand) return;
    setD(null); setProj(null); setErr(null);
    getJSON(`/brands/${encodeURIComponent(brand)}`).then(setD).catch(setErr);
    getJSON(`/projection?brand=${encodeURIComponent(brand)}&months=6`).then(setProj).catch(() => setProj({ available: false }));
  }, [brand]);
  const o = d?.orders?.[0];
  const lines = (o?.lines || []).filter((l) => !l.on_hold);
  // One entry per product (a product can also have a "pulled forward" line).
  const atRisk = [...new Map(lines.filter((l) => l.runs_out_before_arrival || l.floor_dip_date).map((l) => [l.product_id, l])).values()];
  const top = [...lines].sort((a, b) => b.cases - a.cases);
  const maxCases = Math.max(1, ...top.map((l) => l.cases));
  const adjust = () => {
    try { sessionStorage.setItem("ask-prefill", `For ${brand}: `); } catch { /* blocked */ }
    window.location.hash = "/ask";
  };
  return (
    <Drawer open={!!brand} onClose={onClose} title={brand ? <BrandName name={brand} /> : ""}
      footer={o && (
        <>
          <a className="btn ghost" href={`#/brand/${encodeURIComponent(brand)}`}>Open brand page</a>
          <button type="button" className="btn" onClick={() => printDraftPO(brand, o, lines, today)}><FileText className="ico-sm" />Draft PO</button>
          <button type="button" className="btn" onClick={adjust}><SlidersHorizontal className="ico-sm" />Adjust</button>
          <button type="button" className="btn primary" disabled={!!approved[o.order_id]}
                  onClick={() => { onApprove(o.order_id); toast(`${brand}: marked as approved on this computer. Nothing is sent or ordered.`); }}>
            <CheckCircle2 className="ico-sm" />{approved[o.order_id] ? "Approved" : "Approve"}
          </button>
        </>
      )}>
      {err && <p className="error">Could not load {brand}: {String(err.message || err)}</p>}
      {!d && !err && <div className="skeleton"><span /><span /><span /></div>}
      {d && (
        <>
          {o ? (
            <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <StatusPill status={o.status} />
              <Why items={[["Sold per month", cases(d.info?.monthly_cases)], ["On hand", d.info?.on_hand_cases != null ? cases(d.info.on_hand_cases) : null],
                ["Months of stock", d.info?.months_of_cover != null ? `${fmt1(d.info.months_of_cover)} months` : null], ["Floor", "3 months of sales"],
                ["Shipping", "105 to 130 days (safe date uses 130)"], ["This order", `${cases(o.cases)}, ${o.covers}`]]} />
              <span className="small">Order by <b>{shortDate(o.order_by_safe, true)}</b> ({relDays(o.order_by_safe, today)}), latest {shortDate(o.order_by_latest, true)}</span>
            </div>
          ) : <p className="note">No upcoming orders in the 12-month plan.</p>}
          <div className="mini-stats">
            <div><span>Cases to order</span><b>{o ? fmt0(o.cases) : "-"}</b></div>
            <div><span>Weight</span><b>{o ? weight(o.kg) : "-"}</b></div>
            <div><span>Containers</span><b>{o?.containers_needed ?? (o?.containers != null ? `~${o.containers}` : "-")}</b></div>
          </div>
          {o?.containers_needed > 0 && (
            <div>
              <h3>Containers <Tip text="Each box is one container; the last one is filled to the engine's fill level. Hover to rotate." /></h3>
              <Containers3D n={o.containers_needed} lastPct={o.last_container_fill_pct} />
            </div>
          )}
          <div>
            <h3>Stock runway <Tip text="Months of stock on hand at the last 6 months' sales pace, against the 3-month floor." /></h3>
            <RunwayBar months={d.info?.months_of_cover} />
            <p className="note">{d.info?.on_hand_cases != null ? `${cases(d.info.on_hand_cases)} on hand; ` : ""}sells about {cases(d.info?.monthly_cases)} a month.</p>
          </div>
          {proj?.available && proj.months.length > 1 && (
            <div>
              <h3>Projected stock, next {proj.months.length} months</h3>
              <div className="chart" style={{ height: 180 }}>
                <ResponsiveContainer>
                  <ComposedChart data={proj.months} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid vertical={false} strokeDasharray="3 3" />
                    <XAxis dataKey="month" tickFormatter={shortMonth} tickLine={false} axisLine={false} />
                    <YAxis tickLine={false} axisLine={false} width={56} tickFormatter={(v) => fmt0(v)} label={{ value: "cases", angle: -90, position: "insideLeft", fill: "var(--muted)", fontSize: 11 }} />
                    <Tooltip content={({ active, payload, label }) => active && payload?.length ? (
                      <div className="tip"><strong>{monthLabel(label)}</strong>
                        {payload.map((p) => <div key={p.dataKey}>{p.name}: {cases(p.value)}</div>)}</div>) : null} />
                    <Area dataKey="projected_with_suggested_order" name="With the suggested order" stroke="var(--ok-fill)" fill="var(--ok-bg)" strokeWidth={2} />
                    <Line dataKey="projected_stock" name="With no new order" stroke="var(--chart-1)" strokeDasharray="4 3" dot={false} strokeWidth={1.5} />
                    <Line dataKey="floor_3_months" name="3-month floor" stroke="var(--crit-fill)" dot={false} strokeWidth={1.5} className="floor-line" />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <div className="legend-dots">
                <span><i style={{ background: "var(--ok-fill)" }} />With the suggested order</span>
                <span><i style={{ background: "var(--chart-1)" }} />No new order</span>
                <span><i style={{ background: "var(--crit-fill)" }} />3-month floor</span>
              </div>
            </div>
          )}
          {atRisk.length > 0 && (
            <div>
              <h3>SKUs at risk ({atRisk.length})</h3>
              <div className="sku-list">
                {atRisk.slice(0, 8).map((l) => (
                  <div key={l.product_id}>
                    <span><a href={`#/product/${encodeURIComponent(l.product_id)}`} style={{ fontWeight: 600 }}>{l.name}</a>
                      <span className="muted small"> {l.product_id}</span></span>
                    <Pill tone="crit" icon={AlertTriangle}>{l.runs_out_before_arrival ? "Runs out before arrival" : `Below floor ${l.floor_dip_date <= today ? "now" : `from ${shortDate(l.floor_dip_date)}`}`}</Pill>
                  </div>
                ))}
              </div>
              {atRisk.length > 8 && <p className="note">+{atRisk.length - 8} more on the brand page.</p>}
            </div>
          )}
          {top.length > 0 && (
            <div>
              <h3>Suggested order, by product</h3>
              <div style={{ display: "grid", gap: 8 }}>
                {top.slice(0, 8).map((l) => (
                  <div key={`${l.product_id}-${l.pulled_forward}`} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 140px 64px", gap: 10, alignItems: "center", fontSize: 13 }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={l.reason}>{l.name}{l.pulled_forward ? <span className="muted"> (pulled forward)</span> : null}</span>
                    <span className="bar-track"><span className="bar-fill fill-info" style={{ width: `${(l.cases / maxCases) * 100}%` }} /></span>
                    <span style={{ textAlign: "right", fontWeight: 600 }}>{fmt0(l.cases)}</span>
                  </div>
                ))}
              </div>
              {top.length > 8 && <p className="note">+{top.length - 8} more products; {cases(o.cases)} in total.</p>}
            </div>
          )}
          {o?.reason && <p className="note">{o.reason}</p>}
        </>
      )}
    </Drawer>
  );
}

// ---------------------------------------------------------------- page
// ---------------------------------------------------------------- scrub through the engine's stock projection
function Scrubber({ brands, onOpen }) {
  const [data, setData] = useState(null);
  const [i, setI] = useState(0);
  useEffect(() => { getJSON("/projection/brands?months=12").then(setData).catch(() => setData({ available: false })); }, []);
  if (!data) return null;
  if (!data.available || !data.months.length) return null;           // no stock projection: feature hidden
  const month = data.months[Math.min(i, data.months.length - 1)];
  const keep = new Set(brands);
  const rows = data.brands.filter((b) => keep.has(b.brand)).map((b) => ({ brand: b.brand, m: b.months.find((x) => x.month === month) }))
    .filter((r) => r.m).sort((a, b) => b.m.below_floor - a.m.below_floor || a.brand.localeCompare(b.brand));
  const totalBelow = rows.reduce((n, r) => n + r.m.below_floor, 0);
  const label = (m) => new Date(`${m}-01T00:00:00Z`).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
  return (
    <section className="panel hud" aria-labelledby="scrub-h">
      <header>
        <div><h2 id="scrub-h">Look ahead: stock if nothing new is ordered</h2>
          <p className="sub">Drag through the engine's 12-month projection. Products below the <Term k="floor">3-month floor</Term> per brand, without the suggested orders.</p></div>
        <span className="pill crit" aria-live="polite">{fmt0(totalBelow)} products below the floor in {label(month)}</span>
      </header>
      <div className="scrub">
        <span className="when">{label(month)}</span>
        <div>
          <input type="range" min={0} max={data.months.length - 1} value={i} onChange={(e) => setI(Number(e.target.value))} aria-label="Month" />
          <div className="scrub-ticks">{data.months.map((m) => <span key={m}>{label(m).split(" ")[0]}</span>)}</div>
        </div>
        <button type="button" className="btn ghost" onClick={() => setI(0)} disabled={i === 0}>Reset</button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10, marginTop: 14 }}>
        {rows.slice(0, 12).map((r) => {
          const pct = r.m.floor > 0 ? Math.min(100, (r.m.projected_stock / (r.m.floor * 2)) * 100) : 0;
          return (
            <button key={r.brand} type="button" className="action" style={{ gridTemplateColumns: "minmax(0,1fr) auto", textAlign: "left", font: "inherit", color: "inherit", cursor: "pointer" }}
                    onClick={() => onOpen(r.brand)}>
              <span style={{ display: "grid", gap: 6, minWidth: 0 }}>
                <BrandName name={r.brand} avatar={false} />
                <span className="runway"><span className="track"><span className={`fill ${r.m.projected_stock < r.m.floor ? "fill-crit" : "fill-ok"}`} style={{ width: `${pct}%` }} />
                  <span className="floor" style={{ left: "50%" }} /></span></span>
                <span className="small muted">{fmt0(r.m.projected_stock)} cases projected · floor {fmt0(r.m.floor)}</span>
              </span>
              <span className={r.m.below_floor ? "pill crit" : "pill ok"}>{fmt0(r.m.below_floor)} of {fmt0(r.m.products)} below</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

// Draft purchase order: printable, clearly marked as a draft that was not sent.
function printDraftPO(brand, o, lines, today) {
  const esc = (x) => String(x ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const rows = lines.map((l) => `<tr><td>${esc(l.product_id)}</td><td>${esc(l.name)}</td><td class="n">${fmt0(l.cases)}</td><td class="n">${l.kg != null ? fmt0(l.kg) : ""}</td><td class="n">${l.cbm != null ? fmt1(l.cbm) : ""}</td></tr>`).join("");
  const w = window.open("", "_blank", "width=900,height=1000");
  if (!w) return;
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>Draft PO ${esc(brandParts(brand).label)}</title><style>
    body{font:13px/1.5 Inter,system-ui,sans-serif;color:#111;margin:40px}h1{font-size:22px;margin:0}.draft{display:inline-block;border:2px solid #b91c1c;color:#b91c1c;
    font-weight:700;letter-spacing:.08em;padding:4px 10px;transform:rotate(-2deg);margin:12px 0}table{width:100%;border-collapse:collapse;margin-top:16px}
    th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}.n{text-align:right}tfoot td{font-weight:700}.meta{color:#555}</style></head><body>
    <h1>Purchase order (draft)</h1><div class="draft">DRAFT, NOT SENT</div>
    <p class="meta">Shakti Group USA · Supplier/brand: <b>${esc(brandParts(brand).label)}</b> · Prepared ${esc(today)} from the ordering engine's plan.<br>
    Order by ${esc(o.order_by_safe)} (safe, 130-day shipping), latest ${esc(o.order_by_latest)} · ${esc(o.covers)}</p>
    <table><thead><tr><th>Item code</th><th>Product</th><th class="n">Cases</th><th class="n">kg</th><th class="n">CBM</th></tr></thead><tbody>${rows}</tbody>
    <tfoot><tr><td></td><td>Total</td><td class="n">${fmt0(o.cases)}</td><td class="n">${o.kg != null ? fmt0(o.kg) : ""}</td><td class="n">${o.cbm != null ? fmt1(o.cbm) : ""}</td></tr></tfoot></table>
    <p class="meta">Quantities exclude products on hold. Stock already ordered or at sea is not included; check before ordering. This draft was not sent to anyone.</p>
    <script>window.onload=()=>window.print()</script></body></html>`);
  w.document.close();
}

// CSS-3D containers filled to the real fill level (rotate on hover).
function Containers3D({ n, lastPct }) {
  if (!n) return null;
  const shown = Math.min(n, 6);
  return (
    <div>
      <div className="ctr3d-row" aria-label={`${n} container${n === 1 ? "" : "s"}${lastPct != null ? `, last one ${Math.round(lastPct)}% full` : ""}`}>
        {Array.from({ length: shown }, (_, i) => {
          const pct = i === n - 1 && lastPct != null ? Math.max(3, Math.min(100, lastPct)) : 100;
          return (
            <div key={i}>
              <div className="ctr3d" aria-hidden="true">
                <div className="f back" /><div className="f side2" /><div className="f bottom" />
                <div className="load" style={{ width: `${(pct / 100) * 92}px` }} />
                <div className="f front" /><div className="f side" /><div className="f top" />
              </div>
              <div className="ctr3d-cap">{pct}% full</div>
            </div>
          );
        })}
      </div>
      {n > shown && <p className="note">+{n - shown} more full containers.</p>}
    </div>
  );
}

export default function Home() {
  const sum = useData("/summary");
  const br = useData("/brands");
  const [filter, setFilter] = useQueryState("f", "all");
  const [status, setStatus] = useQueryState("s", "all");
  const [q, setQ] = useQueryState("q", "");
  const [openQ, setOpenQ] = useQueryState("open", "");
  const open = openQ || null;
  const setOpen = (b) => setOpenQ(b || "");
  const [sel, setSel] = useState(-1);
  const [approved, approve, undo, canUndo] = useApprovals();
  const { confirm } = useAgent();
  const pointed = usePointed();
  const prefs = useTablePrefs("orders", [["weight", "Weight"], ["cbm", "CBM"], ["containers", "Containers"], ["risk", "SKUs at risk"]]);
  const tableRef = useRef(null);

  const withOrder = useMemo(() => (br.data ? br.data.brands.filter((b) => b.next_order) : []), [br.data]);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return withOrder.filter(FILTERS[filter])
      .filter((b) => status === "all" || b.next_order.status === status)
      .filter((b) => !needle || b.brand.toLowerCase().includes(needle));
  }, [withOrder, filter, status, q]);
  useEffect(() => setSel(-1), [filter, status, q]);

  const onKey = (e) => {
    if (!rows.length) return;
    if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); setSel((s) => Math.min(rows.length - 1, s + 1)); }
    else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    else if (e.key === "Enter" && sel >= 0) { e.preventDefault(); setOpen(rows[sel].brand); }
  };
  useEffect(() => { tableRef.current?.querySelector("tr.sel")?.scrollIntoView({ block: "nearest" }); }, [sel]);
  useEffect(() => { if (pointed.length) tableRef.current?.querySelector("tr.pointed")?.scrollIntoView({ block: "center", behavior: "smooth" }); }, [pointed]);

  if (sum.error || br.error) return <ErrorBox error={sum.error || br.error} />;
  if (!sum.data || !br.data) return <><SkeletonCards /><div className="skeleton"><span /><span /><span /></div></>;
  const s = sum.data;
  const today = s.today;
  const lateBrands = br.data.brands.filter((b) => b.late_orders > 0);
  const toggle = (f) => setFilter((x) => (x === f ? "all" : f));
  const reviewed = withOrder.filter((b) => approved[b.next_order.order_id]).length;
  const tot = rows.reduce((t, b) => ({ cases: t.cases + (b.next_order.cases || 0), kg: t.kg + (b.next_order.kg || 0), cbm: t.cbm + (b.next_order.cbm || 0),
    ctr: t.ctr + (b.next_order.containers_needed || 0) }), { cases: 0, kg: 0, cbm: 0, ctr: 0 });
  const exportRows = () => toCSV(`next-orders-${today}.csv`, rows.map((b) => ({ brand: brandParts(b.brand).label, ...b.next_order, reviewed: approved[b.next_order.order_id] ? "yes" : "" })),
    [...CSV.map(([k, l]) => [k, l]), ["reviewed", "Reviewed"]]);

  return (
    <div data-ready="home">
      <div className="page-head">
        <div>
          <h2>What to order</h2>
          <p>Sales data runs to the end of {monthLabel(s.data_through)}. Each order is sized to last about 3 months after it arrives;
            shipping takes 105 to 130 days, so the <Term k="safe">safe order date</Term> assumes 130.</p>
        </div>
        <div className="panel-side">
          <span className="progress" role="status">
            <span className="bar-track"><span className="bar-fill fill-ok" style={{ width: `${withOrder.length ? (reviewed / withOrder.length) * 100 : 0}%` }} /></span>
            {fmt0(reviewed)} of {fmt0(withOrder.length)} reviewed
            {canUndo && <button type="button" className="btn ghost" style={{ height: 26 }} onClick={undo}><Undo2 className="ico-sm" />Undo</button>}
          </span>
          <button className="btn" type="button" onClick={exportRows}><Download className="ico-sm" />Export CSV</button>
        </div>
      </div>

      <div className={`callout ${s.gross_need ? "info" : "warn"}`} role="note">
        <Ship className="ico" aria-hidden="true" />
        <div><b>{s.gross_need ? "Gross need." : "Check before ordering."}</b> {s.gross_need ? s.gross_need_note : s.stock_note}</div>
      </div>

      <div className="kpis stagger">
        <KPI icon={ShoppingCart} tone="crit" k="Brand orders to place now" v={s.orders_now} unit="orders" onClick={() => toggle("now")} pressed={filter === "now"}
             d={<>{cases(s.orders_now_cases)}{s.orders_now_cbm != null ? <>, {fmt0(s.orders_now_cbm)} <Term k="cbm">CBM</Term></> : null}{s.orders_now_containers != null ? ` · ${fmt0(s.orders_now_containers)} containers if each brand ships alone` : ""}</>} />
        <KPI icon={PackageCheck} tone="warn" k="Products needing a decision" v={s.products_needing_decision} unit="products" d="Now or this month"
             onClick={() => toggle("decision")} pressed={filter === "decision"} />
        <KPI icon={PauseCircle} tone="info" k="Products on hold" v={s.holds} unit="products" onClick={() => toggle("hold")} pressed={filter === "hold"}
             d={<>{fmt0(s.holds_in_plan)} still in the plan · <a href="#/questions" onClick={(e) => e.stopPropagation()}>confirm releases</a></>} />
        <KPI icon={AlertOctagon} tone={s.orders_too_late ? "crit" : "ok"} k="Orders already too late" v={s.orders_too_late} unit="orders"
             onClick={() => toggle("late")} pressed={filter === "late"}
             d={s.orders_too_late ? `${cases(s.cases_too_late)}: expedite or expect a shortfall` : "None"} />
      </div>

      <ShippingTimeline rows={rows} today={today} onOpen={setOpen} />
      <Scrubber brands={rows.map((b) => b.brand)} onOpen={setOpen} />

      <section className="panel" aria-labelledby="orders-h">
        <header>
          <div>
            <h2 id="orders-h">Next order per brand</h2>
            <p className="sub">Sorted by order date{filter !== "all" ? " · filtered by the card above" : ""}. Use j / k or the arrow keys, Enter to open.</p>
          </div>
          <div className="panel-side">
            <label className="search"><Search className="ico" aria-hidden="true" /><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter brands" aria-label="Filter brands" /></label>
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
              <option value="all">All statuses</option>
              {ORDER.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
            {filter !== "all" && <button type="button" className="btn ghost" onClick={() => setFilter("all")}>Clear card filter</button>}
            <TableControls prefs={prefs} />
            <span className="small muted" role="status">{rows.length} of {withOrder.length} brands</span>
          </div>
        </header>
        {rows.length === 0 ? <EmptyState icon={ShoppingCart} title="No brand matches">Clear the filters to see every brand's next order.</EmptyState> : (
          <div className={`tbl-wrap tall${prefs.compact ? " compact" : ""}`} ref={tableRef} tabIndex={0} onKeyDown={onKey} role="region" aria-label="Next order for each brand">
            <table className="tbl sticky-first">
              <thead>
                <tr>
                  <th scope="col">Brand</th><th scope="col">Status</th><th scope="col">Order by</th>
                  <th scope="col" className="num">Cases</th>{prefs.show("weight") && <th scope="col" className="num hide-sm">Weight</th>}
                  {prefs.show("cbm") && <th scope="col" className="num hide-sm"><Term k="cbm">CBM</Term></th>}{prefs.show("containers") && <th scope="col">Containers</th>}
                  {prefs.show("risk") && <th scope="col" className="hide-sm">SKUs at risk <Tip text="Products below the 3-month floor now (red) or soon (amber), out of the brand's active products." /></th>}
                  <th scope="col"><span className="sr-only">Why</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((b, i) => {
                  const o = b.next_order;
                  const passed = o.order_by_safe < today;
                  return (
                    <tr key={b.brand} className={`clickable${i === sel ? " sel" : ""}${pointed.includes(b.brand) ? " pointed" : ""}`} onClick={() => { setSel(i); setOpen(b.brand); }} aria-selected={i === sel}>
                      <th scope="row">
                        <BrandName name={b.brand} sub={`${o.products} product${o.products === 1 ? "" : "s"}${o.products_on_hold ? ` · ${o.products_on_hold} on hold` : ""}`} />
                      </th>
                      <td><div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}><StatusPill status={o.status} />{approved[o.order_id] && <Pill tone="ok" icon={CheckCircle2}>Approved</Pill>}</div></td>
                      <td>
                        <div style={{ fontWeight: 600 }}>{shortDate(passed ? o.order_by_latest : o.order_by_safe, true)}
                          <span className={`small ${passed ? "st-red" : "muted"}`} style={{ fontWeight: 500 }}> · {relDays(passed ? o.order_by_latest : o.order_by_safe, today)}</span></div>
                        <div className="small muted">{passed ? "latest date; the safe date has passed" : `latest ${shortDate(o.order_by_latest, true)}`}</div>
                      </td>
                      <td className="num" style={{ fontWeight: 600 }}>{fmt0(o.cases)}</td>
                      {prefs.show("weight") && <td className="num hide-sm">{weight(o.kg)}</td>}
                      {prefs.show("cbm") && <td className="num hide-sm">{o.cbm != null ? fmt1(o.cbm) : "-"}</td>}
                      {prefs.show("containers") && <td>{o.containers_needed != null
                        ? <ContainerFill n={o.containers_needed} lastPct={o.last_container_fill_pct} cbm={o.cbm} estimated={o.cbm_estimated_pct > 0} />
                        : o.containers != null ? <span className="muted">about {fmt1(o.containers)}</span> : <span className="muted">-</span>}</td>}
                      {prefs.show("risk") && <td className="hide-sm"><RiskBar now={b.below_floor_now} soon={b.below_floor_soon} total={b.active_products ?? b.products} /></td>}
                      <td><Why items={[["Sold per month", cases(b.monthly_cases)], ["On hand", b.on_hand_cases != null ? cases(b.on_hand_cases) : null],
                        ["Months of stock", b.months_of_cover != null ? `${fmt1(b.months_of_cover)} months` : null], ["Floor", "3 months of sales"],
                        ["Below the floor now", `${fmt0(b.below_floor_now || 0)} products`], ["Shipping", "105 to 130 days (safe date uses 130)"],
                        ["Order", `${cases(o.cases)}, ${o.covers}`]]} /></td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row">Total, {fmt0(rows.length)} brands</th><td /><td />
                  <td className="num">{fmt0(tot.cases)}</td>
                  {prefs.show("weight") && <td className="num hide-sm">{weight(tot.kg)}</td>}
                  {prefs.show("cbm") && <td className="num hide-sm">{fmt1(tot.cbm)}</td>}
                  {prefs.show("containers") && <td>{fmt0(tot.ctr)} containers</td>}
                  {prefs.show("risk") && <td className="hide-sm" />}<td />
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>

      {lateBrands.length > 0 && (
        <section className="panel" aria-labelledby="late-h">
          <header><div><h2 id="late-h">Too late for the full shipping time</h2><p className="sub">These arrive later than a normal order could: expedite or expect a shortfall</p></div></header>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th scope="col">Brand</th><th scope="col" className="num">Orders</th><th scope="col" className="num">Cases</th><th scope="col">Action</th></tr></thead>
              <tbody>
                {lateBrands.map((b) => (
                  <tr key={b.brand} className="clickable" onClick={() => setOpen(b.brand)}>
                    <th scope="row"><BrandName name={b.brand} /></th>
                    <td className="num">{b.late_orders}</td>
                    <td className="num">{fmt0(b.late_cases)}</td>
                    <td><StatusPill status={LATE} label="Expedite or expect a shortfall" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <BrandDrawer brand={open} today={today} onClose={() => setOpen(null)} approved={approved} onApprove={(id) => { approve(id); confirm(); }} />
    </div>
  );
}
