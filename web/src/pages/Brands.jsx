import { useMemo, useState } from "react";
import { useData } from "../useData.js";
import { cases, niceDate } from "../format.js";
import { ErrorBox, Loading, Panel, StatusText, Table, Toolbar } from "../components.jsx";

const SORTS = {
  date: ["Next order date", (a, b) => (a.next_order?.order_by_safe || "9999").localeCompare(b.next_order?.order_by_safe || "9999")],
  name: ["Name", (a, b) => a.brand.localeCompare(b.brand)],
  volume: ["Monthly sales", (a, b) => (b.monthly_cases || 0) - (a.monthly_cases || 0)],
  cover: ["Months of stock (lowest first)", (a, b) => (a.months_of_cover ?? 999) - (b.months_of_cover ?? 999)],
};

export default function Brands() {
  const { data, error } = useData("/brands");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("date");
  const rows = useMemo(() => {
    if (!data) return [];
    const needle = q.trim().toLowerCase();
    return data.brands.filter((b) => !needle || b.brand.toLowerCase().includes(needle)).sort(SORTS[sort][1]);
  }, [data, q, sort]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const today = data.today;
  const stock = data.stock_used;
  return (
    <div data-ready="brands">
      <Panel id="brands-h" title={`${data.brands.length} brands`} sub={stock ? "Stock on hand included" : "Gross need: stock on hand not included"}>
        <Toolbar>
          <label>Brand
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by name" />
          </label>
          <label>Sort by
            <select value={sort} onChange={(e) => setSort(e.target.value)}>
              {Object.entries(SORTS).map(([k, [label]]) => <option key={k} value={k}>{label}</option>)}
            </select>
          </label>
          <span className="count" role="status">{rows.length} shown</span>
        </Toolbar>
        <Table id="brands" caption="Every brand with its sales pace, stock and next order">
          <thead>
            <tr>
              <th scope="col">Brand</th><th scope="col" className="num">Products</th><th scope="col" className="num">Sold per month</th>
              {stock && <th scope="col" className="num">On hand</th>}
              {stock && <th scope="col" className="num">Months of stock</th>}
              <th scope="col">Next order</th><th scope="col" className="num">Cases</th><th scope="col">Below 3-month floor</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.brand}>
                <th scope="row"><a href={`#/brand/${encodeURIComponent(b.brand)}`}>{b.brand}</a></th>
                <td className="num" data-label="Products">{b.active_products ?? b.products ?? "-"}</td>
                <td className="num" data-label="Sold per month">{b.monthly_cases != null ? cases(b.monthly_cases) : "-"}</td>
                {stock && <td className="num" data-label="On hand">{b.on_hand_cases != null ? cases(b.on_hand_cases) : "-"}</td>}
                {stock && <td className="num" data-label="Months of stock">{b.months_of_cover != null ? b.months_of_cover.toFixed(1) : "-"}</td>}
                <td data-label="Next order">{b.next_order ? <><StatusText status={b.next_order.status} />
                  <div className="small">{niceDate(b.next_order.order_by_safe, today)}</div></> : <span className="muted">none planned</span>}</td>
                <td className="num" data-label="Cases">{b.next_order ? cases(b.next_order.cases, false) : "-"}</td>
                <td data-label="Below 3-month floor">{b.below_floor_now > 0 ? <span className="st-red">{b.below_floor_now} now</span>
                  : b.below_floor_soon > 0 ? <span className="st-amber">{b.below_floor_soon} soon</span> : <span className="muted">none</span>}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Panel>
    </div>
  );
}
