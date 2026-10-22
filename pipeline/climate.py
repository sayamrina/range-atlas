"""WorldClim 2.1 bioclimatic predictors (10 arc-minutes), downloaded once and cropped to the study area."""
from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass

import numpy as np
import rasterio
import requests
from rasterio.windows import from_bounds

from . import config

log = logging.getLogger(__name__)


@dataclass
class Grid:
    """Predictor stack on a regular lon/lat grid. Row 0 is the northern edge."""
    data: dict[str, np.ndarray]      # name -> 2-D float32 array, NaN where there is no land
    lon0: float
    lat1: float
    res: float

    @property
    def shape(self) -> tuple[int, int]:
        return next(iter(self.data.values())).shape

    @property
    def land(self) -> np.ndarray:
        return np.all([np.isfinite(a) for a in self.data.values()], axis=0)

    def cell(self, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        row = np.floor((self.lat1 - np.asarray(lat)) / self.res).astype(int)
        col = np.floor((np.asarray(lon) - self.lon0) / self.res).astype(int)
        rows, cols = self.shape
        ok = (row >= 0) & (row < rows) & (col >= 0) & (col < cols)
        return np.where(ok, row, -1), np.where(ok, col, -1)

    def sample(self, lon: np.ndarray, lat: np.ndarray) -> dict[str, np.ndarray]:
        row, col = self.cell(lon, lat)
        ok = row >= 0
        out = {}
        for name, arr in self.data.items():
            v = np.full(len(row), np.nan, dtype="float32")
            v[ok] = arr[row[ok], col[ok]]
            out[name] = v
        return out

    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        rows, cols = self.shape
        lon = self.lon0 + (np.arange(cols) + 0.5) * self.res
        lat = self.lat1 - (np.arange(rows) + 0.5) * self.res
        return np.meshgrid(lon, lat)

    @property
    def bounds(self) -> list[float]:
        rows, cols = self.shape
        return [self.lon0, self.lat1 - rows * self.res, self.lon0 + cols * self.res, self.lat1]


def tif_name(predictor: str) -> str:
    return f"wc2.1_10m_{predictor.replace('bio', 'bio_')}.tif"  # bio1 -> wc2.1_10m_bio_1.tif


def download() -> None:
    target = config.CACHE / "worldclim"
    if all((target / tif_name(p)).exists() for p in config.PREDICTORS):
        return
    target.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s", config.WORLDCLIM_URL)
    r = requests.get(config.WORLDCLIM_URL, timeout=600)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for p in config.PREDICTORS:
            (target / tif_name(p)).write_bytes(z.read(tif_name(p)))


def load() -> Grid:
    cached = config.CACHE / "worldclim" / "europe.npz"
    if cached.exists():
        z = np.load(cached)
        return Grid({p: z[p] for p in config.PREDICTORS}, float(z["lon0"]), float(z["lat1"]), float(z["res"]))
    download()
    data = {}
    for p in config.PREDICTORS:
        with rasterio.open(config.CACHE / "worldclim" / tif_name(p)) as src:
            win = from_bounds(*config.BBOX, transform=src.transform).round_offsets().round_lengths()
            arr = src.read(1, window=win).astype("float32")
            if src.nodata is not None:
                arr[arr == src.nodata] = np.nan
            arr[arr < -1e30] = np.nan
            t = src.window_transform(win)
            data[p] = arr
    grid = Grid(data, t.c, t.f, t.a)
    np.savez_compressed(cached, lon0=grid.lon0, lat1=grid.lat1, res=grid.res, **data)
    return grid
