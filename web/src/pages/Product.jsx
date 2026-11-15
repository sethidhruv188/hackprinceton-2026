import { Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useData } from "../useData.js";
import { useSettings } from "../settings.jsx";
import { cases, monthLabel, niceDate, shortMonth, weight } from "../format.js";
import { Banner, ErrorBox, Figure, GrossBanner, Loading, Panel, Table, Tag } from "../components.jsx";

const INK = "#1d1d1f", MUTED = "#5d6066", LINE = "#dcdde1", BAND = "#e4e5e8", HOLD = "#f1f1f3", RED = "#a0251f", AMBER = "#855400";
const CAUSE = {
  post_gap_rebound: "bounce-back after it couldn't be sold", code_switch: "item code change",
  diwali: "before Diwali", other_festival: "near another festival (possible link)",
  recurring_seasonal: "happens every year this month", one_off: "one-off (promotion or bulk order?)",
};
const axis = { stroke: LINE, tick: { fill: MUTED, fontSize: 12 }, tickLine: false };

function stockAction(st, today) {
  if (st.status.startsWith("on hold")) return ["On hold: confirm release", "st-amber"];
  if (st.status.includes("annual harvest")) return ["Annual harvest buy", "st-grey"];
  if (st.order_now_cases > 0) return [`Order now: ${cases(st.order_now_cases)}`, "st-red"];
  if (st.ok_until) return [`OK until ${niceDate(st.ok_until, today)}`, "st-green"];
  return ["OK for more than a year", "st-green"];
}

const times = (r) => (r == null || !Number.isFinite(r) ? "usually almost nothing that month" : `${r}x its usual`);

