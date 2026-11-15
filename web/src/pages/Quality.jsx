import { BarChart3, PackageX, Target, Warehouse } from "lucide-react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useData } from "../useData.js";
import { cases, monthLabel, pct } from "../format.js";
import { ErrorBox, Loading } from "../components.jsx";
import { fmt0 } from "../ui.jsx";

// Forecast vs the 6-month average on one measure: both values, the better one marked in words.
function Compare({ icon: Icon, k, model, avg, fmt, lowerIsBetter }) {
  const better = model === avg ? null : (lowerIsBetter ? model < avg : model > avg) ? "forecast" : "average";
  return (
    <div className="kpi">
      <div className="k"><span className="ico-wrap"><Icon className="ico" aria-hidden="true" /></span>{k}</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <div><div className="v" style={{ fontSize: 26 }}>{fmt(model)}</div><div className="d">Our forecast{better === "forecast" ? " · better" : ""}</div></div>
        <div><div className="v" style={{ fontSize: 26, color: "var(--muted)" }}>{fmt(avg)}</div><div className="d">6-month average{better === "average" ? " · better" : ""}</div></div>
      </div>
    </div>
  );
}

function Row({ label, r }) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td className="num" data-label="Share of sales we could ship">{pct(r["fill_rate_%"])}</td>
      <td className="num" data-label="Times a product ran out">{Math.round(r.stockout_product_months).toLocaleString("en-US")}</td>
      <td className="num" data-label="Cases we could not ship">{cases(r.units_short, false)}</td>
      <td className="num" data-label="Stock held on average">{cases(r.avg_stock_cases, false)} ({r.months_of_cover.toFixed(1)} months of sales)</td>
    </tr>
  );
}

const NAMES = {
  avg: "the average of the last 6 months (what you do today)",
  ml: "our forecasting model",
  ml_regular: "our model, with large bulk orders handled separately",
  ml_simple: "a simpler version of our model",
  seasonal: "the same month last year, adjusted for recent growth",
  blend3: "an equal mix of our model, the 6-month average and last year's pattern",
};
function nameOf(c, t) {
  if (c.startsWith("blend_")) {
    const w = Number(c.split("_")[1]);
    return `a mix: ${Math.round(w * 100)}% ${NAMES[t.best_ml] || "model"} and ${Math.round((1 - w) * 100)}% the 6-month average`;
  }
  return NAMES[c] || c;
}

