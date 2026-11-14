// Click through every page in headless Chrome (installed locally; nothing is downloaded) and report
// console errors, failed API calls and pages that never finish loading. Screenshots go to SHOTS dir.
// Usage:  npm run smoke                       (dashboard running on http://127.0.0.1:5173)
//   bash:       BASE=http://127.0.0.1:5173 SHOTS=/tmp/shots npm run smoke
//   PowerShell: $env:BASE="http://127.0.0.1:5173"; $env:SHOTS="smoke-shots"; npm run smoke
// The browser is found automatically (Chrome, then Edge, then Chromium); set CHROME to a full path to override.
import puppeteer from "puppeteer-core";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE || "http://127.0.0.1:5173";
const SHOTS = process.env.SHOTS || "smoke-shots";

function findBrowser() {
  if (process.env.CHROME) return process.env.CHROME;
  const env = process.env;
  const win = [env.PROGRAMFILES, env["PROGRAMFILES(X86)"], env.LOCALAPPDATA].filter(Boolean);
  const candidates = {
    win32: [
      ...win.map((d) => path.join(d, "Google", "Chrome", "Application", "chrome.exe")),
      ...win.map((d) => path.join(d, "Microsoft", "Edge", "Application", "msedge.exe")),
    ],
    darwin: ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
             "/Applications/Chromium.app/Contents/MacOS/Chromium"],
    linux: ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge"],
  }[process.platform] || [];
  const hit = candidates.find((p) => fs.existsSync(p));
  if (!hit) {
    console.error(`No Chrome/Edge/Chromium found (looked in: ${candidates.join(", ") || "nothing for " + process.platform}). Set CHROME to the browser's full path.`);
    process.exit(1);
  }
  return hit;
}

const CHROME = findBrowser();
fs.mkdirSync(SHOTS, { recursive: true });

const api = async (p) => (await fetch(`${BASE}/api${p}`)).json();
const brands = await api("/brands");
const firstBrand = brands.brands.find((b) => b.next_order)?.brand;
const detail = await api(`/brands/${encodeURIComponent(firstBrand)}`);
const line = detail.orders.flatMap((o) => o.lines).find((l) => !l.on_hold);
const routes = [
  ["home", "#/"], ["brand", `#/brand/${encodeURIComponent(firstBrand)}`],
  ["product", `#/product/${encodeURIComponent(line.product_id)}`], ["diwali", "#/diwali"],
  ["questions", "#/questions"], ["quality", "#/quality"],
];
const only = (process.env.PAGES || "").split(",").filter(Boolean);
const todo = only.length ? routes.filter(([n]) => only.includes(n)) : routes;
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--no-first-run"] });
const results = [];
for (const width of [1280, 390]) {
  const page = await browser.newPage();
  await page.setViewport({ width, height: 900 });
  const problems = [];
  page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
  page.on("response", (r) => r.url().includes("/api/") && r.status() >= 400 && problems.push(`api ${r.status()} ${r.url()}`));
  for (const [name, hash] of todo) {
    const before = problems.length;
    await page.goto(`${BASE}/${hash}`, { waitUntil: "networkidle0" });
    let ok = true;
    try { await page.waitForSelector(`[data-ready="${name}"], .error`, { timeout: 45000 }); } catch { ok = false; }
    const err = await page.$(".error");
    const text = await page.evaluate(() => document.querySelector("main")?.innerText.slice(0, 300) || "");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    await page.screenshot({ path: `${SHOTS}/${name}-${width}.png`, fullPage: false });
    results.push({ page: name, width, loaded: ok && !err, horizontal_scroll: overflow,
                   problems: problems.slice(before), first_text: text.replace(/\s+/g, " ").slice(0, 120) });
  }
  await page.close();
}
await browser.close();
console.log(JSON.stringify(results, null, 2));
