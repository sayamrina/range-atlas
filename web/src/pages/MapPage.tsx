import { useEffect, useMemo, useRef, useState } from "react";
import { ModelLegend, Importance, ResponseCurve } from "../components/Charts";
import MapView, { type Layer } from "../components/MapView";
import { aucReading, fmt, load, MODEL_LABEL, rampCss, type Meta, type ModelResult, type Species } from "../data";

function readParams() {
  return new URLSearchParams(window.location.hash.split("?")[1] ?? "");
}

export default function MapPage() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [species, setSpecies] = useState<Species[]>([]);
  const [slug, setSlug] = useState(() => readParams().get("sp") ?? "calliptamus-italicus");
  const [layer, setLayer] = useState<Layer>({ kind: "suitability", model: "ensemble" });
  const [showPoints, setShowPoints] = useState(true);
  const [result, setResult] = useState<ModelResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([load<Meta>("meta.json"), load<Species[]>("species.json")])
      .then(([m, s]) => { setMeta(m); setSpecies(s); })
      .catch(() => setError("The map data did not load. Reload the page; if it keeps failing, the weekly rebuild may still be running."));
  }, []);

  const current = species.find((s) => s.slug === slug) ?? null;
  const drawer = useRef<HTMLElement>(null);

  // keep the pinned label in view inside the drawer (only where the drawer scrolls on its own)
  useEffect(() => {
    const d = drawer.current;
    const el = d?.querySelector<HTMLElement>('.label[aria-pressed="true"]');
    if (!d || !el || d.scrollHeight <= d.clientHeight) return;
    if (el.offsetTop < d.scrollTop || el.offsetTop + el.offsetHeight > d.scrollTop + d.clientHeight) {
      d.scrollTop = el.offsetTop - d.clientHeight / 3;
    }
  }, [slug, species.length]);

  useEffect(() => {
    window.history.replaceState(null, "", `#/?sp=${slug}`);
    setResult(null);
    if (current?.modelled) load<ModelResult>(`models/${slug}.json`).then(setResult).catch(() => setResult(null));
  }, [slug, current?.modelled]);

  const families = useMemo(() => {
    const groups = new Map<string, Species[]>();
    for (const s of species) groups.set(s.family, [...(groups.get(s.family) ?? []), s]);
    return [...groups.entries()];
  }, [species]);

  if (error) return <p className="page-error">{error}</p>;
  if (!meta) return <p className="loading">Loading the atlas…</p>;

  const effectiveLayer: Layer = layer.kind === "suitability" && !current?.modelled ? { kind: "none" } : layer;
  const predictor = layer.kind === "predictor" ? meta.predictors.find((p) => p.id === layer.id) : null;

  return (
    <div className="atlas">
      <section className="drawer" aria-label="Species" ref={drawer}>
        <p className="drawer-intro">Choose a species to see where it was recorded and where the climate suits it.</p>
        {families.map(([family, list]) => (
          <div className="family" key={family}>
            <h2>{family}</h2>
            <ul>
              {list.map((s) => (
                <li key={s.slug}>
                  <button type="button" className="label" aria-pressed={s.slug === slug} onClick={() => setSlug(s.slug)}>
                    <i className="pin" aria-hidden="true" />
                    <em>{s.name}</em>
                    <span className="common">{s.common}</span>
                    <span className="counts">
                      {fmt.format(s.clean)} records{s.source === "obis" ? " from OBIS" : ""}
                      {s.metrics && `, AUC ${Math.max(s.metrics.glm.auc, s.metrics.rf.auc).toFixed(2)}`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      <section className="stage">
        <div className="controls" role="group" aria-label="Map layers">
          <label>
            <span>Colour the map by</span>
            <select
              value={layer.kind === "predictor" ? layer.id : layer.kind === "suitability" ? `model:${layer.model}` : "none"}
              onChange={(e) => {
                const v = e.target.value;
                if (v === "none") setLayer({ kind: "none" });
                else if (v.startsWith("model:")) setLayer({ kind: "suitability", model: v.slice(6) as "glm" | "rf" | "ensemble" });
                else setLayer({ kind: "predictor", id: v });
              }}
            >
              <optgroup label="Climate suitability">
                {(["ensemble", "glm", "rf"] as const).map((m) => (
                  <option key={m} value={`model:${m}`} disabled={!current?.modelled}>{MODEL_LABEL[m]}</option>
                ))}
              </optgroup>
              <optgroup label="Climate (WorldClim)">
                {meta.predictors.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </optgroup>
              <option value="none">Nothing, records only</option>
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={showPoints} onChange={(e) => setShowPoints(e.target.checked)} />
            Show records
          </label>
        </div>

        <MapView meta={meta} species={current} layer={effectiveLayer} showPoints={showPoints} />

        <div className="map-key" aria-label="Map key">
          {effectiveLayer.kind === "suitability" && (
            <div>
              <span>Climate suitability</span>
              <i className="ramp" style={{ background: rampCss(meta.ramps.suitability) }} />
              <span className="ends"><span>low</span><span>high</span></span>
            </div>
          )}
          {predictor && (
            <div>
              <span>{predictor.label}</span>
              <i className="ramp" style={{ background: rampCss(meta.ramps.predictor) }} />
              <span className="ends"><span>{predictor.min}</span><span>{predictor.max}</span></span>
            </div>
          )}
          {showPoints && current && (
            <div className="key-points"><i aria-hidden="true" /><span><em>{current.name}</em>, recorded {meta.years[0]}–{meta.years[1]}</span></div>
          )}
          {layer.kind === "suitability" && current && !current.modelled && (
            <p className="key-note">Marine species are mapped but not modelled: the climate layers describe land.</p>
          )}
        </div>
      </section>

      {current && (
        <section className="card" aria-label={`Model for ${current.name}`}>
          <header>
            <h2><em>{current.name}</em></h2>
            <p>{current.common}. <a href={current.link} target="_blank" rel="noopener">{current.source === "gbif" ? "Species page on GBIF" : "Taxon page on OBIS"}</a></p>
          </header>
          <dl className="facts">
            <div><dt>Records harvested</dt><dd>{fmt.format(current.harvested)}</dd></div>
            <div><dt>Passed the checks</dt><dd>{fmt.format(current.clean)}</dd></div>
            {current.modelled && <div><dt>Grid cells used</dt><dd>{fmt.format(current.cells)}</dd></div>}
          </dl>
          {!current.modelled && (
            <p className="card-note">This species lives in the sea. The records come from OBIS and are shown on the map, but the
              climate predictors here describe land, so no model is fitted.</p>
          )}
          {result && (
            <>
              <h3>How well the models separate it from other grasshoppers</h3>
              <table className="scores">
                <thead><tr><th scope="col">Model</th><th scope="col">AUC</th><th scope="col">TSS</th><th scope="col">Reading</th></tr></thead>
                <tbody>
                  {(["glm", "rf"] as const).map((m) => (
                    <tr key={m}>
                      <th scope="row"><i className={`swatch ${m}`} aria-hidden="true" />{MODEL_LABEL[m]}</th>
                      <td>{result.metrics[m].auc.toFixed(2)}</td>
                      <td>{result.metrics[m].tss.toFixed(2)}</td>
                      <td>{aucReading(result.metrics[m].auc)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="card-note">Scores come from {result.folds}-fold spatial cross-validation on {result.block_degrees}° blocks, against
                {" "}{fmt.format(result.background_cells)} cells where other grasshoppers and crickets were recorded. A species found almost
                everywhere people look scores low, because its climate barely differs from the background.</p>

              <h3>Which climate variables matter</h3>
              <ModelLegend />
              <Importance result={result} predictors={meta.predictors} />

              <h3>Response to each variable</h3>
              <div className="curves">
                {meta.predictors.map((p) => <ResponseCurve key={p.id} predictor={p} data={result.response[p.id]} />)}
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}
