import { useData } from "../useData.js";
import { cases, niceDate, weight, orderBy } from "../format.js";
import { Banner, ContainerLine, downloadCSV, ErrorBox, Figure, GrossBanner, Loading, StatusText, Tag } from "../components.jsx";

const LATE = "Too late for full lead time: expedite";
const CSV_COLUMNS = [["product_id", "Item code"], ["name", "Product"], ["on_hand_cases", "On hand (cases)"],
  ["cases", "Cases to order"], ["kg", "kg"], ["cbm", "CBM"], ["cbm_source", "CBM source"], ["pulled_forward", "Pulled forward"],
  ["harvest", "Harvest buy"], ["on_hold", "On hold"], ["owner_adjustment", "Owner adjustment"], ["note", "Note"], ["reason", "Why"]];

function title(order) {
  if (order.kind === "harvest") return order.covers;
  return order.covers.startsWith("arrives") ? `Order ${order.covers.replace("arrives", "arriving")}` : `Covers ${order.covers}`;
}

function OrderBlock({ order, today, brand, stockUsed }) {
  const lines = order.lines;
  const live = lines.filter((l) => !l.on_hold && !l.pulled_forward);
  const pulled = lines.filter((l) => l.pulled_forward);
  const held = lines.filter((l) => l.on_hold);
  const id = `o-${order.order_id.replace(/[^a-z0-9]/gi, "-")}`;
  const row = (l) => (
    <tr key={`${l.product_id}-${l.pulled_forward}`} className={l.on_hold ? "hold" : undefined}>
      <th scope="row">
        <a href={`#/product/${encodeURIComponent(l.product_id)}`}>{l.name}</a>
        <div className="small muted">{l.product_id}
          {l.pulled_forward && <Tag>pulled forward</Tag>}
          {l.harvest && <Tag>annual harvest buy</Tag>}
          {l.on_hold && <Tag tone="amber">on hold: confirm release</Tag>}
        </div>
        {l.owner_adjustment && <div className="small"><span className="tag amber">owner adjustment</span> {l.owner_adjustment}</div>}
        {l.runs_out_before_arrival && !l.on_hold && <div className="small st-red">{l.note}</div>}
        {l.floor_dip_date && !l.on_hold && (
          <div className="small st-red">{l.floor_dip_date <= today ? "Below 3 months of stock now" : `Below 3 months of stock from ${niceDate(l.floor_dip_date, today, { relative: false })}`}: expedite or air-ship</div>)}
      </th>
      {stockUsed && <td className="num" data-label="On hand">{l.on_hand_cases != null ? cases(l.on_hand_cases, false) : "-"}</td>}
      <td className="num" data-label="Cases to order">{l.on_hold ? <s>{cases(l.cases, false)}</s> : cases(l.cases, false)}</td>
      <td className="num" data-label="Weight">{weight(l.kg)}</td>
      {stockUsed && <td className="num" data-label="Volume">{l.cbm != null ? `${l.cbm.toFixed(2)} CBM` : "size unknown"}
        {l.cbm_source === "estimated from weight" && <Tag>estimated</Tag>}</td>}
      <td data-label="Why" className="small">{l.reason}</td>
    </tr>
  );
  const harvest = order.kind === "harvest";
  return (
    <section className="order-block" aria-labelledby={`${id}-h`}>
      <div className="order-head">
        <div>
          <h3 id={`${id}-h`}>{title(order)}</h3>
          <StatusText status={order.status} />
        </div>
        <div className="when">
          <strong>{harvest ? `Order between ${niceDate(order.order_by_safe, today)} and ${niceDate(order.order_by_latest, today)}` : orderBy(order, today)}</strong>
          {!harvest && (order.order_by_safe < today
            ? <div className="small muted">The safe date, {niceDate(order.order_by_safe, today, { relative: false })}, has passed.</div>
            : <div className="small muted">Latest possible: {niceDate(order.order_by_latest, today)}</div>)}
          {order.next_order_by && <div className="small muted">Next order by {niceDate(order.next_order_by, today)}</div>}
          {order.runs_out_before_arrival > 0 && <div className="small st-red">{order.runs_out_before_arrival} product(s) run out before this order arrives</div>}
        </div>
        <div>
          <strong>{cases(order.cases)}</strong>, {weight(order.kg)}
          {order.cbm != null && <div className="small"><ContainerLine o={order} /></div>}
          <div className="small muted">{order.products} products{held.length ? `; ${held.length} on hold, not counted` : ""}</div>
        </div>
        <button className="btn" type="button" onClick={() => downloadCSV(`${brand}-${order.first_month}.csv`, lines, CSV_COLUMNS)}>Export CSV</button>
      </div>
      {order.reason && <p className="note" style={{ marginBottom: 16 }}>{order.reason}</p>}
      {pulled.length > 0 && (
        <Banner>The last container was under 70% full, so {pulled.length} product(s) ({cases(order.pulled_forward_cases)}) were pulled forward
          from later orders to fill it. Nothing that is needed was cut.</Banner>
      )}
      <div className="tbl-wrap" tabIndex={0} role="region" aria-labelledby={`${id}-c`}>
        <table className="tbl stack">
          <caption id={`${id}-c`}>{brand}: {harvest ? "annual harvest order" : title(order).toLowerCase()}, product by product</caption>
          <thead>
            <tr><th scope="col">Product</th>{stockUsed && <th scope="col" className="num">On hand</th>}
              <th scope="col" className="num">Cases to order</th><th scope="col" className="num">Weight</th>
              {stockUsed && <th scope="col" className="num">Volume</th>}<th scope="col">Why</th></tr>
          </thead>
          <tbody>{[...live, ...pulled, ...held].map(row)}</tbody>
        </table>
      </div>
    </section>
  );
}

