import { useEffect, useState } from "react";
import Home from "./pages/Home.jsx";
import Brand from "./pages/Brand.jsx";
import Product from "./pages/Product.jsx";
import Diwali from "./pages/Diwali.jsx";
import Questions from "./pages/Questions.jsx";
import Quality from "./pages/Quality.jsx";
import Overview from "./pages/Overview.jsx";
import { NAV, Sidebar, TopBar, useLayout } from "./Shell.jsx";

// Tiny hash router: #/, #/orders, #/brand/<name>, #/product/<id>, #/diwali, #/questions, #/quality, ...
function useRoute() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const on = () => { setHash(window.location.hash || "#/"); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
}

function NotYet({ name }) {
  return (
    <div className="placeholder-page" data-ready={`todo-${name}`}>
      <p>This page is built in the next step, once the layout and the Overview are approved.</p>
    </div>
  );
}

const TITLES = Object.fromEntries(NAV.flatMap((g) => g.items.map(([href, label]) => [href, label])));

export default function App() {
  const [page, arg] = useRoute();
  const wide = useLayout();
  let body, active = page || "", title;
  if (page === "brand") { body = <Brand name={arg} />; active = "brands"; title = arg; }
  else if (page === "product") { body = <Product id={arg} />; active = "products"; title = "Product"; }
  else if (page === "orders") body = <Home />;
  else if (page === "diwali") body = <Diwali />;
  else if (page === "questions") body = <Questions />;
  else if (page === "quality") body = <Quality />;
  else if (["brands", "products", "harvest", "sourcing", "ask", "activity"].includes(page)) body = <NotYet name={page} />;
  else { body = <Overview />; active = ""; }
  title = title || TITLES[active] || "Overview";

  useEffect(() => { document.title = `${title} · Shakti Group USA`; }, [title]);

  return (
    <div className="shell">
      <Sidebar active={active} wide={wide} id="sidebar" />
      <div className="main">
        <TopBar title={title} menuId="sidebar" wide={wide} />
        <main className="content">{body}</main>
      </div>
    </div>
  );
}
