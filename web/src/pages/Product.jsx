import { Area, Bar, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useData } from "../useData.js";
import { cases, monthLabel, niceDate, shortMonth, weight } from "../format.js";
import { ErrorBox, GrossBanner, Loading } from "../components.jsx";

const CAUSE = {
  post_gap_rebound: "bounce-back after it couldn't be sold", code_switch: "item code change",
  diwali: "before Diwali", other_festival: "near another festival (possible link)",
  recurring_seasonal: "happens every year this month", one_off: "one-off (promotion or bulk order?)",
};

function stockAction(st, today) {
  if (st.status.startsWith("on hold")) return "On hold: confirm release";
  if (st.status.includes("annual harvest")) return "Annual harvest buy";
  if (st.order_now_cases > 0) return `Order now: ${cases(st.order_now_cases)}`;
  if (st.ok_until) return `OK until ${niceDate(st.ok_until, today)}`;
  return "OK for more than a year";
}

function times(r) {
  return r == null || !Number.isFinite(r) ? "usually almost nothing that month" : `${r}x its usual`;
}

function prevMonth(ym) {
  const [y, m] = ym.split("-").map(Number);
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`;
}

function TipBox({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  const r = payload[0].payload;
  return (
    <div className="chart" style={{ padding: "0.5rem 0.75rem", fontSize: "0.9rem" }}>
      <strong>{monthLabel(label)}</strong>
      {r.sold != null && <div>Sold: {cases(r.sold)}</div>}
      {r.held && <div>On hold: couldn't be sold</div>}
      {r.notListed && <div>Not listed yet</div>}
      {r.likely != null && <div>Likely: {cases(r.likely)}</div>}
      {r.high != null && <div>Could be as high as: {cases(r.high)}</div>}
      {r.spike && <div>Spike: {cases(r.spike.cases)}, {times(r.spike.ratio)} ({CAUSE[r.spike.cause] || r.spike.cause}). {r.spike.recurrence}.</div>}
      {r.diwali && <div>Diwali: {r.diwali}</div>}
    </div>
  );
}

export default function Product({ id }) {
  const { data, error, loading } = useData(`/products/${encodeURIComponent(id)}`);
  if (error) return <ErrorBox error={error} />;
  if (loading) return <Loading />;
  const p = data.product;
  const spikes = Object.fromEntries(data.spikes.map((s) => [s.month, s]));
  const diwaliByMonth = Object.fromEntries(data.diwali_dates.map((d) => [d.slice(0, 7), d]));
  const rows = data.history.map((h) => ({
    month: h.month, sold: h.state === "sold" ? h.cases : null, held: h.state === "on hold", notListed: h.state === "not listed yet",
    spike: spikes[h.month] || null, spikeDot: spikes[h.month] ? spikes[h.month].cases : null, diwali: diwaliByMonth[h.month] || null,
  }));
  data.forecast.forEach((f) => rows.push({ month: f.month, likely: f.likely, high: f.high, band: [f.likely, f.high], diwali: diwaliByMonth[f.month] || null }));
  const lastHist = data.history.length ? data.history[data.history.length - 1].month : null;
  const today = new Date().toISOString().slice(0, 10);
  const prof = data.profile;
  const reorder = data.reorder;
  return (
    <div data-ready="product">
      <p>{data.brand ? <a href={`#/brand/${encodeURIComponent(data.brand)}`}>← {data.brand}</a> : <a href="#/">← All brands</a>}</p>
      <h1>{p.name}</h1>
      <p className="muted">{p.product_id} · {p.category?.replace("_", " ")}{p.case_weight_kg ? ` · one case is about ${weight(p.case_weight_kg)}` : ""}</p>
      {data.on_hold_now && <p className="banner">This product is on hold. Confirm it is released before ordering.</p>}
      {data.inactive && <p className="banner">No sales in the last 6 months, so it is not in the order plan.</p>}

      <div className="chart" role="img" aria-label={`Monthly sales of ${p.name} and the forecast for the next 12 months`}>
        <ResponsiveContainer width="100%" height={340}>
          <ComposedChart data={rows} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="month" tickFormatter={shortMonth} interval="preserveStartEnd" minTickGap={18} stroke="var(--muted)" />
            <YAxis stroke="var(--muted)" width={68} tickFormatter={(v) => v.toLocaleString("en-US")} />
            <Tooltip content={<TipBox />} />
            {data.hold_months.map((m, i) => (
              <ReferenceArea key={m} x1={m} x2={m} fill="var(--hold-bg)" fillOpacity={1} stroke="var(--line)" ifOverflow="extendDomain"
                label={i === 0 || data.hold_months[i - 1] !== prevMonth(m) ? { value: "on hold", position: "insideTop", fill: "var(--muted)", fontSize: 12 } : undefined} />
            ))}
            <Bar dataKey="sold" fill="var(--coming-fg)" isAnimationActive={false} />
            <Area dataKey="band" stroke="none" fill="var(--month-fg)" fillOpacity={0.25} isAnimationActive={false} />
            <Line dataKey="likely" stroke="var(--now-fg)" strokeWidth={3} dot={false} isAnimationActive={false} />
            <Line dataKey="spikeDot" stroke="none" isAnimationActive={false} legendType="none"
                  dot={{ r: 6, fill: "var(--late-fg)", stroke: "var(--card)", strokeWidth: 2 }} activeDot={false} />
            {data.diwali_dates.map((d) => (
              <ReferenceLine key={d} x={d.slice(0, 7)} stroke="var(--month-fg)" strokeDasharray="5 4" label={{ value: "Diwali", position: "top", fill: "var(--muted)", fontSize: 12 }} />
            ))}
            {lastHist && <ReferenceLine x={lastHist} stroke="var(--muted)" label={{ value: "last sales month", position: "insideTopRight", fill: "var(--muted)", fontSize: 12 }} />}
          </ComposedChart>
        </ResponsiveContainer>
        <div className="legend">
          <span><span className="swatch" style={{ background: "var(--coming-fg)" }} />Cases sold</span>
          <span><span className="swatch" style={{ background: "var(--hold-bg)", border: "1px solid var(--line)" }} />On hold (couldn't be sold)</span>
          <span><span className="swatch" style={{ background: "var(--now-fg)" }} />Likely sales</span>
          <span><span className="swatch" style={{ background: "var(--month-fg)", opacity: 0.4 }} />Could be as high as</span>
          <span><span className="swatch" style={{ background: "var(--late-fg)", borderRadius: "50%" }} />Spike (hover for the reason)</span>
          <span>- - - Diwali</span>
        </div>
      </div>

      {data.harvest && <p className="banner"><span className="tag harvest">{data.harvest.label}</span>{" "}
        Next order {niceDate(data.harvest.order_from, today)} to {niceDate(data.harvest.order_by, today)}: {cases(data.harvest.order_cases)},
        arriving about {monthLabel(data.harvest.expected_arrival.slice(0, 7))} and lasting until {monthLabel(data.harvest.covers_until.slice(0, 7))}
        {" "}(stock expected at arrival: {cases(data.harvest.projected_stock_at_arrival)}).
        {data.harvest.extended_months && <span className="muted"> Months after the 12-month forecast ({data.harvest.extended_months}) reuse last year's.</span>}</p>}

      {data.stock && (
        <>
          <h2>Stock</h2>
          {data.on_order_missing && <p className="banner warn">{data.stock_note}</p>}
          <div className="stats">
            <div className="stat"><div className="big">{cases(data.stock.on_hand_cases, false)}</div>cases on hand</div>
            <div className="stat"><div className="big">{data.on_order_missing ? "not known" : cases(data.stock.on_order_cases, false)}</div>cases on order</div>
            <div className="stat"><div className="big">{cases(data.stock.floor_cases, false)}</div>cases = the next 3 months of sales, the least to keep on hand
              {data.stock.floor_dip_date && <div className="floor-warn" style={{ marginTop: "0.4rem" }}>{data.stock.floor_dip_date <= today ? "Below it now" : `Below it from ${niceDate(data.stock.floor_dip_date, today)}`}, before a new order can arrive: expedite or air-ship.</div>}</div>
            <div className="stat"><div className="big" style={{ fontSize: "1.3rem" }}>{stockAction(data.stock, today)}</div>
              reorder point {cases(data.stock.reorder_point)}; order tops up to {cases(data.stock.order_up_to)}</div>
          </div>
          {data.projection.length > 0 && !data.harvest && (
            <div className="chart" role="img" aria-label="Projected stock for the next 12 months against the reorder point">
              <ResponsiveContainer width="100%" height={260}>
                <ComposedChart data={data.projection} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="var(--line)" vertical={false} />
                  <XAxis dataKey="month" tickFormatter={shortMonth} stroke="var(--muted)" />
                  <YAxis stroke="var(--muted)" width={68} tickFormatter={(v) => v.toLocaleString("en-US")} />
                  <Tooltip labelFormatter={monthLabel} formatter={(v, n) => [cases(v), n]}
                    contentStyle={{ background: "var(--card)", border: "1px solid var(--line)" }} />
                  <ReferenceLine y={0} stroke="var(--muted)" />
                  <Line dataKey="projected_stock" name="Stock if nothing is ordered" stroke="var(--now-fg)" strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Line dataKey="projected_with_suggested_order" name="With the suggested order (arrives after 130 days)" stroke="var(--coming-fg)" strokeWidth={3} dot={false} isAnimationActive={false} />
                  <Line dataKey="reorder_point" name="Reorder point" stroke="var(--month-fg)" strokeDasharray="6 4" strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Line dataKey="floor_3_months" name="3-month floor (next 3 months of sales)" stroke="var(--late-fg)" strokeDasharray="2 3" strokeWidth={2} dot={false} isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>
              <div className="legend">
                <span><span className="swatch" style={{ background: "var(--now-fg)" }} />Stock if nothing is ordered</span>
                <span><span className="swatch" style={{ background: "var(--coming-fg)" }} />With the suggested order</span>
                <span>- - - Reorder point</span>
                <span style={{ color: "var(--late-fg)" }}>···· 3-month floor: keeps 3 months of stock on hand</span>
              </div>
            </div>
          )}
        </>
      )}

      <h2>When to reorder</h2>
      {reorder ? (
        <p>As of {monthLabel(reorder.month)}: reorder when stock plus what's already on order falls below
          {" "}<strong>{cases(reorder.reorder_point)}</strong>, and order enough to bring it up to <strong>{cases(reorder.order_up_to)}</strong>.
          <br /><span className="muted">{reorder.reason}</span></p>
      ) : <p className="muted">Not in the current plan.</p>}
      {data.gross_need && <GrossBanner text={data.gross_need_note} />}

      {prof && (
        <>
          <h2>How it usually sells</h2>
          <ul>
            <li>Sold in {prof.months_sold} of {prof.of_months} months; a typical month is {cases(prof.typical_monthly_cases)}.</li>
            {prof.high_months && <li>Busier months: {prof.high_months}.</li>}
            {prof.low_months && <li>Quieter months: {prof.low_months}.</li>}
            {prof.diwali_lift_pre_month != null && <li>Month before Diwali: about {prof.diwali_lift_pre_month}x a normal month. Diwali month: about {prof.diwali_lift_diwali_month ?? "-"}x.</li>}
            {prof.one_off_spikes > 0 && <li>{prof.one_off_spikes} one-off spike(s), {prof.one_off_share_of_volume_pct ?? prof["one_off_share_of_volume_%"]}% of its sales.</li>}
          </ul>
        </>
      )}

      {data.spikes.length > 0 && (
        <>
          <h2>Spikes and likely reasons</h2>
          <ul>{data.spikes.map((s) => <li key={s.month}>{monthLabel(s.month)}: {cases(s.cases)} ({times(s.ratio)}): {CAUSE[s.cause] || s.cause}. {s.recurrence}.</li>)}</ul>
        </>
      )}

      {data.plan.length > 0 && (
        <>
          <h2>Next 12 months</h2>
          <div className="table-wrapper" tabIndex={0} role="region" aria-labelledby="plan-cap">
            <table>
              <caption id="plan-cap">Cases needed per month (order dates assume up to 130 days to arrive)</caption>
              <thead><tr><th scope="col">Arrives in</th><th scope="col" className="num">Cases</th><th scope="col">Order by</th><th scope="col">Why</th></tr></thead>
              <tbody>
                {data.plan.map((r) => (
                  <tr key={r.month}>
                    <th scope="row">{monthLabel(r.month)}</th>
                    <td className="num" data-label="Cases">{cases(r.cases_needed, false)}</td>
                    <td data-label="Order by">{niceDate(r.order_by_safe, today)}</td>
                    <td data-label="Why">{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
