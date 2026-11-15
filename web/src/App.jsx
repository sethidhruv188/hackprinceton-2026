import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { AgentProvider } from "./agent.jsx";
import Dock from "./robot/Dock.jsx";
import Home from "./pages/Home.jsx";
import Brand from "./pages/Brand.jsx";
import Product from "./pages/Product.jsx";
import Diwali from "./pages/Diwali.jsx";
import Questions from "./pages/Questions.jsx";
import Quality from "./pages/Quality.jsx";
import Overview from "./pages/Overview.jsx";
import Brands from "./pages/Brands.jsx";
import Products from "./pages/Products.jsx";
import Harvest from "./pages/Harvest.jsx";
import Sourcing from "./pages/Sourcing.jsx";
import Ask from "./pages/Ask.jsx";
import Activity from "./pages/Activity.jsx";
import { NAV, ShellFrame } from "./Shell.jsx";
import Palette, { usePaletteHotkeys } from "./Palette.jsx";
import { ToastProvider, brandParts } from "./ui.jsx";

// Tiny hash router: #/, #/orders, #/brand/<name>, #/product/<id>, ... The query part (#/orders?f=now) holds page state
// (filters, sort, open drawer) and is ignored for routing. Page changes use the View Transitions API when available.
const pathOf = (h) => (h || "#/").replace(/^#\/?/, "").split("?")[0];
function useRoute() {
  const [path, setPath] = useState(pathOf(window.location.hash));
  const cur = useRef(path);
  useEffect(() => {
    const on = () => {
      const next = pathOf(window.location.hash);
      if (next === cur.current) return;                 // only the query changed: same page
      cur.current = next;
      const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      if (document.startViewTransition && !reduced) document.startViewTransition(() => flushSync(() => setPath(next)));
      else setPath(next);
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return path.split("/").map(decodeURIComponent);
}

const TITLES = Object.fromEntries(NAV.flatMap((g) => g.items.map(([href, label]) => [href, label])));

export default function App() {
  const [page, arg] = useRoute();
  const [palette, setPalette] = useState(false);
  usePaletteHotkeys(setPalette);
  let body, active = page || "", title, crumb = null;
  if (page === "brand") { body = <Brand name={arg} />; active = "brands"; title = brandParts(arg).label; crumb = ["#/brands", "Brands"]; }
  else if (page === "product") { body = <Product id={arg} />; active = "products"; title = arg; crumb = ["#/products", "Products"]; }
  else if (page === "orders") body = <Home />;
  else if (page === "diwali") body = <Diwali />;
  else if (page === "questions") body = <Questions />;
  else if (page === "quality") body = <Quality />;
  else if (page === "brands") body = <Brands />;
  else if (page === "products") body = <Products />;
  else if (page === "harvest") body = <Harvest />;
  else if (page === "sourcing") body = <Sourcing />;
  else if (page === "ask") body = <Ask />;
  else if (page === "activity") body = <Activity />;
  else { body = <Overview />; active = ""; }
  title = title || TITLES[active] || "Overview";

  useEffect(() => { document.title = `${title} · Shakti Group Ordering`; }, [title]);

  return (
    <AgentProvider>
      <ToastProvider>
        <ShellFrame active={active} title={title} crumb={crumb} onSearch={() => setPalette(true)}>
          <div className="page-enter" key={`${page}/${arg || ""}`}>{body}</div>
        </ShellFrame>
        {active !== "" && active !== "ask" && <Dock />}
        <Palette open={palette} onClose={() => setPalette(false)} />
      </ToastProvider>
    </AgentProvider>
  );
}