function Tournament({ t }) {
  const rows = Object.entries(t.table);
  const fmt = (v) => (v == null ? "-" : pct(v));
  const pts = (v) => (v == null ? "-" : `${v > 0 ? "+" : ""}${v.toFixed(1)} points`);
  return (
    <section className="panel">
      <header><h2>Which forecast we use</h2></header>
      <p className="lead">
        We use <strong>{nameOf(t.chosen, t)}</strong>. We tried {rows.length} ways of forecasting and replayed your ordering
        with each one, comparing how much of your sales each could ship <strong>while holding the same amount of stock</strong>.
        We picked the best on April to July 2026 ({nameOf(t.winner_apr_jul, t)}) and then checked it on August to October 2026,
        which it had not seen. {t.confirmed_aug_oct
          ? "It still did better there, so we use it."
          : "It did not beat the 6-month average there, so we stay with the 6-month average."}
        {" "}This test is re-run every month as new sales come in (<code>python run.py --retest</code>), and the choice is updated.
      </p>
      <div className="tbl-wrap" tabIndex={0} role="region" aria-labelledby="t-cap">
        <table className="tbl">
          <caption id="t-cap">Compared with the 6-month average at the same amount of stock (percentage points of sales shipped)</caption>
          <thead><tr><th scope="col">Way of forecasting</th><th scope="col" className="num">Apr-Jul 2026 (used to pick)</th>
            <th scope="col" className="num">Aug-Oct 2026 (check)</th><th scope="col" className="num">Month before Diwali (share shipped)</th>
            <th scope="col" className="num">Stock held (Aug-Oct)</th></tr></thead>
          <tbody>
            {rows.map(([c, r]) => (
              <tr key={c} className={c === t.chosen ? undefined : undefined}>
                <th scope="row">{nameOf(c, t)}{c === t.chosen && <> <span className="pill ok">used</span></>}</th>
                <td className="num" data-label="Apr-Jul">{pts(r.points_vs_avg_apr_jul)}</td>
                <td className="num" data-label="Aug-Oct">{pts(r.points_vs_avg_aug_oct)}</td>
                <td className="num" data-label="Month before Diwali">{fmt(r.fill_pre_diwali_cushion3)}</td>
                <td className="num" data-label="Stock held">{r.avg_stock_cushion3_aug_oct != null ? cases(r.avg_stock_cushion3_aug_oct, false) : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">"+1.0 points" means it shipped 1% more of your sales than the 6-month average while holding the same stock.
        "-" means it never held a comparable amount of stock in the replay, so there is no fair comparison.
        The month-before-Diwali and stock columns use the normal 3-month rule.</p>
    </section>
  );
}

export default function Quality() {
  const { data, error, loading } = useData("/forecast-quality");
  if (error) return <ErrorBox error={error} />;
  if (loading) return <Loading />;
  const m = data.overall.model, a = data.overall.avg;
  const matched = (data.stock_matched || []).filter((x) => x.difference_pts != null);
  const maxGap = matched.length ? Math.max(...matched.map((x) => Math.abs(x.difference_pts))) : null;
  const tie = maxGap != null && maxGap < 1;
  const moreStock = ((m.avg_stock_cases / a.avg_stock_cases - 1) * 100);
  const pre = data.pre_diwali;
  return (
    <div data-ready="quality">
      <div className="page-head">
        <div>
          <h2>How good is the forecast</h2>
          <p>Replay of {monthLabel(data.scored_from)} to {monthLabel(data.scored_to)}, {data.products.toLocaleString("en-US")} products, same ordering rule for both.
            Open any product to see its forecast against actual sales.</p>
        </div>
      </div>
      <div className="kpis stagger">
        <Compare icon={Target} k="Share of sales we could ship" model={m["fill_rate_%"]} avg={a["fill_rate_%"]} fmt={(v) => pct(v)} />
        <Compare icon={PackageX} k="Times a product ran out" model={Math.round(m.stockout_product_months)} avg={Math.round(a.stockout_product_months)} fmt={fmt0} lowerIsBetter />
        <Compare icon={BarChart3} k="Cases we could not ship" model={m.units_short} avg={a.units_short} fmt={(v) => cases(v, false)} lowerIsBetter />
        <Compare icon={Warehouse} k="Stock held on average (cases)" model={m.avg_stock_cases} avg={a.avg_stock_cases} fmt={(v) => cases(v, false)} lowerIsBetter />
      </div>
      <section className="panel">
        <header><h2>What the replay shows</h2></header>
        <p className="lead" style={{ color: "var(--ink-2)" }}>
        We replayed {monthLabel(data.scored_from)} to {monthLabel(data.scored_to)} for {data.products.toLocaleString("en-US")} products,
        ordering the same way you plan to (each order keeps {data.cushion_months} months of stock on hand when it arrives and covers about {data.cover_months} more months,
        shipping taking {data.lead_days_range[0]} to {data.lead_days_range[1]} days). We did it twice: once with our forecast, once with the
        average of the last 6 months. Our forecast shipped {pct(m["fill_rate_%"])} of sales against {pct(a["fill_rate_%"])}, but it
        held {moreStock.toFixed(0)}% more stock to do it.
        {maxGap != null && (tie
          ? ` With the same amount of stock, the two do about equally well (within ${maxGap.toFixed(1)} percentage points). So today the forecast is not better than averaging; its value is in showing what to order, when, and why.`
          : ` With the same amount of stock, ${matched[0].difference_pts > 0 ? "our forecast" : "averaging"} ships more (by up to ${maxGap.toFixed(1)} percentage points).`)}
        {pre && ` In the month before Diwali (${monthLabel(data.scored_to)}), averaging shipped ${pct(pre.avg["fill_rate_%"])} and our forecast ${pct(pre.model["fill_rate_%"])}.`}
        </p>
        <div className="tbl-wrap" tabIndex={0} role="region" aria-labelledby="q-cap">
          <table className="tbl">
            <caption id="q-cap">Replay of {monthLabel(data.scored_from)} to {monthLabel(data.scored_to)}</caption>
            <thead><tr><th scope="col">Way of planning</th><th scope="col" className="num">Share of sales we could ship</th>
              <th scope="col" className="num">Times a product ran out (product-months)</th><th scope="col" className="num">Cases we could not ship</th>
              <th scope="col" className="num">Stock held on average</th></tr></thead>
            <tbody>
              <Row label="Our forecast" r={m} />
              <Row label="Average of the last 6 months" r={a} />
            </tbody>
          </table>
        </div>
        <p className="note">Both start from the same stock. Sales that found no stock are counted as lost. Months before the first
          replayed order could arrive are left out, because both ways are identical then.</p>
      </section>
      {matched.length > 1 && (
        <section className="panel">
          <header><div><h2>At the same stock</h2><p className="sub">Share of sales shipped when both hold the same amount of stock, by cushion size</p></div></header>
          <div className="chart" style={{ height: 240 }}>
            <ResponsiveContainer>
              <LineChart data={matched} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="model_avg_stock" tickFormatter={(v) => fmt0(v)} tickLine={false} axisLine={false} />
                <YAxis tickLine={false} axisLine={false} width={48} domain={["auto", "auto"]} tickFormatter={(v) => `${v}%`} />
                <Tooltip content={({ active, payload, label }) => active && payload?.length ? (
                  <div className="tip"><strong>{fmt0(label)} cases held</strong>{payload.map((x) => <div key={x.dataKey}>{x.name}: {pct(x.value)}</div>)}</div>) : null} />
                <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
                <Line dataKey="model_fill_rate_pct" name="Our forecast" stroke="var(--accent)" strokeWidth={2} dot={{ r: 3 }} />
                <Line dataKey="averaging_fill_rate_pct_at_same_stock" name="6-month average" stroke="var(--chart-1)" strokeDasharray="4 3" strokeWidth={2} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <p className="note">Horizontal axis: average stock held, in cases.</p>
        </section>
      )}
      {data.tournament && <Tournament t={data.tournament} />}
    </div>
  );
}