function prevMonth(ym) {
  const [y, m] = ym.split("-").map(Number);
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`;
}

function SalesTip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  const r = payload[0].payload;
  return (
    <div className="tip">
      <strong>{monthLabel(label)}</strong>
      {r.sold != null && <div>Sold: {cases(r.sold)}</div>}
      {r.held && <div>On hold: couldn't be sold</div>}
      {r.notListed && <div>Not listed yet</div>}
      {r.likely != null && <div>Likely: {cases(r.likely)}</div>}
      {r.high != null && <div>High estimate: {cases(r.high)}</div>}
      {r.spike && <div>Spike: {cases(r.spike.cases)}, {times(r.spike.ratio)} ({CAUSE[r.spike.cause] || r.spike.cause})</div>}
      {r.diwali && <div>Diwali: {niceDate(r.diwali, null, { relative: false })}</div>}
    </div>
  );
}

export default function Product({ id }) {
  const { data, error } = useData(`/products/${encodeURIComponent(id)}`);
  const { settings } = useSettings();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const today = settings?.as_of || new Date().toISOString().slice(0, 10);
  const p = data.product;
  const spikes = Object.fromEntries(data.spikes.map((s) => [s.month, s]));
  const diwaliByMonth = Object.fromEntries(data.diwali_dates.map((d) => [d.slice(0, 7), d]));
  const rows = data.history.map((h) => ({
    month: h.month, sold: h.state === "sold" ? h.cases : null, held: h.state === "on hold", notListed: h.state === "not listed yet",
    spike: spikes[h.month] || null, spikeDot: spikes[h.month] ? spikes[h.month].cases : null, diwali: diwaliByMonth[h.month] || null,
  }));
  // join the forecast to the last sales month so the dashed line starts where the solid one ends
  if (rows.length && data.forecast.length) {
    const last = rows[rows.length - 1];
    if (last.sold != null) { last.likely = last.sold; last.band = [last.sold, last.sold]; }
  }
  data.forecast.forEach((f) => rows.push({ month: f.month, likely: f.likely, high: f.high, band: [f.likely, f.high], diwali: diwaliByMonth[f.month] || null }));
  const lastHist = data.history.length ? data.history[data.history.length - 1].month : null;
  const prof = data.profile;
  const reorder = data.reorder;
  const st = data.stock;

  return (
    <div data-ready="product">
      {data.brand ? <a className="back" href={`#/brand/${encodeURIComponent(data.brand)}`}>{data.brand}</a> : <a className="back" href="#/products">All products</a>}
      <div className="page-head">
        <h2>{p.name}</h2>
        <span className="muted small">{p.product_id}{p.category ? `, ${p.category.replace(/_/g, " ")}` : ""}{p.case_weight_kg ? `, one case is about ${weight(p.case_weight_kg)}` : ""}</span>
      </div>
      {data.on_hold_now && <Banner tone="warn">This product is on hold. Confirm it is released before ordering.</Banner>}
      {data.inactive && <Banner>No sales in the last 6 months, so it is not in the order plan.</Banner>}

      <Panel id="sales-h" title="Monthly sales and forecast" sub="Cases per month; forecast for the next 12 months">
        <div className="chart" role="img" aria-label={`Monthly sales of ${p.name} and the forecast for the next 12 months`}>
          <ResponsiveContainer width="100%" height={320}>
            <ComposedChart data={rows} margin={{ top: 16, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={LINE} vertical={false} />
              <XAxis dataKey="month" tickFormatter={shortMonth} interval="preserveStartEnd" minTickGap={18} {...axis} />
              <YAxis width={64} tickFormatter={(v) => v.toLocaleString("en-US")} {...axis} axisLine={false} />
              <Tooltip content={<SalesTip />} cursor={{ stroke: LINE }} />
              {data.hold_months.map((m, i) => (
                <ReferenceArea key={m} x1={m} x2={m} fill={HOLD} fillOpacity={1} stroke="none" ifOverflow="extendDomain"
                  label={i === 0 || data.hold_months[i - 1] !== prevMonth(m) ? { value: "on hold", position: "insideTop", fill: MUTED, fontSize: 11 } : undefined} />
              ))}
              <Area dataKey="band" stroke="none" fill={BAND} fillOpacity={1} isAnimationActive={false} activeDot={false} />
              <Line dataKey="sold" stroke={INK} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
              <Line dataKey="likely" stroke={INK} strokeWidth={2} strokeDasharray="6 4" dot={false} isAnimationActive={false} />
              <Line dataKey="spikeDot" stroke="none" isAnimationActive={false} legendType="none"
                dot={{ r: 4, fill: "#fff", stroke: RED, strokeWidth: 2 }} activeDot={false} />
              {data.diwali_dates.map((d) => (
                <ReferenceLine key={d} x={d.slice(0, 7)} stroke={MUTED} strokeDasharray="2 3" label={{ value: "Diwali", position: "top", fill: MUTED, fontSize: 11 }} />
              ))}
              {lastHist && <ReferenceLine x={lastHist} stroke={LINE} />}
            </ComposedChart>
          </ResponsiveContainer>
          <div className="legend">
            <span className="key"><span className="sw" />Cases sold</span>
            <span className="key"><span className="sw dash" />Likely (forecast)</span>
            <span className="key"><span className="sw box" style={{ background: BAND }} />Up to the high estimate</span>
            {data.hold_months.length > 0 && <span className="key"><span className="sw box" style={{ background: HOLD, outline: `1px solid ${LINE}` }} />On hold</span>}
            {data.spikes.length > 0 && <span className="key"><span className="sw box" style={{ width: 8, height: 8, border: `2px solid ${RED}`, background: "#fff" }} />Spike</span>}
            <span className="key"><span className="sw dot" style={{ borderColor: MUTED }} />Diwali</span>
          </div>
        </div>
      </Panel>

      {data.harvest && (
        <Banner><Tag>{data.harvest.label}</Tag>{" "}Next order {niceDate(data.harvest.order_from, today)} to {niceDate(data.harvest.order_by, today)}:
          {" "}{cases(data.harvest.order_cases)}, arriving about {monthLabel(data.harvest.expected_arrival.slice(0, 7))} and lasting until
          {" "}{monthLabel(data.harvest.covers_until.slice(0, 7))} (stock expected at arrival: {cases(data.harvest.projected_stock_at_arrival)}).
          {data.harvest.extended_months && <span className="muted"> Months after the 12-month forecast reuse last year's.</span>}</Banner>
      )}

      {st && (
        <Panel id="stock-h" title="Stock" sub={data.on_order_missing ? "Stock on order is not known" : ""}>
          {data.on_order_missing && <Banner tone="warn">{data.stock_note}</Banner>}
          <div className="figures cols-4" style={{ marginBottom: 16 }}>
            <Figure k="On hand" v={cases(st.on_hand_cases, false)} unit="cases" />
            <Figure k="On order" v={data.on_order_missing ? "not known" : cases(st.on_order_cases, false)} unit={data.on_order_missing ? "" : "cases"} />
            <Figure k="3-month floor" v={cases(st.floor_cases, false)} unit="cases" d="The next 3 months of sales: the least to keep on hand"
              status={st.floor_dip_date ? [st.floor_dip_date <= today ? "Below it now: expedite or air-ship" : `Below it from ${niceDate(st.floor_dip_date, today, { relative: false })}`, "st-red"] : null} />
            <Figure k="What to do" v={<span className={`status-text ${stockAction(st, today)[1]}`} style={{ fontSize: 16 }}>{stockAction(st, today)[0]}</span>}
              d={`Reorder point ${cases(st.reorder_point)}`} />
          </div>
          {data.projection.length > 0 && !data.harvest && (
            <div className="chart" role="img" aria-label="Projected stock for the next 12 months against the reorder point and the 3-month floor">
              <ResponsiveContainer width="100%" height={240}>
                <ComposedChart data={data.projection} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={LINE} vertical={false} />
                  <XAxis dataKey="month" tickFormatter={shortMonth} {...axis} />
                  <YAxis width={64} tickFormatter={(v) => v.toLocaleString("en-US")} {...axis} axisLine={false} />
                  <Tooltip labelFormatter={monthLabel} formatter={(v, n) => [cases(v), n]} contentStyle={{ border: `1px solid ${LINE}`, borderRadius: 0, fontSize: 13 }} />
                  <ReferenceLine y={0} stroke={MUTED} />
                  <Line dataKey="projected_stock" name="Stock if nothing is ordered" stroke={MUTED} strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Line dataKey="projected_with_suggested_order" name="With the suggested order" stroke={INK} strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Line dataKey="reorder_point" name="Reorder point" stroke={AMBER} strokeDasharray="6 4" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                  <Line dataKey="floor_3_months" name="3-month floor" stroke={RED} strokeDasharray="2 3" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>
              <div className="legend">
                <span className="key"><span className="sw" style={{ borderColor: MUTED }} />Stock if nothing is ordered</span>
                <span className="key"><span className="sw" />With the suggested order (arrives after 130 days)</span>
                <span className="key"><span className="sw dash" style={{ borderColor: AMBER }} />Reorder point</span>
                <span className="key"><span className="sw dot" style={{ borderColor: RED }} />3-month floor</span>
              </div>
            </div>
          )}
        </Panel>
      )}

      <Panel id="reorder-h" title="When to reorder">
        {reorder ? (
          <p className="lead">As of {monthLabel(reorder.month)}: reorder when stock plus what is already on order falls below
            {" "}<strong>{cases(reorder.reorder_point)}</strong>. <span className="muted">{reorder.reason}</span></p>
        ) : <p className="note">Not in the current plan.</p>}
        {data.gross_need && <GrossBanner text={data.gross_need_note} />}
      </Panel>

      {(prof || data.spikes.length > 0) && (
        <Panel id="pattern-h" title="How it usually sells">
          {prof && (
            <dl className="kv">
              <dt>Months with sales</dt><dd>{prof.months_sold} of {prof.of_months}</dd>
              <dt>Typical month</dt><dd>{cases(prof.typical_monthly_cases)}</dd>
              {prof.high_months && <><dt>Busier months</dt><dd>{prof.high_months}</dd></>}
              {prof.low_months && <><dt>Quieter months</dt><dd>{prof.low_months}</dd></>}
              {prof.diwali_lift_pre_month != null && <><dt>Around Diwali</dt><dd>month before: about {prof.diwali_lift_pre_month}x a normal month; Diwali month: about {prof.diwali_lift_diwali_month ?? "-"}x</dd></>}
              {prof.one_off_spikes > 0 && <><dt>One-off spikes</dt><dd>{prof.one_off_spikes}, {prof.one_off_share_of_volume_pct ?? prof["one_off_share_of_volume_%"]}% of its sales</dd></>}
            </dl>
          )}
          {data.spikes.length > 0 && (
            <Table id="spikes" caption="Spikes and likely reasons">
              <thead><tr><th scope="col">Month</th><th scope="col" className="num">Cases</th><th scope="col">Size</th><th scope="col">Likely reason</th><th scope="col">How often</th></tr></thead>
              <tbody>{data.spikes.map((s) => (
                <tr key={s.month}><th scope="row">{monthLabel(s.month)}</th><td className="num" data-label="Cases">{cases(s.cases, false)}</td>
                  <td data-label="Size">{times(s.ratio)}</td><td data-label="Likely reason">{CAUSE[s.cause] || s.cause}</td><td data-label="How often">{s.recurrence}</td></tr>
              ))}</tbody>
            </Table>
          )}
        </Panel>
      )}

      {data.plan.length > 0 && (
        <Panel id="plan-h" title="Next 12 months" sub="Order dates assume up to 130 days to arrive">
          <Table id="plan" caption="Cases needed per month and when to order them">
            <thead><tr><th scope="col">Arrives in</th><th scope="col" className="num">Cases</th><th scope="col">Order by</th><th scope="col">Why</th></tr></thead>
            <tbody>
              {data.plan.map((r) => (
                <tr key={r.month}>
                  <th scope="row">{monthLabel(r.month)}</th>
                  <td className="num" data-label="Cases">{cases(r.cases_needed, false)}</td>
                  <td data-label="Order by">{niceDate(r.order_by_safe, today)}</td>
                  <td data-label="Why" className="small">{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Panel>
      )}
    </div>
  );
}
