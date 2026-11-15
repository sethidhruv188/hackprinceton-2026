import { useEffect, useState } from "react";
import { useData } from "../useData.js";
import { cases, niceDate, monthLabel, sentence } from "../format.js";
import { ErrorBox, Loading, Panel, Table, Tag, Toolbar } from "../components.jsx";

function statusOf(p) {
  if (p.on_hold) return ["On hold", "st-amber"];
  if (!p.in_plan) return ["Not in the plan (no recent sales)", "st-grey"];
  const s = String(p.status || "").toLowerCase();
  if (p.below_floor_now || s.includes("below 3-month floor")) return ["Below 3 months of stock: expedite", "st-red"];
  if (p.order_now_cases > 0) return [`Order now: ${cases(p.order_now_cases)}`, "st-red"];
  if (s.includes("annual harvest")) return ["Annual harvest buy", "st-grey"];
  if (p.ok_until) return [`OK until ${niceDate(p.ok_until, null, { relative: false })}`, "st-green"];
  return [p.status ? sentence(p.status) : "In the plan", "st-grey"];
}

export default function Products() {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [show, setShow] = useState("attention");
  useEffect(() => { const t = setTimeout(() => setTerm(q.trim()), 250); return () => clearTimeout(t); }, [q]);
  const { data, error, loading } = useData(`/products?show=${show}&limit=300&q=${encodeURIComponent(term)}`);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <div data-ready="products">
      <Panel id="products-h" title="Products" sub={`${data.total.toLocaleString("en-US")} products in the catalogue`}>
        <Toolbar>
          <label>Search
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, item code or brand" />
          </label>
          <label>Show
            <select value={show} onChange={(e) => setShow(e.target.value)}>
              <option value="attention">Needs attention</option>
              <option value="all">All products</option>
            </select>
          </label>
          <span className="count" role="status">{loading ? "Updating" : `${data.matched.toLocaleString("en-US")} match${data.matched === 1 ? "" : "es"}${data.matched > data.products.length ? `, first ${data.products.length} shown` : ""}`}</span>
        </Toolbar>
        <Table id="products" caption="Products with their brand, stock and next planned month">
          <thead>
            <tr>
              <th scope="col">Product</th><th scope="col">Brand</th><th scope="col">Status</th>
              {data.stock_used && <th scope="col" className="num">On hand</th>}
              <th scope="col">Next month needed</th><th scope="col" className="num">Cases that month</th>
            </tr>
          </thead>
          <tbody>
            {data.products.map((p) => {
              const [label, cls] = statusOf(p);
              return (
                <tr key={p.product_id} className={p.on_hold ? "hold" : undefined}>
                  <th scope="row"><a href={`#/product/${encodeURIComponent(p.product_id)}`}>{p.name}</a>
                    <div className="small muted">{p.product_id}{p.category ? `, ${p.category.replace(/_/g, " ")}` : ""}</div></th>
                  <td data-label="Brand">{p.brand ? <a href={`#/brand/${encodeURIComponent(p.brand)}`}>{p.brand}</a> : <span className="muted">-</span>}</td>
                  <td data-label="Status"><span className={`status-text ${cls}`}>{label}</span>
                    {p.floor_dip_date && !p.below_floor_now && <div className="small st-amber">below 3 months from {niceDate(p.floor_dip_date, null, { relative: false })}</div>}</td>
                  {data.stock_used && <td className="num" data-label="On hand">{p.on_hand_cases != null ? cases(p.on_hand_cases, false) : "-"}</td>}
                  <td data-label="Next month needed">{p.next_month ? monthLabel(p.next_month) : <span className="muted">-</span>}</td>
                  <td className="num" data-label="Cases that month">{p.next_cases != null ? cases(p.next_cases, false) : "-"}</td>
                </tr>
              );
            })}
          </tbody>
        </Table>
        {data.products.length === 0 && <p className="note">No products match. {show === "attention" && <>Try <Tag>All products</Tag>.</>}</p>}
      </Panel>
    </div>
  );
}
