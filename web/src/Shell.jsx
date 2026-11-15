import { useEffect, useRef, useState } from "react";
import {
  Activity, BarChart3, CalendarDays, ChevronsLeft, ChevronsRight, Globe2, HelpCircle, LayoutDashboard, Menu,
  MessageSquare, Moon, Package, Search, ShoppingCart, Sprout, Sun, Tags, Volume2, VolumeX,
} from "lucide-react";
import { useAgent } from "./agent.jsx";
import { MOOD_LABEL } from "./robot/Robot.jsx";
import { useSettings } from "./settings.jsx";
import { niceDate } from "./format.js";
import { getJSON } from "./api.js";

// Sidebar groups, in order. href "" is the Overview (#/). badge: key into useBadges().
export const NAV = [
  { label: "", items: [["", "Overview", LayoutDashboard]] },
  { label: "Plan", items: [["orders", "What to order", ShoppingCart, "orders"], ["brands", "Brands", Tags], ["products", "Products", Package]] },
  { label: "Seasons", items: [["diwali", "Festivals", CalendarDays], ["harvest", "Harvest", Sprout]] },
  { label: "Sourcing", items: [["sourcing", "Sourcing", Globe2]] },
  { label: "Agent", items: [["ask", "Ask", MessageSquare], ["activity", "Activity", Activity]] },
  { label: "Review", items: [["questions", "Questions for you", HelpCircle, "questions"], ["quality", "Forecast quality", BarChart3]] },
];

const WIDE = "(min-width: 961px)";
export function useLayout() {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

// Light / dark, remembered per browser (falls back to light when storage is blocked).
export function useTheme() {
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem("theme") || "dark"; } catch { return "dark"; } });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("theme", theme); } catch { /* storage blocked */ }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

function useCollapsed() {
  const [c, setC] = useState(() => { try { return localStorage.getItem("sidebar") === "collapsed"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem("sidebar", c ? "collapsed" : "open"); } catch { /* blocked */ } }, [c]);
  return [c, setC];
}

// Badge counts from the API: orders to place now, questions still unanswered.
export function useBadges() {
  const { rev } = useSettings();
  const [b, setB] = useState({});
  useEffect(() => {
    let live = true;
    Promise.allSettled([getJSON("/summary"), getJSON("/questions")]).then(([s, q]) => {
      if (!live) return;
      setB({
        orders: s.status === "fulfilled" ? s.value.orders_now : null,
        dataThrough: s.status === "fulfilled" ? s.value.data_through : null,
        questions: q.status === "fulfilled" ? q.value.questions.filter((x) => x.answer == null || x.answer === "").length : null,
      });
    });
    return () => { live = false; };
  }, [rev]);
  return b;
}

function useAgentStatus() {
  const { rev } = useSettings();
  const [st, setSt] = useState(null);
  useEffect(() => {
    let live = true;
    getJSON("/ask/status").then((s) => live && setSt(s)).catch(() => live && setSt({ available: false, reason: "API not reachable" }));
    return () => { live = false; };
  }, [rev]);
  return st;
}

function Mark() {
  const [ok, setOk] = useState(true);
  return (
    <span className="mark" aria-hidden="true">
      {ok ? <img src="/brand/shaktigroupusa_cover.jpg" alt="" onError={() => setOk(false)} />
        : <span style={{ display: "grid", placeItems: "center", height: "100%", fontWeight: 700, color: "#18181b" }}>S</span>}
    </span>
  );
}

