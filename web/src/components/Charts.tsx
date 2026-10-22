import { useState } from "react";
import { MODEL_LABEL, type ModelName, type ModelResult, type Predictor } from "../data";

const MODELS: ModelName[] = ["glm", "rf"];

export function ModelLegend() {
  return (
    <div className="legend-models" aria-label="Legend">
      {MODELS.map((m) => (
        <span key={m}>
          <i className={`swatch ${m}`} aria-hidden="true" />
          {MODEL_LABEL[m]}
        </span>
      ))}
    </div>
  );
}

// One small chart per predictor: predicted suitability as that predictor changes, the others at their median.
export function ResponseCurve({ predictor, data }: { predictor: Predictor; data: ModelResult["response"][string] }) {
  const W = 220, H = 96, P = { l: 30, r: 8, t: 8, b: 22 };
  const [hover, setHover] = useState<number | null>(null);
  const xs = data.x;
  const x0 = xs[0], x1 = xs[xs.length - 1];
  const sx = (v: number) => P.l + ((v - x0) / (x1 - x0 || 1)) * (W - P.l - P.r);
  const sy = (v: number) => H - P.b - v * (H - P.t - P.b);
  const path = (m: ModelName) => data[m].map((v, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(v).toFixed(1)}`).join("");
  const unit = predictor.label.match(/\(([^)]+)\)/)?.[1] ?? "";
  const name = predictor.label.replace(/\s*\([^)]*\)/, "");

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const v = x0 + ((e.clientX - r.left) / r.width) * (x1 - x0);
    let best = 0;
    xs.forEach((x, i) => { if (Math.abs(x - v) < Math.abs(xs[best] - v)) best = i; });
    setHover(best);
  };

  return (
    <figure className="curve">
      <figcaption>{name}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Response of suitability to ${name}`}>
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line className="grid" x1={P.l} x2={W - P.r} y1={sy(t)} y2={sy(t)} />
            <text className="tick" x={P.l - 5} y={sy(t) + 3.5} textAnchor="end">{t}</text>
          </g>
        ))}
        <text className="tick" x={P.l} y={H - 6}>{Math.round(x0)} {unit}</text>
        <text className="tick" x={W - P.r} y={H - 6} textAnchor="end">{Math.round(x1)} {unit}</text>
        {MODELS.map((m) => <path key={m} className={`line ${m}`} d={path(m)} />)}
        {hover !== null && (
          <>
            <line className="crosshair" x1={sx(xs[hover])} x2={sx(xs[hover])} y1={P.t} y2={H - P.b} />
            {MODELS.map((m) => <circle key={m} className={`dot ${m}`} cx={sx(xs[hover])} cy={sy(data[m][hover])} r={4} />)}
          </>
        )}
        <rect x={P.l} y={0} width={W - P.l - P.r} height={H - P.b} fill="transparent"
              onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>
      {hover !== null && (
        <div className="tip" style={{ left: `${(sx(xs[hover]) / W) * 100}%` }}>
          <b>{xs[hover]} {unit}</b>
          {MODELS.map((m) => <span key={m}>{MODEL_LABEL[m]} {data[m][hover].toFixed(2)}</span>)}
        </div>
      )}
    </figure>
  );
}

// How much the cross-validated fit drops when one predictor is shuffled
export function Importance({ result, predictors }: { result: ModelResult; predictors: Predictor[] }) {
  const max = Math.max(0.001, ...MODELS.flatMap((m) => Object.values(result.importance[m])));
  const [hover, setHover] = useState<string | null>(null);
  const order = [...predictors].sort((a, b) => result.importance.rf[b.id] - result.importance.rf[a.id]);
  return (
    <div className="importance" role="table" aria-label="Predictor importance">
      {order.map((p) => (
        <div className="imp-row" role="row" key={p.id} onPointerEnter={() => setHover(p.id)} onPointerLeave={() => setHover(null)}>
          <span role="rowheader" className="imp-name">{p.label.replace(/\s*\([^)]*\)/, "")}</span>
          <span role="cell" className="imp-bars">
            {MODELS.map((m) => (
              <span key={m} className={`bar ${m}`} style={{ width: `${Math.max(0, result.importance[m][p.id]) / max * 100}%` }} />
            ))}
          </span>
          {hover === p.id && (
            <span className="imp-tip">
              {MODELS.map((m) => `${MODEL_LABEL[m]} −${result.importance[m][p.id].toFixed(3)} AUC`).join(", ")}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