export default function Brand({ name }) {
  const { data, error } = useData(`/brands/${encodeURIComponent(name)}`);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const today = data.today;
  const upcoming = data.orders.filter((o) => o.status !== LATE);
  const late = data.orders.filter((o) => o.status === LATE);
  const info = data.info || {};
  return (
    <div data-ready="brand">
      <a className="back" href="#/brands">All brands</a>
      {data.gross_need ? <GrossBanner text={data.gross_need_note} /> : <Banner tone={data.on_order_missing ? "warn" : ""}>{data.stock_note}</Banner>}
      {info.on_hand_cases != null && (
        <div className="figures cols-3" style={{ marginBottom: 32 }}>
          <Figure k="In the warehouse" v={cases(info.on_hand_cases, false)} unit="cases" />
          <Figure k="Months of sales on hand" v={info.months_of_cover ?? "-"} unit="months" d="At the last 6 months' pace" />
          <Figure k="Sold in a typical month" v={cases(info.monthly_cases, false)} unit="cases" d="Last 12 months" />
        </div>
      )}
      <section className="panel" aria-labelledby="next-h">
        <header><h2 id="next-h">Next order</h2><p className="sub">{upcoming.length} upcoming, {late.length} too late</p></header>
        {upcoming.length > 0 ? <OrderBlock order={upcoming[0]} today={today} brand={data.brand} stockUsed={data.stock_used} />
          : <p className="note">No upcoming orders in the 12-month plan.</p>}
        {upcoming.length > 1 && (
          <details className="more">
            <summary>Later orders ({upcoming.length - 1})</summary>
            {upcoming.slice(1).map((o) => <OrderBlock key={o.order_id} order={o} today={today} brand={data.brand} stockUsed={data.stock_used} />)}
          </details>
        )}
        {late.length > 0 && (
          <details className="more">
            <summary>Too late for the full shipping time ({late.length}): expedite or expect a shortfall</summary>
            {late.map((o) => <OrderBlock key={o.order_id} order={o} today={today} brand={data.brand} stockUsed={data.stock_used} />)}
          </details>
        )}
      </section>
    </div>
  );
}
