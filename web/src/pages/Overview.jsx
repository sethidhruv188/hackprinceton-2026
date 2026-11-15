import { useData } from "../useData.js";
import { niceDate, cases, pct } from "../format.js";

const n0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const pts = (x) => (x == null ? "-" : `${x >= 0 ? "+" : ""}${x.toFixed(1)} points`);
const period = (p) => {
  if (!p) return "-";
  const [a, b] = p.split("..");
  const f = (ym) => new Date(`${ym}-01T00:00:00Z`).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
  return b ? `${f(a).split(" ")[0]}–${f(b)}` : f(a);
};
const titleCase = (s) => String(s || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const sentence = (s) => { const t = String(s || "").toLowerCase(); return t.charAt(0).toUpperCase() + t.slice(1); };
const FORECASTER = { "blend_0.5": "Blend (half model, half 6-month average)", ml: "Forecasting model", avg: "6-month average" };

function Skeleton() {
  return <div className="skeleton" aria-hidden="true"><span /><span /><span /></div>;
}

function TournamentTable({ r }) {
  const name = FORECASTER[r.chosen] || r.chosen;
  const stockPct = r.stock.pct_more;
  const short = name.split(" (")[0];
  return (
    <>
      <div className="tbl-wrap" role="region" aria-labelledby="replay-caption" tabIndex={0}>
        <table className="tbl stack">
          <caption id="replay-caption">
            {name} against the 6-month average (how the business orders today), same ordering rule, replaying {n0.format(r.products)} products
          </caption>
          <thead>
            <tr>
              <th scope="col">Measure</th>
              <th scope="col">Months replayed</th>
              <th scope="col" className="num">{short}</th>
              <th scope="col" className="num">6-month average</th>
              <th scope="col" className="num">Difference</th>
            </tr>
          </thead>
          <tbody>
            <tr className="emph">
              <th scope="row">Share of demand met, at equal stock: selection period (used to choose the forecaster)</th>
              <td data-label="Months">{period(r.selection.period)}</td>
              <td className="num na" aria-label="not shown: measured as a difference at equal stock">-</td>
              <td className="num na" aria-label="not shown: measured as a difference at equal stock">-</td>
              <td className="num strong" data-label="Difference">{pts(r.selection.points_vs_avg)}</td>
            </tr>
            <tr>
              <th scope="row">Share of demand met, at equal stock: confirmation period (months not used to choose)</th>
              <td data-label="Months">{period(r.confirmation.period)}</td>
              <td className="num na" aria-label="not shown: measured as a difference at equal stock">-</td>
              <td className="num na" aria-label="not shown: measured as a difference at equal stock">-</td>
              <td className="num strong" data-label="Difference">{pts(r.confirmation.points_vs_avg)}<br /><span className="sub">essentially a tie</span></td>
            </tr>
            <tr>
              <th scope="row">Share of demand met before Diwali, at the 3-month cushion</th>
              <td data-label="Months">{(r.pre_diwali.months || []).map((m) => period(m)).join(", ")}</td>
              <td className="num" data-label={short}>{pct(r.pre_diwali.chosen)}</td>
              <td className="num" data-label="6-month average">{pct(r.pre_diwali.avg)}</td>
              <td className="num" data-label="Difference">{pts(r.pre_diwali.chosen - r.pre_diwali.avg)}</td>
            </tr>
            <tr>
              <th scope="row">Average stock held, at the 3-month cushion</th>
              <td data-label="Months">{period(r.confirmation.period)}</td>
              <td className="num" data-label={short}>{cases(r.stock.chosen)}</td>
              <td className="num" data-label="6-month average">{cases(r.stock.avg)}</td>
              <td className="num" data-label="Difference">{stockPct == null ? "-" : `${stockPct >= 0 ? "+" : ""}${stockPct.toFixed(1)}% stock`}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="note">
        Results from replaying real sales history month by month: orders placed with the same rule, arriving after 105–130 days,
        unmet demand lost. "At equal stock" compares how much demand each forecaster meets when both hold the same amount of stock.
        The forecaster was chosen on the selection period and then checked on later months it had not seen; on those months the
        two are essentially level. At the owner's 3-month cushion it holds about {stockPct == null ? "-" : `${Math.round(stockPct)}%`} more stock.
      </p>
      {r.not_completed?.length > 0 && (
        <p className="note">Not tested to the end: the simpler-model and seasonal candidates were not completed ({r.not_completed.join(", ")}).</p>
      )}
      <p className="note">No dollar figures: the NetSuite exports have quantities only, no prices or costs.</p>
    </>
  );
}

function SummaryTable({ r, demo }) {
  const m = r.overall?.model || {}, a = r.overall?.avg || {};
  const rows = [
    ["Share of demand met", pct(m["fill_rate_%"]), pct(a["fill_rate_%"])],
    ["Product-months with a stockout", n0.format(m.stockout_product_months ?? 0), n0.format(a.stockout_product_months ?? 0)],
    ["Average stock held", cases(m.avg_stock_cases), cases(a.avg_stock_cases)],
    ["Months of cover held", m.months_of_cover?.toFixed(1) ?? "-", a.months_of_cover?.toFixed(1) ?? "-"],
  ];
  return (
    <>
      <div className="tbl-wrap" role="region" aria-labelledby="replay-caption" tabIndex={0}>
        <table className="tbl stack">
          <caption id="replay-caption">
            Forecasting model against the 6-month average, same ordering rule ({r.cushion_months}-month cushion), replaying {n0.format(r.products)} products,
            {" "}{period(`${r.scored_from}..${r.scored_to}`)}{demo ? " (demo data)" : ""}
          </caption>
          <thead><tr><th scope="col">Measure</th><th scope="col" className="num">Forecasting model</th><th scope="col" className="num">6-month average</th></tr></thead>
          <tbody>
            {rows.map(([k, x, y]) => (
              <tr key={k}><th scope="row">{k}</th><td className="num" data-label="Forecasting model">{x}</td><td className="num" data-label="6-month average">{y}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">Engine verdict, at equal stock: {r.verdict}</p>
      <p className="note">No dollar figures: the data has quantities only, no prices or costs.</p>
    </>
  );
}

function Figure({ k, v, unit, d, status }) {
  return (
    <div className="figure">
      <div className="k">{k}</div>
      <div className="v">{v}{unit && <small>{unit}</small>}</div>
      {status && <div className={`status ${status[1]}`}>{status[0]}</div>}
      {d && <div className="d">{d}</div>}
    </div>
  );
}

export default function Overview() {
  const { data, error, loading } = useData("/overview");
  if (error) return <p className="st-red" role="alert">Could not load the overview: {String(error.message || error)}</p>;
  if (loading && !data) return <div data-loading="overview"><Skeleton /></div>;
  const { replay, counts, next_harvest: h, diwali, alerts, settings } = data;
  const today = settings.as_of;
  const demo = settings.mode === "demo";

  return (
    <div data-ready="overview">
      <section className="panel" aria-labelledby="replay-h">
        <header>
          <h2 id="replay-h">Replaying real history: our forecaster against the 6-month average</h2>
          <p className="sub">{replay ? `Source: outputs/${replay.source}` : ""}{demo ? " · demo data" : ""}</p>
        </header>
        {!replay ? <p>No replay results yet: run the engine (python run.py).</p>
          : replay.source === "forecaster_tournament.json" ? <TournamentTable r={replay} /> : <SummaryTable r={replay} demo={demo} />}
      </section>

      <section className="panel" aria-labelledby="today-h">
        <header>
          <h2 id="today-h">Today</h2>
          <p className="sub">As of {niceDate(today, null, { relative: false })}</p>
        </header>
        <div className="figures">
          <Figure k="Brand orders to place now" v={n0.format(counts.order_now)} unit="orders" status={["Order now", "st-red"]}
                  d={cases(counts.order_now_cases)} />
          <Figure k="Due this month" v={n0.format(counts.this_month)} unit="orders" status={["Order this month", "st-amber"]} />
          <Figure k="Already too late" v={n0.format(counts.too_late)} unit="orders"
                  status={counts.too_late ? ["Too late: expedite", "st-red"] : ["None too late", "st-green"]} />
          <Figure k="Next harvest window"
                  v={h ? titleCase(h.commodity) : "None"}
                  status={h ? [`${sentence(h.status)}: ${h.confirmed ? "confirmed by owner" : "needs owner confirmation"}`, h.status === "ORDER NOW" ? "st-red" : "st-amber"] : null}
                  d={h ? `${h.country}: order ${niceDate(h.window_start, null, { relative: false })} to ${niceDate(h.order_by, null, { relative: false })}; arrives ${niceDate(h.arrival_earliest, null, { relative: false })} to ${niceDate(h.arrival_latest, null, { relative: false })}`
                     : "No harvest plan in these outputs yet."} />
          <Figure k={diwali ? `Diwali ${diwali.year} order deadline` : "Diwali order deadline"}
                  v={diwali ? n0.format(Math.max(diwali.days_to_safe, 0)) : "-"} unit={diwali ? "days" : ""}
                  d={diwali ? `Order by ${niceDate(diwali.order_by_safe, null, { relative: false })} (130-day shipping), latest ${niceDate(diwali.order_by_latest, null, { relative: false })}` : "No Diwali season ahead in the plan."} />
          <Figure k="Active country alerts" v={n0.format(alerts.length)} unit="alerts"
                  status={alerts.length ? ["Check sources", "st-amber"] : ["None", "st-green"]} />
        </div>
      </section>

      <section className="panel" aria-labelledby="alerts-h">
        <header>
          <h2 id="alerts-h">Active country alerts</h2>
          <p className="sub">Problems reported in producing countries; dismiss them in Questions for you</p>
        </header>
        {alerts.length === 0 ? <p className="note">No active alerts.</p> : (
          <div className="tbl-wrap" role="region" aria-labelledby="alerts-h" tabIndex={0}>
            <table className="tbl stack">
              <thead><tr><th scope="col">Type</th><th scope="col">Commodity</th><th scope="col">Country</th><th scope="col">Issue</th>
                <th scope="col" className="num">Reported</th><th scope="col">Source</th></tr></thead>
              <tbody>
                {alerts.map((a) => (
                  <tr key={a.alert_id}>
                    <td data-label="Type"><span className={`label ${a.label === "demo" ? "demo" : "real"}`}>{a.label}</span></td>
                    <td data-label="Commodity">{titleCase(a.commodity)}</td>
                    <th scope="row" data-label="Country">{a.country}</th>
                    <td data-label="Issue">{a.issue}</td>
                    <td className="num" data-label="Reported">{niceDate(a.reported_on, null, { relative: false })}</td>
                    <td data-label="Source"><a href={a.source_url} target="_blank" rel="noreferrer noopener">{new URL(a.source_url).hostname}</a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
