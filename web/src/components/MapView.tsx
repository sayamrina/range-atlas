import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import { API, dataUrl, type Meta, type Species } from "../data";

export type Layer = { kind: "suitability"; model: "glm" | "rf" | "ensemble" } | { kind: "predictor"; id: string } | { kind: "none" };

type Props = { meta: Meta; species: Species | null; layer: Layer; showPoints: boolean };

function useDark() {
  const q = "(prefers-color-scheme: dark)";
  const [dark, setDark] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = () => setDark(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return dark;
}

// OpenFreeMap vector styles (OpenStreetMap data, no API key)
const styleUrl = (dark: boolean) => `https://tiles.openfreemap.org/styles/${dark ? "dark" : "positron"}`;

// Data layers go under the first label layer, so place names stay readable on top
function labelLayer(m: MlMap) {
  return m.getStyle().layers.find((l) => l.type === "symbol")?.id;
}

function overlayUrl(layer: Layer, species: Species | null) {
  if (layer.kind === "predictor") return dataUrl(`overlays/${layer.id}.png`);
  if (layer.kind === "suitability" && species?.modelled) {
    return API ? `${API}/api/species/${species.slug}/suitability.png?model=${layer.model}&t=${Date.now()}`
               : dataUrl(`overlays/${species.slug}-${layer.model}.png`);
  }
  return null;
}

export default function MapView({ meta, species, layer, showPoints }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  // counts completed style loads; layer effects re-run after each one (first load and theme changes)
  const [ready, setReady] = useState(0);
  const dark = useDark();
  const appliedDark = useRef(dark);
  const loadedMap = useRef<MlMap | null>(null); // the map whose current style has finished loading

  useEffect(() => {
    const [w, s, e, n] = meta.bounds;
    const m = new maplibregl.Map({
      container: box.current!,
      style: styleUrl(dark),
      bounds: [[w, s + 2], [e, n - 4]],
      fitBoundsOptions: { padding: 8 },
      maxBounds: [[w - 25, s - 12], [e + 25, n + 8]],
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    setReady(0);
    appliedDark.current = dark;
    loadedMap.current = null;
    m.on("style.load", () => { loadedMap.current = m; setReady((r) => r + 1); });
    map.current = m;
    if (import.meta.env.DEV) (window as unknown as { __map: MlMap }).__map = m;
    return () => m.remove();
    // the style is swapped in place below when the colour scheme changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta]);

  useEffect(() => {
    const m = map.current;
    if (!m || appliedDark.current === dark) return;
    appliedDark.current = dark;
    loadedMap.current = null;
    m.setStyle(styleUrl(dark));
  }, [dark]);

  // raster overlay: model surface or climate predictor
  useEffect(() => {
    const m = map.current;
    if (!m || !ready || loadedMap.current !== m) return;
    if (m.getLayer("overlay")) m.removeLayer("overlay");
    if (m.getSource("overlay")) m.removeSource("overlay");
    const url = overlayUrl(layer, species);
    if (!url) return;
    const [w, s, e, n] = meta.bounds;
    m.addSource("overlay", { type: "image", url, coordinates: [[w, n], [e, n], [e, s], [w, s]] });
    m.addLayer({ id: "overlay", type: "raster", source: "overlay",
      paint: { "raster-opacity": layer.kind === "predictor" ? 0.8 : 0.92, "raster-resampling": "nearest", "raster-fade-duration": 0 } },
      m.getLayer("points-ring") ? "points-ring" : labelLayer(m));
  }, [ready, layer, species, meta]);

  // occurrence points from vector tiles (static z/x/y.pbf, or PostGIS through the API)
  useEffect(() => {
    const m = map.current;
    if (!m || !ready || loadedMap.current !== m) return;
    for (const id of ["points", "points-ring"]) if (m.getLayer(id)) m.removeLayer(id);
    if (m.getSource("occ")) m.removeSource("occ");
    if (!species || !showPoints) return;
    const tiles = API ? [`${API}/tiles/{z}/{x}/{y}.pbf?species=${species.slug}`]
                      : [dataUrl("tiles/{z}/{x}/{y}.pbf").replace("%7Bz%7D", "{z}").replace("%7Bx%7D", "{x}").replace("%7By%7D", "{y}")];
    m.addSource("occ", { type: "vector", tiles, minzoom: 0, maxzoom: 6 });
    const filter: maplibregl.FilterSpecification = ["==", ["get", "s"], species.index];
    // merged points grow with the square root of how many records they hold; the ring is the same size plus 1.5 px
    const radius = (extra: number): maplibregl.ExpressionSpecification => ["interpolate", ["linear"], ["zoom"],
      3, ["+", 1.8 + extra, ["*", 0.9, ["sqrt", ["get", "n"]]]],
      7, ["+", 3 + extra, ["*", 0.6, ["sqrt", ["get", "n"]]]],
      10, 5 + extra];
    const before = labelLayer(m);
    m.addLayer({ id: "points-ring", type: "circle", source: "occ", "source-layer": "occurrences", filter,
      paint: { "circle-radius": radius(1.5), "circle-color": dark ? "#18201B" : "#FFFFFF", "circle-opacity": 0.9 } }, before);
    m.addLayer({ id: "points", type: "circle", source: "occ", "source-layer": "occurrences", filter,
      paint: { "circle-radius": radius(0), "circle-color": dark ? "#6FD0C8" : "#0F5257" } }, before);
  }, [ready, species, showPoints, dark]);

  // click a point to see what it is
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "260px" });
    const onClick = (e: maplibregl.MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f) return;
      const p = f.properties as { n: number; y: number; b: string };
      const basis = p.b ? p.b.toLowerCase().replace(/_/g, " ") : "unknown basis";
      const html = p.n > 1
        ? `<strong>${p.n} records</strong><br>Latest from ${p.y || "an unknown year"}. Zoom in to separate them.`
        : `<strong>1 record</strong><br>${p.y || "Unknown year"}, ${basis}`;
      popup.setLngLat(e.lngLat).setHTML(html).addTo(m);
    };
    const enter = () => (m.getCanvas().style.cursor = "pointer");
    const leave = () => (m.getCanvas().style.cursor = "");
    m.on("click", "points", onClick);
    m.on("mouseenter", "points", enter);
    m.on("mouseleave", "points", leave);
    return () => {
      m.off("click", "points", onClick);
      m.off("mouseenter", "points", enter);
      m.off("mouseleave", "points", leave);
      popup.remove();
    };
  }, [ready]);

  return <div ref={box} className="map" role="region" aria-label="Map of Europe" />;
}
