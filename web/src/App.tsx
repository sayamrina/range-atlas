import { lazy, Suspense, useEffect, useState } from "react";
import AboutPage from "./pages/AboutPage";
import DataPage from "./pages/DataPage";
import RunsPage from "./pages/RunsPage";

// the map (MapLibre) and the SQL page (DuckDB) are the heavy parts; load them only when opened
const MapPage = lazy(() => import("./pages/MapPage"));
const QueryPage = lazy(() => import("./pages/QueryPage"));

const PAGES = [
  { path: "", label: "Map" },
  { path: "runs", label: "Model runs" },
  { path: "data", label: "Data quality" },
  { path: "query", label: "Query the data" },
  { path: "about", label: "About" },
] as const;

function useRoute() {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("?")[0];
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export default function App() {
  const route = useRoute();
  const page = PAGES.find((p) => p.path === route) ?? PAGES[0];

  useEffect(() => {
    document.title = page.path ? `${page.label} · Range Atlas` : "Range Atlas";
    if (page.path) window.scrollTo(0, 0);
  }, [page]);

  return (
    <div className={`shell ${page.path === "" ? "shell-map" : ""}`}>
      <header className="top">
        <a className="brand" href="#/">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="10.5" />
            <circle cx="12" cy="12" r="3.5" />
          </svg>
          Range Atlas
        </a>
        <nav aria-label="Main">
          {PAGES.map((p) => (
            <a key={p.path} href={`#/${p.path}`} aria-current={p === page ? "page" : undefined}>
              {p.label}
            </a>
          ))}
        </nav>
      </header>
      <aside className="project-popup" aria-label="Portfolio project">
        <span>This app was developed by Amrina as part of a portfolio.</span>
        <a href="https://sayamrina.github.io/" target="_blank" rel="noopener">
          View the portfolio →
        </a>
      </aside>
      <main className={page.path === "" ? "main-map" : "main-page"}>
        <Suspense fallback={<p className="loading">Loading…</p>}>
          {page.path === "" && <MapPage />}
          {page.path === "runs" && <RunsPage />}
          {page.path === "data" && <DataPage />}
          {page.path === "query" && <QueryPage />}
          {page.path === "about" && <AboutPage />}
        </Suspense>
      </main>
    </div>
  );
}
