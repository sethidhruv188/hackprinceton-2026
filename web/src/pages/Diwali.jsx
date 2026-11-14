import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useData } from "../useData.js";
import { cases, monthLabel, niceDate, shortMonth, weight } from "../format.js";
import { ErrorBox, GrossBanner, Loading } from "../components.jsx";

const KIND_COLOR = { "Diwali month": "var(--now-fg)", "pre-Diwali month": "var(--month-fg)", other: "var(--coming-fg)" };

function Season({ s, today, big }) {
  const passed = s.order_by_latest < today;
  return (
    <section className="order">
      <h2 style={{ marginTop: 0 }}>Diwali {s.year}{s.diwali_date ? `: ${niceDate(s.diwali_date, today, { relative: false })}` : ""}</h2>
      <p className={big ? "big-window" : undefined}>
        {passed ? "The ordering window has passed. " : ""}
        Place orders between {niceDate(s.order_by_safe, today)} (safe) and {niceDate(s.order_by_latest, today)} (latest),
        so the stock is in the warehouse by {niceDate(s.in_warehouse_by, today, { relative: false })}.
      </p>
      {!s.in_plan && <p className="muted">This season is beyond the 12-month plan.</p>}
      {s.top_brands && s.top_brands.length > 0 && (
        <div className="table-wrapper" tabIndex={0} role="region" aria-labelledby={`b-${s.year}`}>
          <table>
            <caption id={`b-${s.year}`}>Top brands by extra cases for Diwali {s.year} (the month before Diwali and the Diwali month, above a normal month)</caption>
            <thead><tr><th scope="col">Brand</th><th scope="col" className="num">Extra cases</th></tr></thead>
            <tbody>{s.top_brands.map((b) => (
              <tr key={b.brand}><th scope="row"><a href={`#/brand/${encodeURIComponent(b.brand)}`}>{b.brand}</a></th>
                <td className="num" data-label="Extra cases">{cases(b.extra_cases, false)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {s.top_products && s.top_products.length > 0 && (
        <div className="table-wrapper" tabIndex={0} role="region" aria-labelledby={`p-${s.year}`}>
          <table>
            <caption id={`p-${s.year}`}>Top products for Diwali {s.year}, with how much they usually rise</caption>
            <thead><tr><th scope="col">Product</th><th scope="col" className="num">Extra cases</th><th scope="col" className="num">Month before Diwali</th><th scope="col" className="num">Diwali month</th></tr></thead>
            <tbody>{s.top_products.map((p) => (
              <tr key={p.product_id}>
                <th scope="row"><a href={`#/product/${encodeURIComponent(p.product_id)}`}>{p.name}</a></th>
                <td className="num" data-label="Extra cases">{cases(p.extra_cases_vs_normal, false)}</td>
                <td className="num" data-label="Month before Diwali">{p.diwali_lift_pre_month != null ? `${p.diwali_lift_pre_month}x usual` : "-"}</td>
                <td className="num" data-label="Diwali month">{p.diwali_lift_diwali_month != null ? `${p.diwali_lift_diwali_month}x usual` : "-"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function Diwali() {
  const { data, error, loading } = useData("/diwali");
  if (error) return <ErrorBox error={error} />;
  if (loading) return <Loading />;
  const today = data.today;
  const useKg = data.monthly.some((m) => m.kg != null);
  const rows = data.monthly.map((m) => ({ ...m, value: useKg ? (m.kg || 0) / 1000 : m.cases }));
  const months = new Set(rows.map((r) => r.month));
  const marks = data.diwali_dates.filter((d) => months.has(d.slice(0, 7)));
  const next = data.seasons.find((s) => s.order_by_latest >= today);
  const others = data.seasons.filter((s) => s !== next);
  return (
    <div data-ready="diwali">
      <h1>Diwali</h1>
      <p className="muted">Diwali moves every year, and so does the busy season. The bars show everything sold each month;
        the dashed lines mark Diwali.</p>
      <div className="chart" role="img" aria-label={`Total sold per month, ${useKg ? "in tonnes" : "in cases"}, with Diwali marked`}>
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={rows} margin={{ top: 18, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="month" tickFormatter={shortMonth} minTickGap={14} stroke="var(--muted)" />
            <YAxis stroke="var(--muted)" width={64} tickFormatter={(v) => `${v.toLocaleString("en-US")}${useKg ? " t" : ""}`} />
            <Tooltip formatter={(v) => (useKg ? weight(v * 1000) : cases(v))} labelFormatter={monthLabel}
              contentStyle={{ background: "var(--card)", border: "1px solid var(--line)" }} />
            <Bar dataKey="value" name={useKg ? "Sold (weight)" : "Sold (cases)"} isAnimationActive={false}>
              {rows.map((r) => <Cell key={r.month} fill={KIND_COLOR[r.kind] || KIND_COLOR.other} />)}
            </Bar>
            {marks.map((d) => (
              <ReferenceLine key={d} x={d.slice(0, 7)} stroke="var(--text)" strokeDasharray="5 4"
                label={{ value: `Diwali ${d.slice(0, 4)}`, position: "top", fill: "var(--muted)", fontSize: 12 }} />
            ))}
          </BarChart>
        </ResponsiveContainer>
        <div className="legend">
          <span><span className="swatch" style={{ background: KIND_COLOR["pre-Diwali month"] }} />Month before Diwali</span>
          <span><span className="swatch" style={{ background: KIND_COLOR["Diwali month"] }} />Diwali month</span>
          <span><span className="swatch" style={{ background: KIND_COLOR.other }} />Other months</span>
          <span>- - - Diwali date</span>
        </div>
      </div>
      <GrossBanner />
      {next && <Season s={next} today={today} big />}
      {others.map((s) => <Season key={s.year} s={s} today={today} />)}
    </div>
  );
}
