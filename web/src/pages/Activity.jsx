import { useMemo, useState } from "react";
import { useData } from "../useData.js";
import { ErrorBox, Loading, Panel, Source, Table, Tag, Toolbar } from "../components.jsx";
import { monthLabel } from "../format.js";

// "Nov 15, 2026, 00:28" in the viewer's time zone; the log never carries message text or phone numbers.
const fmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
function when(s) {
  if (!s) return "-";
  const d = new Date(s.length === 10 ? `${s}T00:00:00` : s);
  return Number.isNaN(d.getTime()) ? s : fmt.format(d);
}

const SOURCE = { agent: "Agent", owner: "Owner", research: "Research" };
const CHANNEL = { imessage: "iMessage", dashboard: "Dashboard", terminal: "Terminal" };
const ADJ_STATE = {
  active: ["Active", "st-green"], proposed: ["Waiting for owner's yes", "st-amber"],
  expired: ["Expired", "st-grey"], cancelled: ["Cancelled", "st-grey"],
};

export default function Activity() {
  const { data, error } = useData("/activity?limit=500");
  const [source, setSource] = useState("all");
  const events = useMemo(() => (data ? data.events.filter((e) => source === "all" || e.source === source) : []), [data, source]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading what="Loading activity" />;
  const counts = Object.entries(data.counts).sort((a, b) => b[1] - a[1]);

  return (
    <div data-ready="activity">
      <section className="panel" aria-labelledby="act-sum">
        <header>
          <h2 id="act-sum">What happened</h2>
          <p className="sub">{data.total.toLocaleString("en-US")} events recorded. Event types and tools only; never message text or phone numbers.</p>
        </header>
        {counts.length === 0 ? <p className="note">Nothing recorded yet. Ask the agent a question or text it to start the log.</p> : (
          <Table id="act-counts" caption="Events by type" stack={false}>
            <thead><tr><th scope="col">Event</th><th scope="col" className="num">Count</th></tr></thead>
            <tbody>
              {counts.map(([k, n]) => (
                <tr key={k}><th scope="row">{k}</th><td className="num">{n.toLocaleString("en-US")}</td></tr>
              ))}
            </tbody>
          </Table>
        )}
      </section>

      <Panel id="act-adj" title="Owner adjustments"
             sub="Demand changes the owner taught the agent; applied to the forecast before order quantities, never to the model">
        {!data.adjustments?.length ? (
          <p className="note">None yet. Tell the agent something like "expect 30% more moong in December, we're running a promo".</p>
        ) : (
          <Table id="act-a" caption="Adjustments, newest first">
            <thead>
              <tr><th scope="col">Id</th><th scope="col">Applies to</th><th scope="col">Change</th><th scope="col">Months</th>
                <th scope="col">State</th><th scope="col">Told via</th></tr>
            </thead>
            <tbody>
              {data.adjustments.map((a) => {
                const [label, cls] = ADJ_STATE[a.state] || [a.state, "st-grey"];
                return (
                  <tr key={a.adj_id}>
                    <td data-label="Id">{a.adj_id}</td>
                    <th scope="row" data-label="Applies to">{a.target} <span className="muted small">({a.scope})</span></th>
                    <td data-label="Change">{a.label}</td>
                    <td data-label="Months">{a.months.map((m) => monthLabel(m)).join(", ")}</td>
                    <td data-label="State"><span className={`status-text ${cls}`}>{label}</span></td>
                    <td data-label="Told via">{CHANNEL[a.channel] || a.channel || "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Panel>

      <Panel id="act-log" title="Log" sub="Newest first">
        <Toolbar>
          <label>From
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="all">Everyone</option>
              <option value="agent">Agent</option>
              <option value="owner">Owner</option>
              <option value="research">Research</option>
            </select>
          </label>
          <span className="count" role="status">{events.length.toLocaleString("en-US")} shown</span>
        </Toolbar>
        <Table id="act" caption="Agent, owner and research events">
          <thead>
            <tr><th scope="col" className="num">When</th><th scope="col">From</th><th scope="col">Channel</th>
              <th scope="col">What</th><th scope="col">Detail</th></tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={`${e.at}-${i}`}>
                <td className="num" data-label="When">{when(e.at)}</td>
                <td data-label="From">{SOURCE[e.source] || e.source}{e.role === "guest" && <>{" "}<Tag>guest</Tag></>}</td>
                <td data-label="Channel">{CHANNEL[e.channel] || e.channel || <span className="muted">-</span>}</td>
                <th scope="row" data-label="What">{e.what}{e.demo && <>{" "}<Tag>demo</Tag></>}</th>
                <td data-label="Detail">{e.detail || <span className="muted">-</span>}{e.url && <> <Source url={e.url} /></>}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Panel>
    </div>
  );
}
