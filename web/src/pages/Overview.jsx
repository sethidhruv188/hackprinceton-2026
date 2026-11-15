import { AlertOctagon, ArrowRight, Boxes, Container, ShoppingCart, Tags } from "lucide-react";
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useData } from "../useData.js";
import { monthLabel, shortMonth } from "../format.js";
import { useState } from "react";
import { BrandName, EmptyState, KPI, SkeletonCards, StatusPill, Term, fmt0, fmt1, relDays, shortDate } from "../ui.jsx";
import Robot from "../robot/Robot.jsx";
import Briefing from "../robot/Briefing.jsx";
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

// Executive summary: the four numbers a CEO asks first, stock coverage ahead, and the five most urgent actions.
const URGENT = { "Too late for full lead time: expedite": 0, "Order now": 1, "Order this month": 2, "Coming up": 3 };
function Executive({ today }) {
  const [openK, setOpenK] = useState(null);
  const sum = useData("/summary");
  const br = useData("/brands");
  const proj = useData("/projection?months=6");
  if (sum.error || br.error) return null;
  if (!sum.data || !br.data) return <SkeletonCards />;
  const s = sum.data;
  const atRisk = br.data.brands.filter((b) => (b.below_floor_now || 0) > 0);
  const skusNow = br.data.brands.reduce((n, b) => n + (b.below_floor_now || 0), 0);
  const actions = br.data.brands.filter((b) => b.next_order)
    .sort((a, b) => (URGENT[a.next_order.status] ?? 9) - (URGENT[b.next_order.status] ?? 9)
      || a.next_order.order_by_safe.localeCompare(b.next_order.order_by_safe)).slice(0, 5);
  const p = proj.data;
  const nowBrands = br.data.brands.filter((b) => b.next_order?.status === "Order now");
  const DETAIL = {
    cases: ["Cases to order now, by brand", nowBrands.map((b) => [b.brand, `${fmt0(b.next_order.cases)} cases`])],
    containers: ["Containers, by brand (each brand shipped alone)", nowBrands.map((b) => [b.brand, b.next_order.containers_needed != null ? `${fmt0(b.next_order.containers_needed)} container${b.next_order.containers_needed === 1 ? "" : "s"}` : "size unknown"])],
    cbm: ["Volume, by brand", nowBrands.map((b) => [b.brand, b.next_order.cbm != null ? `${fmt1(b.next_order.cbm)} CBM` : "size unknown"])],
    risk: ["Products below the 3-month floor now, by brand", [...atRisk].sort((a, b) => b.below_floor_now - a.below_floor_now).map((b) => [b.brand, `${fmt0(b.below_floor_now)} of ${fmt0(b.active_products ?? b.products)} products`])],
  };
  const tgl = (k) => setOpenK((x) => (x === k ? null : k));
  return (
    <>
      <section className="hero hud scan" aria-label="Agent briefing">
        <span className="scanline" aria-hidden="true" />
        <Robot variant="hero" />
        <div>
          <p className="hero-title">Today's briefing</p>
          <Briefing />
          {actions.length > 0 && (
            <div className="actions-list" style={{ marginTop: 8 }}>
              {actions.slice(0, 3).map((b) => (
                <a key={b.brand} className="action" href={`#/orders?open=${encodeURIComponent(b.brand)}`}>
                  <BrandName name={b.brand} />
                  <span style={{ minWidth: 0 }}><span className="t">{fmt0(b.next_order.cases)} cases</span>
                    <span className="s"> · order by {shortDate(b.next_order.order_by_safe, true)} ({relDays(b.next_order.order_by_safe, today)})</span></span>
                  <StatusPill status={b.next_order.status} />
                </a>
              ))}
            </div>
          )}
        </div>
      </section>
      <div className="kpis stagger">
        <KPI big icon={ShoppingCart} tone="crit" k="Cases to order now" v={s.orders_now_cases} unit="cases" d={`${fmt0(s.orders_now)} brand orders · click for detail`} onClick={() => tgl("cases")} pressed={openK === "cases"} />
        <KPI big icon={Container} tone="info" k="Containers" v={s.orders_now_containers ?? null} unit="containers" d="If each brand ships alone" onClick={() => tgl("containers")} pressed={openK === "containers"} />
        <KPI big icon={Boxes} k={<>Volume (<Term k="cbm">CBM</Term>)</>} v={s.orders_now_cbm != null ? Math.round(s.orders_now_cbm) : null} unit="CBM"
             d={s.cbm_estimated_pct > 0 ? `${fmt0(s.cbm_estimated_pct)}% from estimated case sizes` : "From case sizes"} onClick={() => tgl("cbm")} pressed={openK === "cbm"} />
        <KPI big icon={AlertOctagon} tone={atRisk.length ? "warn" : "ok"} k="Brands at risk" v={atRisk.length} unit={`of ${br.data.brands.length}`}
             d={`${fmt0(skusNow)} products below the 3-month floor now`} onClick={() => tgl("risk")} pressed={openK === "risk"} />
        {openK && (
          <div className="kpi-detail" role="region" aria-label={DETAIL[openK][0]}>
            <div className="card-title" style={{ marginBottom: 10 }}>{DETAIL[openK][0]}</div>
            {DETAIL[openK][1].length === 0 ? <p className="note">None.</p> : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 8 }}>
                {DETAIL[openK][1].map(([b, v]) => (
                  <a key={b} href={`#/brand/${encodeURIComponent(b)}`} className="action" style={{ gridTemplateColumns: "minmax(0,1fr) auto" }}>
                    <BrandName name={b} /><b className="num">{v}</b></a>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      <div className="grid-2" style={{ marginBottom: 20 }}>
        <section className="panel" aria-labelledby="cov-h">
          <header><div><h2 id="cov-h">Stock coverage, next 6 months</h2><p className="sub">All products, in cases, from the engine's stock projection</p></div></header>
          {!p ? <span className="sk" style={{ height: 220 }} /> : !p.available ? (
            <EmptyState title="No stock projection">It appears once the engine runs with stock data (data/stock.csv).</EmptyState>
          ) : (
            <>
              <div className="chart" style={{ height: 240 }}>
                <ResponsiveContainer>
                  <ComposedChart data={p.months} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid vertical={false} strokeDasharray="3 3" />
                    <XAxis dataKey="month" tickFormatter={shortMonth} tickLine={false} axisLine={false} />
                    <YAxis tickLine={false} axisLine={false} width={64} tickFormatter={(v) => fmt0(v)} label={{ value: "cases", angle: -90, position: "insideLeft", fill: "var(--muted)", fontSize: 11 }} />
                    <Tooltip content={({ active, payload, label }) => active && payload?.length ? (
                      <div className="tip"><strong>{monthLabel(label)}</strong>{payload.map((x) => <div key={x.dataKey}>{x.name}: {fmt0(x.value)} cases</div>)}</div>) : null} />
                    <Area dataKey="projected_with_suggested_order" name="With the suggested orders" stroke="var(--ok-fill)" fill="var(--ok-bg)" strokeWidth={2} />
                    <Line dataKey="projected_stock" name="With no new orders" stroke="var(--chart-1)" strokeDasharray="4 3" dot={false} strokeWidth={1.5} />
                    <Line dataKey="floor_3_months" name="3-month floor" stroke="var(--crit-fill)" dot={false} strokeWidth={1.5} className="floor-line" />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <div className="legend-dots">
                <span><i style={{ background: "var(--ok-fill)" }} />With the suggested orders</span>
                <span><i style={{ background: "var(--chart-1)" }} />No new orders</span>
                <span><i style={{ background: "var(--crit-fill)" }} />3-month floor</span>
              </div>
            </>
          )}
        </section>
        <section className="panel" aria-labelledby="act-h">
          <header><div><h2 id="act-h">Top 5 urgent actions</h2><p className="sub">Most urgent first</p></div><a className="btn ghost" href="#/orders">All orders<ArrowRight className="ico-sm" /></a></header>
          {actions.length === 0 ? <EmptyState icon={Tags} title="Nothing to order">No brand has an order in the 12-month plan.</EmptyState> : (
            <div className="actions-list">
              {actions.map((b) => (
                <a key={b.brand} className="action" href={`#/brand/${encodeURIComponent(b.brand)}`}>
                  <BrandName name={b.brand} />
                  <span style={{ minWidth: 0 }}>
                    <div className="t">{fmt0(b.next_order.cases)} cases</div>
                    <div className="s">by {shortDate(b.next_order.order_by_safe, true)} · {relDays(b.next_order.order_by_safe, today)}</div>
                  </span>
                  <StatusPill status={b.next_order.status} />
                </a>
              ))}
            </div>
          )}
        </section>
      </div>
    </>
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
      <Executive today={today} />
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
