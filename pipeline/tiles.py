"""Static Mapbox Vector Tiles for the occurrence points, and Web Mercator PNG overlays for the model surfaces.

The live site has no server, so the occurrence layer is pre-cut into z/x/y.pbf tiles (zoom 0-6; the map
over-zooms beyond that). At low zooms, points that would land on the same pixel are merged per species,
which keeps the world-level tiles small. The API serves the same layer dynamically from PostGIS.
"""
from __future__ import annotations

import math
from pathlib import Path

import mapbox_vector_tile
import numpy as np
import pandas as pd
from PIL import Image

from . import climate

EXTENT = 4096
MAX_ZOOM = 6


def tile_coords(lon: np.ndarray, lat: np.ndarray, z: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Tile x/y and the position inside the tile (0..EXTENT, y down) for every point."""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = np.radians(np.clip(lat, -85.0511, 85.0511))
    y = (1.0 - np.log(np.tan(lat_r) + 1 / np.cos(lat_r)) / math.pi) / 2.0 * n
    tx, ty = np.floor(x).astype(int), np.floor(y).astype(int)
    px = np.floor((x - tx) * EXTENT).astype(int)
    py = np.floor((y - ty) * EXTENT).astype(int)
    return tx, ty, px, py


def write_tiles(points: pd.DataFrame, out: Path, layer: str = "occurrences", bbox=None) -> int:
    """points: columns lon, lat, species (int index), year, basis. Returns the number of tiles written.
    With bbox, every tile over it exists, empty where there are no records, so the map gets no 404s."""
    written = 0
    if bbox is not None:
        lon0, lat0, lon1, lat1 = bbox
        empty = mapbox_vector_tile.encode([{"name": layer, "features": []}], default_options={"extents": EXTENT})
        for z in range(MAX_ZOOM + 1):
            (x_west, x_east), (y_south, y_north), _, _ = tile_coords(np.array([lon0, lon1]), np.array([lat0, lat1]), z)
            for x in range(x_west, x_east + 1):
                for y in range(y_north, y_south + 1):
                    path = out / str(z) / str(x) / f"{y}.pbf"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(empty)
    for z in range(MAX_ZOOM + 1):
        tx, ty, px, py = tile_coords(points.lon.to_numpy(), points.lat.to_numpy(), z)
        df = points.assign(tx=tx, ty=ty, px=px, py=py)
        if z < MAX_ZOOM:
            # below the last zoom, merge points of a species that fall on the same ~2 screen pixels
            # (a 256 px tile is drawn from 4096 units, so 32 units = 2 px)
            q = 32
            df = df.assign(px=df.px // q * q + q // 2, py=df.py // q * q + q // 2)
            df = (df.groupby(["tx", "ty", "px", "py", "species"], as_index=False)
                    .agg(n=("lon", "size"), year=("year", "max"), basis=("basis", "first")))
        else:
            df = df.assign(n=1)
        for (x, y), part in df.groupby(["tx", "ty"]):
            features = [{"geometry": f"POINT({r.px} {r.py})",
                         "properties": {"s": int(r.species), "n": int(r.n), "y": int(r.year) if pd.notna(r.year) else 0,
                                        "b": str(r.basis)}}
                        for r in part.itertuples()]
            data = mapbox_vector_tile.encode([{"name": layer, "features": features}],
                                             default_options={"extents": EXTENT, "y_coord_down": True})
            path = out / str(z) / str(x) / f"{y}.pbf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            written += 1
    return written


# Single-hue sequential ramps, light to dark. Suitability uses heather; climate predictors use slate so the two
# kinds of layer are never confused. Low suitability fades to transparent so the base map shows through.
HEATHER = [(0.0, (241, 230, 242)), (0.25, (214, 180, 218)), (0.5, (176, 125, 184)), (0.75, (132, 76, 147)), (1.0, (85, 36, 106))]
SLATE = [(0.0, (232, 238, 240)), (0.25, (184, 202, 208)), (0.5, (128, 157, 168)), (0.75, (77, 111, 124)), (1.0, (40, 66, 78))]
STOPS = HEATHER


def colour(values: np.ndarray, lo: float, hi: float, fade: bool, stops=HEATHER) -> np.ndarray:
    t = np.nan_to_num(np.clip((values - lo) / (hi - lo or 1), 0, 1))
    rgba = np.zeros(values.shape + (4,), dtype=np.uint8)
    xs = [s[0] for s in stops]
    for c in range(3):
        rgba[..., c] = np.interp(t, xs, [s[1][c] for s in stops]).astype(np.uint8)
    alpha = np.where(np.isfinite(values), 215, 0)
    if fade:  # low suitability fades out
        alpha = np.where(np.isfinite(values), np.clip(40 + t * 400, 0, 215), 0)
    rgba[..., 3] = alpha.astype(np.uint8)
    return rgba


def to_mercator_rows(arr: np.ndarray, grid: climate.Grid, height: int) -> np.ndarray:
    """Resample a lat/lon grid to rows evenly spaced in Web Mercator, so a MapLibre image source lines up."""
    lon0, lat0, lon1, lat1 = grid.bounds

    def merc(lat):
        return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))

    ys = np.linspace(merc(lat1), merc(lat0), height)
    lats = np.degrees(2 * np.arctan(np.exp(ys)) - np.pi / 2)
    rows = np.clip(((lat1 - lats) / grid.res).astype(int), 0, arr.shape[0] - 1)
    return arr[rows]


def write_overlay(arr: np.ndarray, grid: climate.Grid, path, lo: float, hi: float, fade: bool = False,
                  stops=HEATHER) -> None:
    """path: a file path or a binary file object."""
    merc = to_mercator_rows(arr, grid, height=arr.shape[0] * 2)
    img = Image.fromarray(colour(merc, lo, hi, fade, stops), "RGBA")
    img = img.resize((arr.shape[1] * 2, merc.shape[0]), Image.NEAREST)
    if isinstance(path, Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True)
