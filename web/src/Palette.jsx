import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, CornerDownLeft, Package, Search, Tags } from "lucide-react";
import { getJSON } from "./api.js";
import { NAV } from "./Shell.jsx";
import { brandParts } from "./ui.jsx";

// Opens on Cmd/Ctrl+K, or "/" when not typing in a field.
export function usePaletteHotkeys(setOpen) {
  useEffect(() => {
    const on = (e) => {
      const t = e.target;
      const typing = t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) { e.preventDefault(); setOpen((o) => !o); }
      else if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); setOpen(true); }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [setOpen]);
}

const PAGES = NAV.flatMap((g) => g.items.map(([href, label, Icon]) => ({ kind: "Pages", label, href: `#/${href}`, Icon })));

export default function Palette({ open, onClose }) {
  const [q, setQ] = useState("");
  const [brands, setBrands] = useState([]);
  const [products, setProducts] = useState([]);
  const [sel, setSel] = useState(0);
  const input = useRef(null);
  const list = useRef(null);

  useEffect(() => {
    if (!open) return;
    setQ(""); setSel(0); setProducts([]);
    setTimeout(() => input.current?.focus(), 0);
    getJSON("/brands").then((d) => setBrands(d.brands.map((b) => b.brand))).catch(() => setBrands([]));
  }, [open]);

  useEffect(() => {                                       // product search, debounced, from the existing API
    const t = q.trim();
    if (!open || t.length < 2) { setProducts([]); return undefined; }
    const h = setTimeout(() => getJSON(`/search?q=${encodeURIComponent(t)}`).then((d) => setProducts(d.products || [])).catch(() => {}), 160);
    return () => clearTimeout(h);
  }, [q, open]);

  const items = useMemo(() => {
    const n = q.trim().toLowerCase();
    const pages = PAGES.filter((p) => !n || p.label.toLowerCase().includes(n));
    const br = brands.filter((b) => n && b.toLowerCase().includes(n)).slice(0, 8)
      .map((b) => ({ kind: "Brands", label: brandParts(b).label, hint: brandParts(b).code ? "item code" : "brand", href: `#/brand/${encodeURIComponent(b)}`, Icon: Tags }));
    const pr = products.slice(0, 10).map((p) => ({ kind: "Products", label: p.name, hint: `${p.product_id}${p.brand ? ` · ${brandParts(p.brand).label}` : ""}`,
      href: `#/product/${encodeURIComponent(p.product_id)}`, Icon: Package }));
    // Anything else is a question for the agent (opens Ask with it filled in).
    const ask = q.trim().length > 2 ? [{ kind: "Agent", label: `Ask the agent: "${q.trim()}"`, hint: "opens Ask", href: "#/ask", Icon: Bot, ask: q.trim() }] : [];
    return [...pages, ...br, ...pr, ...ask];
  }, [q, brands, products]);

  useEffect(() => { setSel(0); }, [q]);
  useEffect(() => { list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [sel]);

  if (!open) return null;
  const go = (it) => {
    if (!it) return;
    if (it.ask) { try { sessionStorage.setItem("ask-prefill", it.ask); } catch { /* blocked */ } }
    window.location.hash = it.href.slice(1); onClose();
  };
  const onKey = (e) => {
    if (e.key === "Escape") onClose();
    else if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(items.length - 1, s + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); go(items[sel]); }
  };
  let last = "";
  return (
    <div className="palette-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Search">
        <div className="palette-input">
          <Search className="ico" aria-hidden="true" />
          <input ref={input} autoFocus value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} placeholder="Agent console: go to a page, brand or product, or ask a question"
                 role="combobox" aria-expanded="true" aria-controls="palette-list" aria-activedescendant={`pi-${sel}`} />
          <span className="kbd">esc</span>
        </div>
        <div className="palette-list" id="palette-list" role="listbox" ref={list}>
          {items.length === 0 && <div className="palette-empty">No matches. Try a brand name or an item code.</div>}
          {items.map((it, i) => {
            const head = it.kind !== last ? <div className="palette-group" key={`g-${it.kind}`}>{it.kind}</div> : null;
            last = it.kind;
            return [head, (
              <div key={`${it.kind}-${it.href}`} id={`pi-${i}`} role="option" aria-selected={i === sel} className="palette-item"
                   onMouseEnter={() => setSel(i)} onClick={() => go(it)}>
                <it.Icon className="ico" aria-hidden="true" />
                <span>{it.label}</span>
                {it.hint && <span className="hint">{it.hint}</span>}
                {i === sel && <CornerDownLeft className="ico-sm" aria-hidden="true" style={{ color: "var(--muted)", marginLeft: it.hint ? 8 : "auto" }} />}
              </div>
            )];
          })}
        </div>
      </div>
    </div>
  );
}
