// Plain-English formatting: real dates, cases with separators, kg as tonnes above 1,000 kg.
const num0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const num1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });

export function parseDay(s) {
  if (!s) return null;
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d || 1));
}

export function daysBetween(a, b) {
  return Math.round((parseDay(b) - parseDay(a)) / 86400000);
}

// "Sun, Nov 22 (8 days)", "Thu, Oct 22 (23 days ago)", "Mon, Mar 1, 2027 (107 days)"
export function niceDate(s, today, { relative = true } = {}) {
  const d = parseDay(s);
  if (!d) return "-";
  const t = parseDay(today);
  const opts = { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" };
  if (!t || d.getUTCFullYear() !== t.getUTCFullYear()) opts.year = "numeric";
  const label = d.toLocaleDateString("en-US", opts);
  if (!relative || !t) return label;
  const n = daysBetween(today, s);
  const rel = n === 0 ? "today" : n === 1 ? "tomorrow" : n > 0 ? `${num0.format(n)} days` : n === -1 ? "yesterday" : `${num0.format(-n)} days ago`;
  return `${label} (${rel})`;
}

export function monthLabel(ym) {
  const d = parseDay(ym.length === 7 ? `${ym}-01` : ym);
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

export function shortMonth(ym) {
  const d = parseDay(ym.length === 7 ? `${ym}-01` : ym);
  return d.toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });
}

export function cases(n, unit = true) {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  const s = num0.format(Math.round(n));
  return unit ? `${s} ${Math.round(n) === 1 ? "case" : "cases"}` : s;
}

export function weight(kg) {
  if (kg === null || kg === undefined || Number.isNaN(kg)) return "weight unknown";
  return kg >= 1000 ? `${num1.format(kg / 1000)} t` : `${num0.format(kg)} kg`;
}

export function pct(x, digits = 1) {
  if (x === null || x === undefined || Number.isNaN(x)) return "-";
  return `${x.toFixed(digits)}%`;
}

// Order statuses: shown as text, with a restrained color class (never color alone, never an icon).
export const STATUS = {
  "Order now": { cls: "st-red", short: "Order now" },
  "Order this month": { cls: "st-amber", short: "Order this month" },
  "Coming up": { cls: "st-grey", short: "Coming up" },
  "Too late for full lead time: expedite": { cls: "st-red", short: "Too late: expedite" },
};

export function titleCase(s) {
  return String(s ?? "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function sentence(s) {
  const t = String(s ?? "").toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url || ""; }
}

// Lead with the date that can still be met.
export function orderBy(o, today) {
  return o.order_by_safe < today ? `Order by ${niceDate(o.order_by_latest, today)} at the latest`
                                 : `Order by ${niceDate(o.order_by_safe, today)}`;
}
