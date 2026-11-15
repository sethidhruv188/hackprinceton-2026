import { useEffect, useRef, useState } from "react";
import { useSettings } from "./settings.jsx";
import { niceDate } from "./format.js";

// Sidebar groups, in order. href "" is the Overview (#/).
export const NAV = [
  { label: "Overview", items: [["", "Overview"]] },
  { label: "Plan", items: [["orders", "What to order"], ["brands", "Brands"], ["products", "Products"]] },
  { label: "Seasons", items: [["diwali", "Festivals"], ["harvest", "Harvest"]] },
  { label: "Sourcing", items: [["sourcing", "Sourcing"]] },
  { label: "Agent", items: [["ask", "Ask"], ["activity", "Activity"]] },
  { label: "Review", items: [["questions", "Questions for you"], ["quality", "Forecast quality"]] },
];

const WIDE = "(min-width: 961px)";

function useWide() {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

function Logo() {
  const [ok, setOk] = useState(true);
  return (
    <a className="logo" href="#/" aria-label="Shakti Group USA, overview">
      {ok ? (
        <span className="logo-crop">
          <img src="/brand/shaktigroupusa_cover.jpg" alt="" onError={() => setOk(false)} />
        </span>
      ) : (
        <span className="wordmark">Shakti Group USA</span>
      )}
    </a>
  );
}

function Settings() {
  const { settings, update, error } = useSettings();
  const [date, setDate] = useState("");
  useEffect(() => { if (settings) setDate(settings.as_of); }, [settings]);
  if (!settings) return null;
  const modes = settings.modes || {};
  return (
    <form className="settings" aria-label="Dashboard settings" onSubmit={(e) => { e.preventDefault(); update({ as_of: date }); }}>
      <fieldset>
        <legend>Data</legend>
        <div className="seg">
          {[["demo", "Demo"], ["real", "Real data"]].map(([m, label]) => (
            <label key={m}>
              <input type="radio" name="mode" value={m} checked={settings.mode === m} disabled={!modes[m]}
                     onChange={() => update({ mode: m })} />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      <div>
        <label className="field-label" htmlFor="asof">Plan as of</label>
        <div className="date-row">
          <input id="asof" type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
          <button className="btn" type="submit">Apply</button>
        </div>
        {settings.as_of_set && (
          <button className="btn" type="button" style={{ marginTop: 8 }} onClick={() => update({ as_of: "" })}>
            Back to {niceDate(settings.run_date, null, { relative: false })}
          </button>
        )}
      </div>
      {error && <p className="hint st-red" role="alert">{String(error.message || error)}</p>}
    </form>
  );
}

export function Sidebar({ active, wide, id }) {
  const ref = useRef(null);
  // One navigation tree: inline on wide screens, a native popover on narrow ones (attribute only when narrow).
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (wide) el.removeAttribute("popover");
    else el.setAttribute("popover", "auto");
  }, [wide]);
  const close = () => { if (!wide && ref.current?.hidePopover) { try { ref.current.hidePopover(); } catch { /* closed */ } } };
  return (
    <aside className="sidebar" id={id} ref={ref}>
      <Logo />
      <nav className="nav" aria-label="Main">
        {NAV.map((g) => (
          <div className="nav-group" key={g.label}>
            {g.items.length > 1 || g.label !== g.items[0][1] ? <span className="nav-label">{g.label}</span> : null}
            <ul>
              {g.items.map(([href, label]) => (
                <li key={href}>
                  <a href={`#/${href}`} aria-current={active === href ? "page" : undefined} onClick={close}>{label}</a>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
      <Settings />
    </aside>
  );
}

export function TopBar({ title, menuId, wide }) {
  const { settings } = useSettings();
  return (
    <>
      <header className="topbar">
        {!wide && (
          <button className="btn menu-button" type="button" popovertarget={menuId} aria-controls={menuId}>Menu</button>
        )}
        <h1>{title}</h1>
        {settings && (
          <div className="meta">
            <span>Viewing as of <strong>{niceDate(settings.as_of, null, { relative: false })}</strong></span>
            <span><strong>{settings.mode === "demo" ? "Demo data" : settings.mode === "real" ? "Real data" : "Custom folders"}</strong></span>
          </div>
        )}
      </header>
      {settings?.as_of_set && (
        <p className="asof-note" role="note">
          Viewing as of {niceDate(settings.as_of, null, { relative: false })}: statuses and deadlines are recomputed for this
          date; quantities come from the engine run of {niceDate(settings.run_date, null, { relative: false })}.
        </p>
      )}
    </>
  );
}

export function useLayout() {
  return useWide();
}
