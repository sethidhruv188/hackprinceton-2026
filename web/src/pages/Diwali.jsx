import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useData } from "../useData.js";
import { cases, monthLabel, niceDate, shortMonth, weight } from "../format.js";
import { ErrorBox, GrossBanner, Loading, Panel, Table } from "../components.jsx";

const MUTED = "#5d6066", LINE = "#dcdde1";
const KIND = { "Diwali month": "#e63336", "pre-Diwali month": "#1d1d1f", other: "#b4b6bc" };
const axis = { stroke: LINE, tick: { fill: MUTED, fontSize: 12 }, tickLine: false };

function Season({ s, today, first }) {
  const passed = s.order_by_latest < today;
  const days = Math.round((Date.parse(s.order_by_safe) - Date.parse(today)) / 86400000);
  return (
    <Panel id={`d-${s.year}`} title={`Diwali ${s.year}${s.diwali_date ? `: ${niceDate(s.diwali_date, today, { relative: false })}` : ""}`}
      sub={passed ? "Ordering window has passed" : first ? `${days > 0 ? `${days} days to the safe order date` : "Safe order date has passed"}` : (!s.in_plan ? "Beyond the 12-month plan" : "")}>
      <dl className="kv">
        <dt>Order between</dt><dd><strong>{niceDate(s.order_by_safe, today)}</strong> (safe, 130-day shipping) and <strong>{niceDate(s.order_by_latest, today)}</strong> (latest)</dd>
        <dt>In the warehouse by</dt><dd>{niceDate(s.in_warehouse_by, today, { relative: false })}</dd>
      </dl>
      {s.top_brands && s.top_brands.length > 0 && (
        <Table id={`b-${s.year}`} caption={`Top brands by extra cases for Diwali ${s.year} (month before Diwali and Diwali month, above a normal month)`}>
          <thead><tr><th scope="col">Brand</th><th scope="col" className="num">Extra cases</th></tr></thead>
          <tbody>{s.top_brands.map((b) => (
            <tr key={b.brand}><th scope="row"><a href={`#/brand/${encodeURIComponent(b.brand)}`}>{b.brand}</a></th>
              <td className="num" data-label="Extra cases">{cases(b.extra_cases, false)}</td></tr>
          ))}</tbody>
        </Table>
      )}
      {s.top_products && s.top_products.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <Table id={`p-${s.year}`} caption={`Top products for Diwali ${s.year}, with how much they usually rise`}>
            <thead><tr><th scope="col">Product</th><th scope="col" className="num">Extra cases</th><th scope="col" className="num">Month before Diwali</th><th scope="col" className="num">Diwali month</th></tr></thead>
            <tbody>{s.top_products.map((p) => (
              <tr key={p.product_id}>
                <th scope="row"><a href={`#/product/${encodeURIComponent(p.product_id)}`}>{p.name}</a></th>
                <td className="num" data-label="Extra cases">{cases(p.extra_cases_vs_normal, false)}</td>
                <td className="num" data-label="Month before Diwali">{p.diwali_lift_pre_month != null ? `${p.diwali_lift_pre_month}x usual` : "-"}</td>
                <td className="num" data-label="Diwali month">{p.diwali_lift_diwali_month != null ? `${p.diwali_lift_diwali_month}x usual` : "-"}</td>
              </tr>
            ))}</tbody>
          </Table>
        </div>
      )}
    </Panel>
  );
}

export default function Diwali() {
  const { data, error } = useData("/diwali");
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const today = data.today;
  const useKg = data.monthly.some((m) => m.kg != null);
  const rows = data.monthly.map((m) => ({ ...m, value: useKg ? (m.kg || 0) / 1000 : m.cases }));
  const months = new Set(rows.map((r) => r.month));
  const marks = data.diwali_dates.filter((d) => months.has(d.slice(0, 7)));
  const next = data.seasons.find((s) => s.order_by_latest >= today);
  const others = data.seasons.filter((s) => s !== next);
  return (
    <div data-ready="diwali">
      <p className="lead">Diwali moves every year, and so does the busy season. Stock for it has to be ordered about 130 days ahead.</p>
      <Panel id="monthly-h" title="Everything sold, by month" sub={useKg ? "Tonnes" : "Cases"}>
        <div className="chart" role="img" aria-label={`Total sold per month, ${useKg ? "in tonnes" : "in cases"}, with Diwali marked`}>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={rows} margin={{ top: 20, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={LINE} vertical={false} />
              <XAxis dataKey="month" tickFormatter={shortMonth} minTickGap={14} {...axis} />
              <YAxis width={64} tickFormatter={(v) => `${v.toLocaleString("en-US")}${useKg ? " t" : ""}`} {...axis} axisLine={false} />
              <Tooltip formatter={(v) => (useKg ? weight(v * 1000) : cases(v))} labelFormatter={monthLabel} cursor={{ fill: "#f6f6f7" }}
                contentStyle={{ border: `1px solid ${LINE}`, borderRadius: 0, fontSize: 13 }} />
              <Bar dataKey="value" name={useKg ? "Sold (weight)" : "Sold (cases)"} isAnimationActive={false}>
                {rows.map((r) => <Cell key={r.month} fill={KIND[r.kind] || KIND.other} />)}
              </Bar>
              {marks.map((d) => (
                <ReferenceLine key={d} x={d.slice(0, 7)} stroke={MUTED} strokeDasharray="2 3"
                  label={{ value: `Diwali ${d.slice(0, 4)}`, position: "top", fill: MUTED, fontSize: 11 }} />
              ))}
            </BarChart>
          </ResponsiveContainer>
          <div className="legend">
            <span className="key"><span className="sw box" style={{ background: KIND["pre-Diwali month"] }} />Month before Diwali</span>
            <span className="key"><span className="sw box" style={{ background: KIND["Diwali month"] }} />Diwali month</span>
            <span className="key"><span className="sw box" style={{ background: KIND.other }} />Other months</span>
            <span className="key"><span className="sw dot" style={{ borderColor: MUTED }} />Diwali date</span>
          </div>
        </div>
        <GrossBanner />
      </Panel>
      {next && <Season s={next} today={today} first />}
      {others.map((s) => <Season key={s.year} s={s} today={today} />)}
    </div>
  );
}
