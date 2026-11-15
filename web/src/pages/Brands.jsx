import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, LayoutGrid, Rows3, Search, Tags } from "lucide-react";
import { useData } from "../useData.js";
import { cases } from "../format.js";
import { ErrorBox } from "../components.jsx";
import { BrandName, EmptyState, Gauge, RiskBar, RunwayBar, SavedViews, StatusPill, Term, Tip, brandParts, fmt0, relDays, shortDate, toCSV, useQueryState } from "../ui.jsx";
import { usePointed } from "../agent.jsx";
import { Download } from "lucide-react";

// Sort keys: [label, value getter, default direction]. Missing values always sort last.
const COLS = {
  brand: ["Brand", (b) => b.brand.toLowerCase(), "asc"],
  products: ["Products", (b) => b.active_products ?? b.products, "desc"],
  monthly: ["Sold per month", (b) => b.monthly_cases, "desc"],
  onhand: ["On hand", (b) => b.on_hand_cases, "desc"],
  cover: ["Months of stock", (b) => b.months_of_cover, "asc"],
  risk: ["SKUs below floor", (b) => (b.below_floor_now || 0) * 1000 + (b.below_floor_soon || 0), "desc"],
  next: ["Next order", (b) => b.next_order?.order_by_safe, "asc"],
  cases: ["Cases", (b) => b.next_order?.cases, "desc"],
};

function sortRows(rows, key, dir) {
  const get = COLS[key][1];
  return [...rows].sort((a, b) => {
    const x = get(a), y = get(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    const c = x < y ? -1 : x > y ? 1 : 0;
    return dir === "asc" ? c : -c;
  });
}

function Th({ k, sort, setSort, className = "", children }) {
  const active = sort[0] === k;
  const Icon = !active ? ArrowUpDown : sort[1] === "asc" ? ArrowUp : ArrowDown;
  return (
    <th scope="col" className={`sortable ${className}`} aria-sort={active ? (sort[1] === "asc" ? "ascending" : "descending") : "none"}
        onClick={() => setSort(active ? [k, sort[1] === "asc" ? "desc" : "asc"] : [k, COLS[k][2]])}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.currentTarget.click(); } }} tabIndex={0}>
      {children || COLS[k][0]}<span className="sort"><Icon className="ico-sm" aria-hidden="true" /></span>
    </th>
  );
}

