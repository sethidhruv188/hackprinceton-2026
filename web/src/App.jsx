import { useEffect, useState } from "react";
import Home from "./pages/Home.jsx";
import Brand from "./pages/Brand.jsx";
import Product from "./pages/Product.jsx";
import Diwali from "./pages/Diwali.jsx";
import Questions from "./pages/Questions.jsx";
import Quality from "./pages/Quality.jsx";

// Tiny hash router: #/, #/brand/<name>, #/product/<id>, #/diwali, #/questions, #/quality
function useRoute() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const on = () => { setHash(window.location.hash || "#/"); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const parts = hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  return parts;
}

const NAV = [
  ["", "What to order"], ["diwali", "Diwali"], ["questions", "Questions for you"], ["quality", "How good is the forecast"],
];

export default function App() {
  const [page, arg] = useRoute();
  let body;
  if (page === "brand") body = <Brand name={arg} />;
  else if (page === "product") body = <Product id={arg} />;
  else if (page === "diwali") body = <Diwali />;
  else if (page === "questions") body = <Questions />;
  else if (page === "quality") body = <Quality />;
  else body = <Home />;
  const active = page === "brand" || page === "product" ? "" : page || "";
  return (
    <>
      <header className="top">
        <nav aria-label="Main">
          {NAV.map(([p, label]) => (
            <a key={p} href={`#/${p}`} aria-current={active === p ? "page" : undefined}>{label}</a>
          ))}
        </nav>
      </header>
      <main>{body}</main>
    </>
  );
}