export function Sidebar({ active, collapsed, setCollapsed, open, onNavigate, theme, toggleTheme }) {
  const badges = useBadges();
  const { sound, setSound } = useAgent();
  return (
    <aside className={`sidebar${open ? " open" : ""}`} id="sidebar" aria-label="Sidebar">
      <a className="brandbar" href="#/" aria-label="Shakti Group, overview" onClick={onNavigate}>
        <Mark />
        <span className="brandname"><b>Shakti Group</b><span>Ordering</span></span>
      </a>
      <nav className="nav" aria-label="Main">
        {NAV.map((g, gi) => (
          <div className="nav-group" key={gi}>
            {g.label && <span className="nav-label">{g.label}</span>}
            <ul>
              {g.items.map(([href, label, Icon, badge]) => {
                const n = badge ? badges[badge] : null;
                return (
                  <li key={href}>
                    <a href={`#/${href}`} aria-current={active === href ? "page" : undefined} onClick={onNavigate} title={collapsed ? label : undefined}>
                      <Icon className="ico" aria-hidden="true" />
                      <span className="txt">{label}</span>
                      {n > 0 && <span className={`badge ${badge === "orders" ? "crit" : "warn"}`} aria-label={`${n} pending`}>{n}</span>}
                    </a>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      <div className="sidebar-foot">
        <button type="button" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
          {theme === "dark" ? <Sun className="ico" /> : <Moon className="ico" />}<span className="txt">{theme === "dark" ? "Light mode" : "Dark mode"}</span>
        </button>
        <button type="button" onClick={() => setSound(!sound)} aria-pressed={sound} aria-label={sound ? "Turn interface sounds off" : "Turn interface sounds on"}>
          {sound ? <Volume2 className="ico" /> : <VolumeX className="ico" />}<span className="txt">{sound ? "Sounds on" : "Sounds off"}</span>
        </button>
        <button type="button" className="hide-sm" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          {collapsed ? <ChevronsRight className="ico" /> : <ChevronsLeft className="ico" />}<span className="txt">Collapse</span>
        </button>
      </div>
    </aside>
  );
}

// "Viewing as of" pill: opens a small popover to change the plan date (time travel), same API as before.
function AsOf({ settings, update }) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(settings.as_of);
  const ref = useRef(null);
  useEffect(() => setDate(settings.as_of), [settings.as_of]);
  useEffect(() => {
    if (!open) return undefined;
    const on = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const key = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", on);
    window.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", on); window.removeEventListener("keydown", key); };
  }, [open]);
  return (
    <div className="rel" ref={ref}>
      <button type="button" className="pill-btn" onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="dialog">
        <CalendarDays className="ico-sm" aria-hidden="true" />
        <span className="hide-sm">Viewing as of</span> <b>{niceDate(settings.as_of, null, { relative: false })}</b>
      </button>
      {open && (
        <form className="popover" role="dialog" aria-label="Plan as of" onSubmit={(e) => { e.preventDefault(); update({ as_of: date }); setOpen(false); }}>
          <h3>Plan as of</h3>
          <p className="sub">Statuses and deadlines are recomputed for this date. Quantities come from the engine run of {niceDate(settings.run_date, null, { relative: false })}.</p>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required aria-label="Plan date" />
          <div className="btn-row">
            <button className="btn primary" type="submit">Apply</button>
            {settings.as_of_set && <button className="btn" type="button" onClick={() => { update({ as_of: "" }); setOpen(false); }}>Back to engine run</button>}
          </div>
        </form>
      )}
    </div>
  );
}

// The dashboard always shows the business's real data. If the server was left in demo mode (the synthetic data used by
// the tests), switch it back once on load. No mode switch is shown.
function useRealData(settings, update) {
  useEffect(() => {
    if (settings?.mode === "demo" && settings.modes?.real) update({ mode: "real" });
  }, [settings?.mode, settings?.modes?.real, update]);
}

export function TopBar({ title, crumb, wide, onMenu, onSearch }) {
  const { settings, update, error } = useSettings();
  const agent = useAgentStatus();
  const { mood } = useAgent();
  const badges = useBadges();
  useRealData(settings, update);
  const stale = settings && settings.as_of > settings.run_date
    ? Math.round((Date.parse(settings.as_of) - Date.parse(settings.run_date)) / 86400000) : 0;
  return (
    <>
      <header className="topbar">
        {!wide && <button className="icon-btn menu-button" type="button" onClick={onMenu} aria-label="Open menu" aria-controls="sidebar"><Menu className="ico" /></button>}
        <div className="crumbs">
          {crumb && <><a href={crumb[0]}>{crumb[1]}</a><span aria-hidden="true">/</span></>}
          <h1>{title}</h1>
        </div>
        <div className="top-actions">
          {agent && (
            <a className="pill-btn hide-sm" href="#/ask" title={agent.reason || agent.model}>
              <span className={`dot ${agent.available ? "live" : "off"}`} aria-hidden="true" />
              {agent.available ? `Agent: ${MOOD_LABEL[mood]}` : "Agent offline"}
            </a>
          )}
          <button type="button" className="pill-btn" onClick={onSearch} aria-label="Search (Command K)">
            <Search className="ico-sm" aria-hidden="true" /><span className="hide-sm">Search</span>
            <span className="kbd hide-sm">⌘K</span>
          </button>
          {settings && <AsOf settings={settings} update={update} />}
        </div>
      </header>
      {settings && (
        <div className="status-strip" role="status" aria-label="System status">
          <span>Engine run <b>{niceDate(settings.run_date, null, { relative: false })}</b></span>
          {badges.dataThrough && <span>Sales data to end of <b>{new Date(`${badges.dataThrough}-01T00:00:00Z`).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" })}</b></span>}
          {agent && <span><i className={`dot ${agent.available ? "live" : "off"}`} aria-hidden="true" />Agent {agent.available ? "online" : "offline"}</span>}
          {stale > 0 && <span className="stale">The engine run is {stale} day{stale === 1 ? "" : "s"} older than the viewing date: re-run the engine for fresh numbers</span>}
        </div>
      )}
      {error && <p className="asof-note" role="alert">{String(error.message || error)}</p>}
      {settings?.as_of_set && (
        <p className="asof-note" role="note">
          Viewing as of {niceDate(settings.as_of, null, { relative: false })}: statuses and deadlines are recomputed for this date;
          quantities come from the engine run of {niceDate(settings.run_date, null, { relative: false })}.
        </p>
      )}
    </>
  );
}

export function ShellFrame({ active, title, crumb, children, onSearch }) {
  const wide = useLayout();
  const [collapsed, setCollapsed] = useCollapsed();
  const [open, setOpen] = useState(false);
  const [theme, toggleTheme] = useTheme();
  return (
    <div className={`shell${collapsed && wide ? " collapsed" : ""}`}>
      {!wide && open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden="true" />}
      <Sidebar active={active} collapsed={collapsed && wide} setCollapsed={setCollapsed} open={!wide && open}
               onNavigate={() => setOpen(false)} theme={theme} toggleTheme={toggleTheme} />
      <div className="main">
        <TopBar title={title} crumb={crumb} wide={wide} onMenu={() => setOpen(true)} onSearch={onSearch} />
        <main className="content">{children}</main>
      </div>
    </div>
  );
}

