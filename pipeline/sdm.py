"""Species distribution models: a GLM and a Random Forest on presence vs. target-group background.

Target-group background: the background points are places where *any* grasshopper or cricket was recorded,
not random points. They share the observers' bias (roads, towns, well-surveyed countries), so the model
learns climate preferences rather than where people go looking.

Evaluation uses spatial block cross-validation: 5°×5° blocks are assigned to five folds, so a model is
always tested on regions it has not seen. Random splits would test on neighbours of training points and
overstate accuracy.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from . import climate, config, lake

log = logging.getLogger(__name__)
PREDICTORS = list(config.PREDICTORS)
BLOCK_DEG = 5
FOLDS = 5


def add_squares(x: np.ndarray) -> np.ndarray:
    return np.hstack([x, x ** 2])


def models() -> dict:
    return {
        "glm": make_pipeline(StandardScaler(), FunctionTransformer(add_squares),
                             LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000)),
        "rf": RandomForestClassifier(n_estimators=300, min_samples_leaf=5, max_features=2,
                                     class_weight="balanced_subsample", n_jobs=-1, random_state=0),
    }


def spatial_folds(lon: np.ndarray, lat: np.ndarray, seed: int = 0) -> np.ndarray:
    block = (np.floor(lon / BLOCK_DEG).astype(int) * 1000 + np.floor(lat / BLOCK_DEG).astype(int))
    blocks = np.unique(block)
    rng = np.random.default_rng(seed)
    fold_of = dict(zip(blocks, rng.permutation(len(blocks)) % FOLDS))
    return np.array([fold_of[b] for b in block])


def tss_at_best(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    fpr, tpr, thr = roc_curve(y, p)
    i = int(np.argmax(tpr - fpr))
    return float(tpr[i] - fpr[i]), float(thr[i])


def training_table(slug: str) -> pd.DataFrame:
    con = lake.connect()
    df = con.sql(f"""
        SELECT species, decimalLongitude AS lon, decimalLatitude AS lat, cell, {", ".join(PREDICTORS)}
        FROM read_parquet('{lake.glob('features')}', hive_partitioning = true)
        WHERE species IN ('{slug}', 'orthoptera-background')
    """).df()
    pres = df[df.species == slug].assign(y=1)
    # background cells that also hold a presence stay in: target-group background is a sample of the
    # surveyed landscape, presences included
    bg = df[df.species == "orthoptera-background"].assign(y=0)
    return pd.concat([pres, bg], ignore_index=True)


def response_curves(fitted: dict, X: pd.DataFrame) -> dict:
    """Predicted suitability along each predictor, the others held at their median."""
    out = {}
    med = X.median()
    for p in PREDICTORS:
        xs = np.linspace(X[p].quantile(0.01), X[p].quantile(0.99), 40)
        grid = pd.DataFrame([med] * len(xs))
        grid[p] = xs
        out[p] = {"x": [round(float(v), 2) for v in xs]}
        for name, m in fitted.items():
            out[p][name] = [round(float(v), 4) for v in m.predict_proba(grid[PREDICTORS])[:, 1]]
    return out


def run_species(slug: str, grid: climate.Grid | None = None) -> dict:
    t0 = time.time()
    grid = grid or climate.load()
    df = training_table(slug)
    n_pres = int(df.y.sum())
    if n_pres < 30:
        raise ValueError(f"{slug}: only {n_pres} presence cells after thinning, too few to model")
    X, y = df[PREDICTORS], df.y.to_numpy()
    folds = spatial_folds(df.lon.to_numpy(), df.lat.to_numpy())

    metrics: dict[str, dict] = {}
    oof = {}
    for name, model in models().items():
        pred = np.full(len(y), np.nan)
        for k in range(FOLDS):
            test = folds == k
            if y[test].min() == y[test].max() or y[~test].min() == y[~test].max():
                continue  # a fold without both classes cannot be scored
            pred[test] = clone(model).fit(X[~test], y[~test]).predict_proba(X[test])[:, 1]
        ok = ~np.isnan(pred)
        oof[name] = pred
        tss, thr = tss_at_best(y[ok], pred[ok])
        metrics[name] = {"auc": round(float(roc_auc_score(y[ok], pred[ok])), 3), "tss": round(tss, 3),
                         "threshold": round(thr, 3)}

    fitted = {name: clone(m).fit(X, y) for name, m in models().items()}
    importance = {}
    for name, m in fitted.items():
        imp = permutation_importance(m, X, y, scoring="roc_auc", n_repeats=3, random_state=0, n_jobs=1)
        importance[name] = {p: round(float(v), 4) for p, v in zip(PREDICTORS, imp.importances_mean)}

    land = grid.land
    stack = np.column_stack([grid.data[p][land] for p in PREDICTORS])
    Xg = pd.DataFrame(stack, columns=PREDICTORS)
    maps = {}
    for name, m in fitted.items():
        surface = np.full(grid.shape, np.nan, dtype="float32")
        surface[land] = m.predict_proba(Xg)[:, 1]
        maps[name] = surface
    maps["ensemble"] = np.full(grid.shape, np.nan, dtype="float32")
    maps["ensemble"][land] = (maps["glm"][land] + maps["rf"][land]) / 2

    path = lake.uri("models", f"species={slug}", "suitability.npz")
    fs = lake.fs_for(path)
    fs.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
    with fs.open(path, "wb") as f:
        np.savez_compressed(f, **maps)

    result = {
        "species": slug, "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1), "presence_cells": n_pres, "background_cells": int(len(y) - n_pres),
        "folds": FOLDS, "block_degrees": BLOCK_DEG, "metrics": metrics, "importance": importance,
        "response": response_curves(fitted, X),
    }
    lake.write_json(result, f"models/species={slug}/result.json")
    log.info("%s: GLM AUC %.3f, RF AUC %.3f (%s presences)", slug, metrics["glm"]["auc"], metrics["rf"]["auc"], n_pres)
    return result


def run() -> dict:
    grid = climate.load()
    metrics = [run_species(sp.slug, grid)["metrics"] for sp in config.SPECIES if sp.model]
    return {"species_modelled": len(metrics),
            "median_auc_glm": round(float(np.median([m["glm"]["auc"] for m in metrics])), 3),
            "median_auc_rf": round(float(np.median([m["rf"]["auc"] for m in metrics])), 3)}
