// Types and loaders for the files the pipeline exports to public/data, plus the optional live API.

export type Metrics = { auc: number; tss: number; threshold: number };
export type ModelName = "glm" | "rf";

export type Species = {
  index: number;
  slug: string;
  name: string;
  common: string;
  family: string;
  source: "gbif" | "obis";
  key: number;
  modelled: boolean;
  harvested: number;
  clean: number;
  cells: number;
  link: string;
  metrics?: Record<ModelName, Metrics>;
};

export type ModelResult = {
  species: string;
  finished: string;
  seconds: number;
  presence_cells: number;
  background_cells: number;
  folds: number;
  block_degrees: number;
  metrics: Record<ModelName, Metrics>;
  importance: Record<ModelName, Record<string, number>>;
  response: Record<string, { x: number[] } & Record<ModelName, number[]>>;
};

export type Predictor = { id: string; label: string; min: number; max: number };
export type Ramp = [number, [number, number, number]][];
export type Meta = {
  bounds: [number, number, number, number];
  predictors: Predictor[];
  years: [number, number];
  ramps: { suitability: Ramp; predictor: Ramp };
};

export type Quality = {
  species: string;
  source: string;
  harvested: number;
  passed: number;
  failed: Record<string, number>;
  repaired: Record<string, number>;
};

export type Dataset = { source: string; datasetKey: string; datasetName: string; licences: string[]; records: number };

export type RunStep = {
  name: string;
  started: string;
  status: "succeeded" | "failed";
  seconds: number;
  output?: Record<string, unknown>;
  error?: string;
};
export type PipelineRun = {
  id: string;
  started: string;
  finished: string;
  trigger: string;
  commit: string;
  status: "running" | "succeeded" | "failed";
  steps: RunStep[];
};

// A model run started through the API (only when VITE_API_URL is set)
export type ApiRun = {
  id: number;
  species: string;
  backend: string;
  status: "queued" | "running" | "succeeded" | "failed";
  created: string;
  started: string | null;
  finished: string | null;
  result: { metrics: Record<ModelName, Metrics>; presence_cells: number; seconds: number } | null;
  error: string | null;
};

export const BASE = import.meta.env.BASE_URL;
export const API = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") || null;

export const dataUrl = (path: string) => new URL(`${BASE}data/${path}`, window.location.origin).toString();

export async function load<T>(path: string): Promise<T> {
  const r = await fetch(dataUrl(path));
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json() as Promise<T>;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, { headers: { "content-type": "application/json" }, ...init });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body as { detail?: string }).detail ?? `${path}: ${r.status}`);
  return body as T;
}

export const MODEL_LABEL: Record<ModelName | "ensemble", string> = {
  glm: "GLM",
  rf: "Random Forest",
  ensemble: "Average of both",
};

export const fmt = new Intl.NumberFormat("en-GB");

export function fmtDate(iso: string) {
  return new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function rampCss(ramp: Ramp) {
  return `linear-gradient(to right, ${ramp.map(([t, [r, g, b]]) => `rgb(${r} ${g} ${b}) ${t * 100}%`).join(", ")})`;
}

// Plain-language reading of a spatially cross-validated AUC
export function aucReading(auc: number) {
  if (auc >= 0.8) return "separates well";
  if (auc >= 0.7) return "separates fairly";
  if (auc >= 0.6) return "separates weakly";
  return "barely better than chance";
}
