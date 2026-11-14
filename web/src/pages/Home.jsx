import { useEffect, useMemo, useState } from "react";
import { useData } from "../useData.js";
import { getJSON } from "../api.js";
import { cases, niceDate, weight, monthLabel, orderBy, STATUS } from "../format.js";
import { ContainerLine, ErrorBox, GrossBanner, Loading, StatusBadge } from "../components.jsx";

const ORDER = ["Order now", "Order this month", "Coming up"];


export default function Home() {
  const sum = useData("/summary");
  const br = useData("/brands");
  const [status, setStatus] = useState("all");
  const [q, setQ] = useState("");
  const [found, setFound] = useState({ products: [], brands: [] });

  useEffect(() => {
    if (q.trim().length < 2) { setFound({ products: [], brands: [] }); return; }
    const t = setTimeout(() => getJSON(`/search?q=${encodeURIComponent(q)}`).then(setFound).catch(() => {}), 200);
    return () => clearTimeout(t);
  }, [q]);

  const shown = useMemo(() => {
    if (!br.data) return [];
    const needle = q.trim().toLowerCase();
    const productBrands = new Set(found.products.map((p) => p.brand));
    return br.data.brands.filter((b) => b.next_order)
      .filter((b) => status === "all" || b.next_order.status === status)
      .filter((b) => !needle || b.brand.toLowerCase().includes(needle) || productBrands.has(b.brand));
  }, [br.data, status, q, found]);

  if (sum.error || br.error) return <ErrorBox error={sum.error || br.error} />;
  if (sum.loading || br.loading) return <Loading />;
  const s = sum.data;
  const today = s.today;

  return (
    <div data-ready="home">
      <h1>What to order</h1>
      <p className="muted">Today is {niceDate(today, today, { relative: false })}. Sales data up to the end of {monthLabel(s.data_through)}.</p>
      {s.gross_need && <GrossBanner text={s.gross_need_note} />}
      {s.stock_used && <p className={`banner${s.on_order_missing ? " warn" : ""}`} role="note">{s.stock_note}</p>}
      {s.stock_used && s.orders_now > 0 && (
        <p style={{ fontSize: "1.15rem" }}><strong>{s.orders_now} brand orders to place now</strong>: {cases(s.orders_now_cases)},
          {" "}{s.orders_now_cbm != null ? `${s.orders_now_cbm.toLocaleString("en-US", { maximumFractionDigits: 0 })} CBM: about ${Math.ceil(s.orders_now_container_volume)} containers' worth of space, or ${s.orders_now_containers} containers if every brand ships in its own` : ""}
          {s.cbm_estimated_pct > 0 && <span className="tag"> {Math.round(s.cbm_estimated_pct)}% of the volume is estimated</span>}</p>
      )}

      <div className="stats">
        <div className="stat"><div className="big">{s.products_needing_decision.toLocaleString("en-US")}</div>products need an order decision now or this month</div>
        <div className="stat"><div className="big">{s.holds.toLocaleString("en-US")}</div>products on hold ({s.holds_in_plan} still in the plan) — <a href="#/questions">confirm releases</a></div>
        <div className="stat"><div className="big">{s.orders_too_late.toLocaleString("en-US")}</div>brand orders are already too late for the full shipping time ({cases(s.cases_too_late)}): expedite or expect a shortfall</div>
      </div>

      {s.next_orders.length > 0 && (
        <>
          <h2>Next orders due</h2>
          <ol>
            {s.next_orders.map((o) => (
              <li key={o.order_id}>
                <a href={`#/brand/${encodeURIComponent(o.brand)}`}><strong>{o.brand}</strong></a>: {orderBy(o, today)},
                {" "}{cases(o.cases)}, {weight(o.kg)} — <StatusBadge status={o.status} />
              </li>
            ))}
          </ol>
        </>
      )}

      <h2>Brands</h2>
      <div className="controls">
        <label>Show
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">All brands</option>
            {ORDER.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
        <label>Search a brand or product
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. MDH or basmati" />
        </label>
      </div>
      {found.products.length > 0 && (
        <p className="muted">Products matching “{q}”: {found.products.slice(0, 8).map((p, i) => (
          <span key={p.product_id}>{i ? ", " : ""}<a href={`#/product/${encodeURIComponent(p.product_id)}`}>{p.name}</a> ({p.brand})</span>
        ))}{found.products.length > 8 ? " …" : ""}</p>
      )}
      <p className="muted">{shown.length} brands, sorted by order-by date.</p>
      <div className="grid">
        {shown.map((b) => {
          const o = b.next_order;
          const cls = (STATUS[o.status] || {}).cls || "coming";
          return (
            <a key={b.brand} className={`card ${cls}`} href={`#/brand/${encodeURIComponent(b.brand)}`}>
              <h3>{b.brand}</h3>
              <StatusBadge status={o.status} />
              <div className="when">{orderBy(o, today)}</div>
              {o.order_by_safe < today && <div className="muted">The safe date ({niceDate(o.order_by_safe, today, { relative: false })}) has passed; ordering by the latest date only works if shipping is quick.</div>}
              <div className="amounts"><strong>{cases(o.cases)}</strong> · {weight(o.kg)}{o.cbm == null && o.containers != null ? ` · about ${o.containers} containers` : ""}</div>
              {o.cbm != null && <div><ContainerLine o={o} /></div>}
              {o.next_order_by && <div className="muted">Lasts about 3 months after it arrives; next order by {niceDate(o.next_order_by, today)}</div>}
              {o.pulled_forward_cases > 0 && <div className="muted">Includes {cases(o.pulled_forward_cases)} pulled forward to fill the last container</div>}
              {(b.below_floor_now > 0 || b.below_floor_soon > 0) && (
                <div className="badge late" style={{ whiteSpace: "normal" }}>⚠ {b.below_floor_now} product(s) below 3 months of stock now
                  {b.below_floor_soon > 0 ? `, ${b.below_floor_soon} more before a new order can arrive` : ""}</div>)}
              {b.on_hand_cases != null && <div className="muted">In the warehouse: {cases(b.on_hand_cases)}{b.months_of_cover != null ? ` (about ${b.months_of_cover} months of sales)` : ""}</div>}
              <div>{o.products} products{o.products_on_hold ? `, plus ${o.products_on_hold} on hold (not counted)` : ""}</div>
              <div className="muted">{o.reason}</div>
              {b.late_orders > 0 && <div className="muted">Earlier: {b.late_orders} order(s) too late for full shipping time ({cases(b.late_cases)})</div>}
            </a>
          );
        })}
      </div>
    </div>
  );
}
