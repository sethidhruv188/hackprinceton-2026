import { useData } from "../useData.js";
import { cases, niceDate, weight, orderBy } from "../format.js";
import { ContainerLine, downloadCSV, ErrorBox, GrossBanner, Loading, StatusBadge } from "../components.jsx";

const CSV_COLUMNS = [["product_id", "Item code"], ["name", "Product"], ["on_hand_cases", "On hand (cases)"],
  ["cases", "Cases to order"], ["kg", "kg"], ["cbm", "CBM"], ["cbm_source", "CBM source"], ["pulled_forward", "Pulled forward"],
  ["harvest", "Harvest buy"], ["on_hold", "On hold"], ["note", "Note"], ["reason", "Why"]];

function OrderTable({ order, today, brand, stockUsed }) {
  const lines = order.lines;
  const live = lines.filter((l) => !l.on_hold && !l.pulled_forward);
  const pulled = lines.filter((l) => l.pulled_forward);
  const held = lines.filter((l) => l.on_hold);
  const id = `t-${order.order_id.replace(/[^a-z0-9]/gi, "-")}`;
  const row = (l) => (
    <tr key={`${l.product_id}-${l.pulled_forward}`} className={l.on_hold ? "hold" : undefined}>
      <th scope="row"><a href={`#/product/${encodeURIComponent(l.product_id)}`}>{l.name}</a>
        <div className="muted">{l.product_id}
          {l.on_hold ? " · on hold: confirm release before ordering" : ""}
          {l.pulled_forward && <span className="tag pulled">pulled forward</span>}
          {l.harvest && <span className="tag harvest">annual harvest buy</span>}
          {l.floor_dip_date && !l.on_hold && <span className="tag pulled" style={{ background: "var(--late-bg)", color: "var(--late-fg)" }}>
            {l.floor_dip_date <= today ? "below 3 months of stock now" : `below 3 months of stock from ${niceDate(l.floor_dip_date, today, { relative: false })}`}: expedite or air-ship</span>}
        </div></th>
      {stockUsed && <td className="num" data-label="On hand">{l.on_hand_cases != null ? cases(l.on_hand_cases, false) : "-"}</td>}
      <td className="num" data-label="Cases to order">{l.on_hold ? <s>{cases(l.cases, false)}</s> : cases(l.cases, false)}</td>
      <td className="num" data-label="Weight">{weight(l.kg)}</td>
      {stockUsed && <td className="num" data-label="Volume">{l.cbm != null ? `${l.cbm.toFixed(2)} CBM` : "size unknown"}
        {l.cbm_source === "estimated from weight" && <span className="tag">estimated</span>}</td>}
      <td data-label="Why">{l.reason}</td>
    </tr>
  );
  return (
    <section className="order" aria-labelledby={`${id}-h`}>
      <div className="order-head">
        <div>
          <h2 id={`${id}-h`} style={{ margin: 0 }}>{order.kind === "harvest" ? order.covers
            : order.covers.startsWith("arrives") ? `Order ${order.covers.replace("arrives", "arriving")}` : `Covers ${order.covers}`}</h2>
          <StatusBadge status={order.status} />
        </div>
        <div className="when">
          <strong>{order.kind === "harvest" ? `Order between ${niceDate(order.order_by_safe, today)} and ${niceDate(order.order_by_latest, today)}` : orderBy(order, today)}</strong>
          {order.kind !== "harvest" && (order.order_by_safe < today
            ? <div className="muted">Safe date {niceDate(order.order_by_safe, today, { relative: false })} has passed.</div>
            : <div className="muted">Latest possible: {niceDate(order.order_by_latest, today)}</div>)}
        </div>
        <div>
          <strong>{cases(order.cases)}</strong> · {weight(order.kg)}
          {order.cbm != null && <div><ContainerLine o={order} /></div>}
          <div className="muted">{order.products} products{held.length ? `; ${held.length} on hold not counted` : ""}</div>
        </div>
        <button type="button" onClick={() => downloadCSV(`${brand}-${order.first_month}.csv`, lines, CSV_COLUMNS)}>Export to CSV</button>
      </div>
      <p className="muted">{order.reason}</p>
      {pulled.length > 0 && (
        <p className="banner">The last container was under 70% full, so {pulled.length} product(s) ({cases(order.pulled_forward_cases)})
          were pulled forward from later orders to fill it. Nothing that is needed was cut.</p>
      )}
      <div className="table-wrapper" tabIndex={0} role="region" aria-labelledby={`${id}-c`}>
        <table>
          <caption id={`${id}-c`}>{brand}: {order.kind === "harvest" ? "annual harvest order" : order.covers.startsWith("arrives") ? `order ${order.covers.replace("arrives", "arriving")}` : `order covering ${order.covers}`}</caption>
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
  const { data, error, loading } = useData(`/brands/${encodeURIComponent(name)}`);
  if (error) return <ErrorBox error={error} />;
  if (loading) return <Loading />;
  const today = data.today;
  const upcoming = data.orders.filter((o) => o.status !== "Too late for full lead time: expedite");
  const late = data.orders.filter((o) => o.status === "Too late for full lead time: expedite");
  const info = data.info || {};
  return (
    <div data-ready="brand">
      <p><a href="#/">← All brands</a></p>
      <h1>{data.brand}</h1>
      {data.gross_need ? <GrossBanner text={data.gross_need_note} /> : <p className={`banner${data.on_order_missing ? " warn" : ""}`}>{data.stock_note}</p>}
      {info.on_hand_cases != null && (
        <div className="stats">
          <div className="stat"><div className="big">{cases(info.on_hand_cases, false)}</div>cases in the warehouse</div>
          <div className="stat"><div className="big">{info.months_of_cover ?? "-"}</div>months of sales on hand (last 6 months' pace)</div>
          <div className="stat"><div className="big">{cases(info.monthly_cases, false)}</div>cases sold in a typical month (last 12 months)</div>
        </div>
      )}
      {upcoming.length > 0 && <OrderTable order={upcoming[0]} today={today} brand={data.brand} stockUsed={data.stock_used} />}
      {upcoming.length > 1 && (
        <details>
          <summary><strong>Later orders ({upcoming.length - 1})</strong></summary>
          {upcoming.slice(1).map((o) => <OrderTable key={o.order_id} order={o} today={today} brand={data.brand} stockUsed={data.stock_used} />)}
        </details>
      )}
      {late.length > 0 && (
        <details>
          <summary><strong>Too late for the full shipping time ({late.length})</strong>: these arrive before a normal order could. Expedite or expect a shortfall.</summary>
          {late.map((o) => <OrderTable key={o.order_id} order={o} today={today} brand={data.brand} stockUsed={data.stock_used} />)}
        </details>
      )}
    </div>
  );
}