export default function Brands() {
  const { data, error } = useData("/brands");
  const [q, setQ] = useQueryState("q", "");
  const [sortQ, setSortQ] = useQueryState("sort", "next.asc");
  const sort = sortQ.split(".");
  const setSort = ([k, d]) => setSortQ(`${k}.${d}`);
  const [view, setV] = useQueryState("view", "table");
  const pointed = usePointed();

  const rows = useMemo(() => {
    if (!data) return [];
    const needle = q.trim().toLowerCase();
    return sortRows(data.brands.filter((b) => !needle || b.brand.toLowerCase().includes(needle)), sort[0], sort[1]);
  }, [data, q, sort]);

  if (error) return <ErrorBox error={error} />;
  if (!data) return <div className="skeleton"><span /><span /><span /></div>;
  const today = data.today;
  const stock = data.stock_used;
  const go = (b) => { window.location.hash = `/brand/${encodeURIComponent(b)}`; };
  const exportRows = () => toCSV(`brands-${today}.csv`, rows, [[(b) => brandParts(b.brand).label, "Brand"], [(b) => b.active_products ?? b.products, "Products"],
    ["monthly_cases", "Sold per month (cases)"], ["on_hand_cases", "On hand (cases)"], ["months_of_cover", "Months of stock"], ["below_floor_now", "Below floor now"],
    ["below_floor_soon", "Below floor soon"], [(b) => b.next_order?.status ?? "", "Next order"], [(b) => b.next_order?.order_by_safe ?? "", "Order by"], [(b) => b.next_order?.cases ?? "", "Cases"]]);

  return (
    <div data-ready="brands">
      <div className="page-head">
        <div>
          <h2>Brands</h2>
          <p>{data.brands.length} brands. {stock ? "Months of stock and the SKU split use stock on hand." : "Gross need: stock on hand not included."}{" "}
            A brand can have plenty of stock overall and still have many products below the <Term k="floor">3-month floor</Term>: the split bar shows it.</p>
        </div>
        <div className="panel-side">
          <label className="search"><Search className="ico" aria-hidden="true" />
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter brands" aria-label="Filter brands" /></label>
          <div className="seg" role="group" aria-label="View">
            <button type="button" aria-pressed={view === "table"} onClick={() => setV("table")}><Rows3 className="ico-sm" />Table</button>
            <button type="button" aria-pressed={view === "grid"} onClick={() => setV("grid")}><LayoutGrid className="ico-sm" />Cards</button>
          </div>
          <SavedViews id="brands" />
          <button type="button" className="btn" onClick={exportRows}><Download className="ico-sm" />CSV</button>
          <span className="small muted" role="status">{rows.length} shown</span>
        </div>
      </div>

      {rows.length === 0 ? <div className="panel"><EmptyState icon={Tags} title="No brand matches">Try part of the name.</EmptyState></div>
        : view === "grid" ? (
          <div className="brand-grid stagger">
            {rows.map((b) => (
              <button type="button" key={b.brand} className={`card brand-card${pointed.includes(b.brand) ? " pointed" : ""}`} onClick={() => go(b.brand)}>
                <div className="row">
                  <BrandName name={b.brand} sub={`${fmt0(b.active_products ?? b.products)} products · ${cases(b.monthly_cases)}/mo`} />
                  {b.next_order ? <StatusPill status={b.next_order.status} /> : <span className="pill neutral">No order</span>}
                </div>
                {stock && (
                  <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                    <Gauge months={b.months_of_cover} />
                    <div className="small muted">{b.on_hand_cases != null ? `${fmt0(b.on_hand_cases)} cases on hand` : ""}<br />floor: 3 months</div>
                  </div>
                )}
                <div>
                  <div className="small muted" style={{ marginBottom: 4 }}>SKUs below the 3-month floor</div>
                  <RiskBar now={b.below_floor_now} soon={b.below_floor_soon} total={b.active_products ?? b.products} />
                </div>
                <div className="row small">
                  <span className="muted">{b.next_order ? `Order by ${shortDate(b.next_order.order_by_safe, true)}` : "Nothing planned"}</span>
                  <b>{b.next_order ? cases(b.next_order.cases) : ""}</b>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div className="tbl-wrap tall">
            <table className="tbl sticky-first">
              <caption className="sr-only">Every brand with its sales pace, stock, SKU risk and next order. Click a column to sort.</caption>
              <thead>
                <tr>
                  <Th k="brand" sort={sort} setSort={setSort} />
                  <Th k="products" sort={sort} setSort={setSort} className="num" />
                  <Th k="monthly" sort={sort} setSort={setSort} className="num" />
                  {stock && <Th k="onhand" sort={sort} setSort={setSort} className="num" />}
                  {stock && <Th k="cover" sort={sort} setSort={setSort}>Stock runway <Tip text="Months of stock on hand at the last 6 months' pace. The black tick is the 3-month floor." /></Th>}
                  <Th k="risk" sort={sort} setSort={setSort}>SKU split <Tip text="Products below the 3-month floor now (red), soon (amber) and healthy (green)." /></Th>
                  <Th k="next" sort={sort} setSort={setSort} />
                  <Th k="cases" sort={sort} setSort={setSort} className="num" />
                </tr>
              </thead>
              <tbody>
                {rows.map((b) => (
                  <tr key={b.brand} className={`clickable${pointed.includes(b.brand) ? " pointed" : ""}`} onClick={() => go(b.brand)}>
                    <th scope="row"><BrandName name={b.brand} href={`#/brand/${encodeURIComponent(b.brand)}`} /></th>
                    <td className="num">{fmt0(b.active_products ?? b.products)}</td>
                    <td className="num">{b.monthly_cases != null ? fmt0(b.monthly_cases) : "-"}</td>
                    {stock && <td className="num">{b.on_hand_cases != null ? fmt0(b.on_hand_cases) : "-"}</td>}
                    {stock && <td style={{ minWidth: 150 }}><RunwayBar months={b.months_of_cover} /></td>}
                    <td style={{ minWidth: 150 }}><RiskBar now={b.below_floor_now} soon={b.below_floor_soon} total={b.active_products ?? b.products} /></td>
                    <td>{b.next_order ? (
                      <div style={{ display: "grid", gap: 4, justifyItems: "start" }}>
                        <StatusPill status={b.next_order.status} />
                        <span className="small muted">{shortDate(b.next_order.order_by_safe, true)} · {relDays(b.next_order.order_by_safe, today)}</span>
                      </div>) : <span className="muted small">none planned</span>}</td>
                    <td className="num" style={{ fontWeight: 600 }}>{b.next_order ? fmt0(b.next_order.cases) : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}
