import { useMemo, useState } from "react";
import { useData } from "../useData.js";
import { cases, niceDate, weight, monthLabel, STATUS } from "../format.js";
import { Banner, ContainerLine, downloadCSV, ErrorBox, Figure, GrossBanner, Loading, Panel, StatusText, Table, Toolbar } from "../components.jsx";

const ORDER = ["Order now", "Order this month", "Coming up"];
const LATE = "Too late for full lead time: expedite";
const n0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

const CSV = [["brand", "Brand"], ["status", "Status"], ["order_by_safe", "Order by (safe)"], ["order_by_latest", "Order by (latest)"],
  ["covers", "Covers"], ["cases", "Cases"], ["kg", "kg"], ["cbm", "CBM"], ["containers_needed", "Containers"], ["products", "Products"],
  ["reason", "Why"]];

// What to order: every brand's next order in one table, most urgent first.
export default function Home() {
  const sum = useData("/summary");
  const br = useData("/brands");
  const [status, setStatus] = useState("all");
  const [q, setQ] = useState("");

  const rows = useMemo(() => {
    if (!br.data) return [];
    const needle = q.trim().toLowerCase();
    return br.data.brands.filter((b) => b.next_order)
      .filter((b) => status === "all" || b.next_order.status === status)
      .filter((b) => !needle || b.brand.toLowerCase().includes(needle));
  }, [br.data, status, q]);

  if (sum.error || br.error) return <ErrorBox error={sum.error || br.error} />;
  if (!sum.data || !br.data) return <Loading />;
  const s = sum.data;
  const today = s.today;
  const lateBrands = br.data.brands.filter((b) => b.late_orders > 0);

  return (
    <div data-ready="home">
      <p className="lead">
        Sales data up to the end of {monthLabel(s.data_through)}. Each order is sized to last about 3 months after it arrives;
        shipping takes 105 to 130 days, so the safe order date assumes 130.
      </p>
      {s.gross_need ? <GrossBanner text={s.gross_need_note} /> : <Banner tone={s.on_order_missing ? "warn" : ""}>{s.stock_note}</Banner>}

      <div className="figures cols-4" style={{ marginBottom: 32 }}>
        <Figure k="Brand orders to place now" v={n0.format(s.orders_now)} unit="orders" status={["Order now", "st-red"]}
          d={`${cases(s.orders_now_cases)}${s.orders_now_cbm != null ? `, ${n0.format(s.orders_now_cbm)} CBM (${s.orders_now_containers} containers if each brand ships alone)` : ""}`} />
        <Figure k="Products needing a decision" v={n0.format(s.products_needing_decision)} unit="products" d="Now or this month" />
        <Figure k="Products on hold" v={n0.format(s.holds)} unit="products"
          d={<>{s.holds_in_plan} still in the plan. <a href="#/questions">Confirm releases</a></>} />
        <Figure k="Orders already too late" v={n0.format(s.orders_too_late)} unit="orders"
          status={s.orders_too_late ? ["Expedite or expect a shortfall", "st-red"] : ["None", "st-green"]} d={cases(s.cases_too_late)} />
      </div>

      <Panel id="orders-h" title="Next order per brand" sub="Sorted by order date"
        actions={<button className="btn" type="button" onClick={() => downloadCSV(`next-orders-${today}.csv`, rows.map((b) => ({ brand: b.brand, ...b.next_order })), CSV)}>Export CSV</button>}>
        <Toolbar>
          <label>Status
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="all">All</option>
              {ORDER.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </label>
          <label>Brand
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by brand name" />
          </label>
          <span className="count" role="status">{rows.length} of {br.data.brands.filter((b) => b.next_order).length} brands</span>
        </Toolbar>
        <Table id="orders" caption="Next order for each brand: when to place it, how much, and why">
          <thead>
            <tr>
              <th scope="col">Brand</th><th scope="col">Status</th><th scope="col">Order by</th><th scope="col">Covers</th>
              <th scope="col" className="num">Cases</th><th scope="col" className="num">Weight</th><th scope="col">Containers</th>
              <th scope="col" className="num">Products</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => {
              const o = b.next_order;
              const passed = o.order_by_safe < today;
              return (
                <tr key={b.brand}>
                  <th scope="row"><a href={`#/brand/${encodeURIComponent(b.brand)}`}>{b.brand}</a>
                    {(b.below_floor_now > 0 || b.below_floor_soon > 0) && (
                      <div className="small st-red">{b.below_floor_now} below 3 months of stock now{b.below_floor_soon ? `, ${b.below_floor_soon} soon` : ""}</div>)}
                  </th>
                  <td data-label="Status"><StatusText status={o.status} /></td>
                  <td data-label="Order by">{niceDate(passed ? o.order_by_latest : o.order_by_safe, today)}
                    <div className="small muted">{passed ? "latest date; the safe date has passed" : `latest ${niceDate(o.order_by_latest, today, { relative: false })}`}</div></td>
                  <td data-label="Covers">{o.covers}</td>
                  <td className="num" data-label="Cases">{cases(o.cases, false)}</td>
                  <td className="num" data-label="Weight">{weight(o.kg)}</td>
                  <td data-label="Containers">{o.cbm != null ? <ContainerLine o={o} /> : o.containers != null ? `about ${o.containers}` : "-"}</td>
                  <td className="num" data-label="Products">{o.products}{o.products_on_hold ? <div className="small muted">+{o.products_on_hold} on hold</div> : null}</td>
                </tr>
              );
            })}
          </tbody>
        </Table>
        {rows.length === 0 && <p className="note">No brand matches these filters.</p>}
      </Panel>

      {lateBrands.length > 0 && (
        <Panel id="late-h" title="Too late for the full shipping time" sub="These arrive later than a normal order could">
          <Table id="late" caption="Brands with orders that can no longer arrive in time by sea at the normal lead time">
            <thead><tr><th scope="col">Brand</th><th scope="col" className="num">Orders</th><th scope="col" className="num">Cases</th><th scope="col">Action</th></tr></thead>
            <tbody>
              {lateBrands.map((b) => (
                <tr key={b.brand}>
                  <th scope="row"><a href={`#/brand/${encodeURIComponent(b.brand)}`}>{b.brand}</a></th>
                  <td className="num" data-label="Orders">{b.late_orders}</td>
                  <td className="num" data-label="Cases">{cases(b.late_cases, false)}</td>
                  <td data-label="Action"><span className={STATUS[LATE].cls}>Expedite or expect a shortfall</span></td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Panel>
      )}
    </div>
  );
}
