import { useMemo, useState } from "react";
import { useData } from "../useData.js";
import { niceDate, sentence, titleCase } from "../format.js";
import { ErrorBox, Figure, Loading, Panel, Source, Table, Tag, Toolbar } from "../components.jsx";

// Risk sentence from the engine: red when already below the 3-month floor or running out, amber when it dips later.
function riskOf(r) {
  const s = String(r || "").toLowerCase();
  if (s.includes("floor now") || s.includes("runs out")) return "st-red";
  if (s.includes("dips") || s.includes("expedite")) return "st-amber";
  return "st-grey";
}
const LEAD = { confirmed: ["Confirmed", "st-green"], rejected: ["Rejected by owner", "st-grey"], unverified_lead: ["Unverified, not contacted", "st-amber"] };
// Capitalise only the first letter; keeps month names and codes as the engine wrote them.
const cap = (s) => { const t = String(s || ""); return t.charAt(0).toUpperCase() + t.slice(1); };
const list = (s) => String(s || "").split(";").map((x) => x.trim()).filter(Boolean);

function Options({ options, today }) {
  if (!options?.length) return <p className="note">No alternative sources found for this product.</p>;
  return (
    <Table id={`opt-${options[0].product_id}`} caption="Ways to source it, best first" stack>
      <thead>
        <tr><th scope="col" className="num">Rank</th><th scope="col">Option</th><th scope="col">Country</th><th scope="col">Supplier</th>
          <th scope="col">Harvest timing</th><th scope="col">Alert</th><th scope="col">Source</th></tr>
      </thead>
      <tbody>
        {options.map((o) => (
          <tr key={`${o.rank}-${o.country}-${o.lead_id}`}>
            <td className="num" data-label="Rank">{o.rank || "-"}</td>
            <th scope="row" data-label="Option">{sentence(o.label || o.tier)}{o.consolidation && <div className="small muted">{o.consolidation}</div>}</th>
            <td data-label="Country">{o.country || "-"}</td>
            <td data-label="Supplier">{o.company || <span className="muted">no supplier yet</span>}</td>
            <td data-label="Harvest timing">{o.harvest_timing || "-"}</td>
            <td data-label="Alert">{o.alert ? <span className="st-amber">{o.alert}</span> : <span className="muted">none</span>}</td>
            <td data-label="Source"><Source url={o.source_url} /></td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export default function Sourcing() {
  const { data, error } = useData("/sourcing");
  const [commodity, setCommodity] = useState("all");
  const [open, setOpen] = useState(null);
  const products = useMemo(() => (data ? data.products.filter((p) => commodity === "all" || p.commodity === commodity) : []), [data, commodity]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading what="Loading sourcing" />;
  const today = data.today;
  const atRisk = data.products.filter((p) => riskOf(p.risk) === "st-red").length;

  return (
    <div data-ready="sourcing">
      <section className="panel" aria-labelledby="src-sum">
        <header>
          <h2 id="src-sum">Sourcing</h2>
          <p className="sub">Products at risk of running out, other countries and suppliers that could fill the gap, and problems reported in producing countries</p>
        </header>
        <div className="figures cols-4">
          <Figure k="Products with sourcing options" v={data.products.length} unit="products" />
          <Figure k="Below the 3-month floor or running out" v={atRisk} unit="products"
                  status={atRisk ? ["Act now", "st-red"] : ["None", "st-green"]} />
          <Figure k="Supplier leads" v={data.leads.length} unit="leads" d="Owner confirms every supplier before use." />
          <Figure k="Active country alerts" v={data.alerts.length} unit="alerts"
                  status={data.alerts.length ? ["Check sources", "st-amber"] : ["None", "st-green"]} />
        </div>
      </section>

      <Panel id="src-products" title="At-risk products" sub="Select a product to see its options">
        {data.products.length === 0 ? (
          <p className="note">No products need other sources in this plan. Products appear here when stock would run out before a normal order could arrive.</p>
        ) : (
          <>
            <Toolbar>
              <label>Commodity
                <select value={commodity} onChange={(e) => setCommodity(e.target.value)}>
                  <option value="all">All commodities</option>
                  {data.commodities.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
                </select>
              </label>
              <span className="count" role="status">{products.length} shown</span>
            </Toolbar>
            <Table id="src-p" caption="At-risk products and when they run out">
              <thead>
                <tr><th scope="col">Product</th><th scope="col">Brand</th><th scope="col">Commodity</th><th scope="col">Risk</th>
                  <th scope="col">Runs out</th><th scope="col">If ordered today</th><th scope="col" className="num">Options</th><th scope="col"><span className="sr-only">Details</span></th></tr>
              </thead>
              <tbody>
                {products.map((p) => (
                  <tr key={p.product_id} className={open === p.product_id ? "emph" : ""}>
                    <th scope="row"><a href={`#/product/${encodeURIComponent(p.product_id)}`}>{p.name || p.product_id}</a>
                      <div className="small muted">{p.product_id}</div></th>
                    <td data-label="Brand">{p.brand || "-"}</td>
                    <td data-label="Commodity">{titleCase(p.commodity)}</td>
                    <td data-label="Risk"><span className={`small ${riskOf(p.risk)}`} style={{ fontWeight: 500 }}>{cap(p.risk || "unknown")}</span></td>
                    <td data-label="Runs out">{niceDate(p.run_out_date, today)}</td>
                    <td data-label="If ordered today">{(() => {
                      const t = String(p.arrival_if_ordered_today || "");
                      const cls = t.startsWith("too late") ? "st-red" : t.startsWith("in time") ? "st-green" : "";
                      return t ? <span className={`small ${cls}`}>{cap(t)}</span> : <span className="muted">-</span>;
                    })()}</td>
                    <td className="num" data-label="Options">{p.options?.length || 0}</td>
                    <td data-label="Details">
                      <button className="btn" type="button" aria-expanded={open === p.product_id}
                              onClick={() => setOpen(open === p.product_id ? null : p.product_id)}>
                        {open === p.product_id ? "Hide" : "Options"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
            {open && (() => {
              const p = data.products.find((x) => x.product_id === open);
              return p ? (
                <div className="order-block">
                  <h3 className="small" style={{ fontSize: 15, margin: "0 0 8px" }}>{p.name || p.product_id}</h3>
                  {list(p.unknowns).length > 0 && <p className="note">Not known yet: {list(p.unknowns).join(", ")}</p>}
                  <Options options={p.options} today={today} />
                </div>
              ) : null;
            })()}
          </>
        )}
      </Panel>

      <Panel id="src-leads" title="Supplier leads" sub="Found by research or imported; never contacted automatically">
        {data.leads.length === 0 ? <p className="note">No supplier leads recorded yet.</p> : (
          <Table id="src-l" caption="Supplier leads by country">
            <thead>
              <tr><th scope="col">Company</th><th scope="col">Country</th><th scope="col">Commodities</th><th scope="col">Status</th>
                <th scope="col" className="num">Found</th><th scope="col">Source</th></tr>
            </thead>
            <tbody>
              {data.leads.map((l) => (
                <tr key={l.lead_id}>
                  <th scope="row">{l.company}{l.is_demo === "yes" && <>{" "}<Tag>demo</Tag></>}</th>
                  <td data-label="Country">{l.country}</td>
                  <td data-label="Commodities">{list(l.commodities).map(titleCase).join(", ")}</td>
                  <td data-label="Status">{(() => { const [t, c] = LEAD[l.status] || [sentence(l.status || "unconfirmed"), "st-grey"]; return <span className={`status-text ${c}`}>{t}</span>; })()}</td>
                  <td className="num" data-label="Found">{niceDate(l.found_on, null, { relative: false })}</td>
                  <td data-label="Source"><Source url={l.source_url || l.website} /></td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Panel>

      <Panel id="src-alerts" title="Country alerts" sub="Dismiss an alert in Questions for you">
        {data.alerts.length === 0 ? <p className="note">No active alerts.</p> : (
          <Table id="src-a" caption="Problems reported in producing countries">
            <thead>
              <tr><th scope="col">Type</th><th scope="col">Commodity</th><th scope="col">Country</th><th scope="col">Issue</th>
                <th scope="col" className="num">Reported</th><th scope="col">Source</th></tr>
            </thead>
            <tbody>
              {data.alerts.map((a) => (
                <tr key={a.alert_id}>
                  <td data-label="Type"><span className={`label ${a.label === "demo" ? "demo" : "real"}`}>{a.label}</span></td>
                  <td data-label="Commodity">{titleCase(a.commodity)}</td>
                  <th scope="row" data-label="Country">{a.country}</th>
                  <td data-label="Issue">{a.issue}</td>
                  <td className="num" data-label="Reported">{niceDate(a.reported_on, null, { relative: false })}</td>
                  <td data-label="Source"><Source url={a.source_url} /></td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Panel>
    </div>
  );
}
