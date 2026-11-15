import { useMemo, useState } from "react";
import { useData } from "../useData.js";
import { cases, niceDate, sentence, shortMonth, titleCase } from "../format.js";
import { ErrorBox, Figure, Loading, Panel, Source, Table, Tag, Toolbar } from "../components.jsx";

// Harvest status from the engine, shown as text with a restrained color.
const HSTATUS = {
  "ORDER NOW": ["Order now", "st-red"],
  upcoming: ["Upcoming", "st-amber"],
  "window closed": ["Window closed", "st-grey"],
  "no harvest data": ["No harvest data", "st-grey"],
  "no products": ["No products", "st-grey"],
};
const hstatus = (s) => HSTATUS[s] || [sentence(s || "unknown"), "st-grey"];

// One month cell: up to three thin bars (harvest, order window, arrival). Text equivalent goes to screen readers.
function Cell({ c, now }) {
  const said = [c.harvest && "harvest", c.order && "order window", c.arrive && "arrival"].filter(Boolean).join(", ");
  return (
    <td className={`m${now ? " now-col" : ""}`} aria-label={said || "nothing"}>
      <span className="cell" aria-hidden="true">
        <span className={`bar ${c.harvest ? "harvest" : "none"}`} />
        <span className={`bar ${c.order ? "order" : "none"}`} />
        <span className={`bar ${c.arrive ? "arrive" : "none"}`} />
      </span>
    </td>
  );
}

export default function Harvest() {
  const { data, error } = useData("/harvest");
  const [show, setShow] = useState("products");
  const rows = useMemo(() => {
    if (!data) return [];
    return show === "products" ? data.rows.filter((r) => r.products > 0) : data.rows;
  }, [data, show]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading what="Loading the harvest plan" />;
  if (!data.has_plan) {
    return (
      <Panel id="harvest-h" title="Harvest plan">
        <p className="note">No harvest plan in these outputs yet. Run the engine (python run.py) to build it.</p>
      </Panel>
    );
  }
  const today = data.today;
  const withProducts = data.rows.filter((r) => r.products > 0);
  const orderNow = withProducts.filter((r) => r.status === "ORDER NOW");
  const upcoming = withProducts.filter((r) => r.status === "upcoming");
  const unconfirmed = withProducts.filter((r) => !r.confirmed);
  const next = [...orderNow, ...upcoming].sort((a, b) => (a.order_by || "9999").localeCompare(b.order_by || "9999"))[0];

  return (
    <div data-ready="harvest">
      <section className="panel" aria-labelledby="harvest-sum">
        <header>
          <h2 id="harvest-sum">Annual harvest buys</h2>
          <p className="sub">Commodities bought once a year, after the crop comes in</p>
        </header>
        <div className="figures cols-4">
          <Figure k="Order window open now" v={orderNow.length} unit="commodities"
                  status={orderNow.length ? ["Order now", "st-red"] : ["None open", "st-green"]} />
          <Figure k="Windows coming up" v={upcoming.length} unit="commodities" />
          <Figure k="Next order deadline" v={next ? titleCase(next.commodity) : "None"}
                  d={next ? `${next.country}: order by ${niceDate(next.order_by, today)}` : "No open or upcoming windows."} />
          <Figure k="Need owner confirmation" v={unconfirmed.length} unit="calendar rows"
                  status={unconfirmed.length ? ["Confirm in Questions for you", "st-amber"] : ["All confirmed", "st-green"]} />
        </div>
      </section>

      <Panel id="harvest-tl" title="Next 12 months"
             sub="Bars: harvest, order window, arrival. The heavy rule marks the current month.">
        <Toolbar>
          <label>Show
            <select value={show} onChange={(e) => setShow(e.target.value)}>
              <option value="products">Commodities we sell</option>
              <option value="all">All calendar rows</option>
            </select>
          </label>
          <span className="count" role="status">{rows.length} rows</span>
        </Toolbar>
        <div className="legend" aria-hidden="true" style={{ marginBottom: 8 }}>
          <span className="key"><span className="sw box harvest" />Harvest</span>
          <span className="key"><span className="sw box order" />Order window</span>
          <span className="key"><span className="sw box arrive" />Arrival</span>
        </div>
        <Table id="harvest" caption="Harvest, order and arrival months per commodity and source country" stack={false} className="timeline">
          <thead>
            <tr>
              <th scope="col">Commodity</th>
              <th scope="col">Country</th>
              <th scope="col">Status</th>
              {data.months.map((m, i) => <th scope="col" key={m} className={`m${i === 0 ? " now-col" : ""}`}>{shortMonth(m)}</th>)}
              <th scope="col" className="num">Cases</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const [label, cls] = hstatus(r.status);
              return (
                <tr key={`${r.commodity}-${r.country}`}>
                  <th scope="row">
                    {titleCase(r.commodity)}
                    <div className="small muted">{r.products} product{r.products === 1 ? "" : "s"}</div>
                  </th>
                  <td>{r.country || "-"}</td>
                  <td>
                    <span className={`status-text ${cls}`}>{label}</span>
                    <div>{r.confirmed ? <Tag tone="green">confirmed</Tag> : <Tag tone="amber">unconfirmed</Tag>}</div>
                  </td>
                  {r.cells.map((c, i) => <Cell key={c.month} c={c} now={i === 0} />)}
                  <td className="num">{r.cases != null && r.cases !== "" ? cases(Number(r.cases), false) : "-"}</td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Panel>

      <Panel id="harvest-detail" title="Order windows in detail" sub="Dates from the harvest calendar and the 105 to 130 day shipping lead time">
        <Table id="harvest-d" caption="Order and arrival dates per commodity we sell">
          <thead>
            <tr>
              <th scope="col">Commodity</th><th scope="col">Country</th><th scope="col">Harvest</th>
              <th scope="col">Order from</th><th scope="col">Order by</th><th scope="col">Arrives</th>
              <th scope="col">Covers until</th><th scope="col">Source</th>
            </tr>
          </thead>
          <tbody>
            {withProducts.map((r) => (
              <tr key={`${r.commodity}-${r.country}-d`}>
                <th scope="row">{titleCase(r.commodity)}</th>
                <td data-label="Country">{r.country || "-"}</td>
                <td data-label="Harvest">{r.harvest_window || "-"}</td>
                <td data-label="Order from">{niceDate(r.order_window_start, today, { relative: false })}</td>
                <td data-label="Order by" className="strong">{niceDate(r.order_by, today)}</td>
                <td data-label="Arrives">{r.arrival_earliest ? `${niceDate(r.arrival_earliest, today, { relative: false })} to ${niceDate(r.arrival_latest, today, { relative: false })}` : "-"}</td>
                <td data-label="Covers until">{niceDate(r.covers_until, today, { relative: false })}
                  {(r.runs_out_before_arrival === "yes" || Number(r.runs_out_before_arrival) === 1) && <div className="small st-red">Runs out before arrival</div>}</td>
                <td data-label="Source"><Source url={r.source_url} /></td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Panel>
    </div>
  );
}
