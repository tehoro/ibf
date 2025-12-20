"""
Terrain lookup using a compressed global GMTED2010 dataset with H3 interpolation.
"""

from __future__ import annotations

import gzip
import logging
import math
import pickle
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import h3
import numpy as np

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TERRAIN_DB = REPO_ROOT / "assets" / "terrain" / "global_terrain_ultra_compressed.pkl.gz"


class TerrainLookup:
    """Load and query the compressed terrain dataset."""

    ELEVATION_SCALE = 30.0  # meters per unit

    def __init__(self, db_path: Path = DEFAULT_TERRAIN_DB) -> None:
        self.db_path = db_path
        self.h3_resolution: Optional[int] = None
        self.elevation_data: Optional[np.ndarray] = None
        self.h3_lookup: Optional[dict] = None
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Terrain database not found at {self.db_path}. "
                "Please add 'global_terrain_ultra_compressed.pkl.gz' under assets/terrain."
            )
        with gzip.open(self.db_path, "rb") as handle:
            data = pickle.load(handle)
        self.h3_resolution = data["h3_resolution"]
        self.elevation_data = np.array(data["elevation_data"], dtype=np.uint8)
        self.h3_lookup = data["h3_lookup"]
        self._loaded = True
        logger.info(
            "Loaded terrain dataset (%s cells, H3 resolution %s)",
            len(self.elevation_data),
            self.h3_resolution,
        )

    def _byte_to_elevation(self, value: int) -> Optional[float]:
        if value == 0:
            return None
        return float(value) * self.ELEVATION_SCALE

    def get_elevation(self, latitude: float, longitude: float) -> Optional[float]:
        self._load()
        assert self.h3_resolution is not None and self.h3_lookup is not None and self.elevation_data is not None

        h3_index = h3.latlng_to_cell(latitude, longitude, self.h3_resolution)
        if h3_index in self.h3_lookup:
            idx = self.h3_lookup[h3_index]
            return self._byte_to_elevation(int(self.elevation_data[idx]))
        return self._interpolate(latitude, longitude, h3_index)

    def get_elevations(self, coordinates: Sequence[Tuple[float, float]]) -> List[Optional[float]]:
        return [self.get_elevation(lat, lon) for lat, lon in coordinates]

    def _interpolate(self, latitude: float, longitude: float, center_h3: str) -> Optional[float]:
        assert self.h3_resolution is not None and self.h3_lookup is not None and self.elevation_data is not None
        neighbors = h3.grid_disk(center_h3, 1)
        elevations: List[float] = []
        weights: List[float] = []

        for cell in neighbors:
            lookup_idx = self.h3_lookup.get(cell)
            if lookup_idx is None:
                continue
            value = self._byte_to_elevation(int(self.elevation_data[lookup_idx]))
            if value is None:
                continue
            cell_lat, cell_lon = h3.cell_to_latlng(cell)
            distance = _approx_distance(latitude, longitude, cell_lat, cell_lon)
            weight = 1.0 / (distance + 0.001)
            elevations.append(value)
            weights.append(weight)

        if not elevations:
            return self._nearest(latitude, longitude, center_h3)
        if len(elevations) == 1:
            return elevations[0]
        total_weight = sum(weights)
        if total_weight == 0:
            return elevations[0]
        return sum(e * w for e, w in zip(elevations, weights)) / total_weight

    def _nearest(self, latitude: float, longitude: float, center_h3: str) -> Optional[float]:
        assert self.h3_resolution is not None and self.h3_lookup is not None and self.elevation_data is not None
        for ring in range(1, 4):
            for neighbor in h3.grid_ring(center_h3, ring):
                idx = self.h3_lookup.get(neighbor)
                if idx is None:
                    continue
                value = self._byte_to_elevation(int(self.elevation_data[idx]))
                if value is not None:
                    return value
        return None


_LOOKUP: Optional[TerrainLookup] = None


def _get_lookup() -> Optional[TerrainLookup]:
    global _LOOKUP
    if _LOOKUP is None:
        _LOOKUP = TerrainLookup()
    try:
        _LOOKUP._load()
    except FileNotFoundError as exc:
        logger.warning("%s", exc)
        return None
    return _LOOKUP


def get_highest_point(latitude: float, longitude: float, radius_km: int = 50) -> float:
    """
    Estimate the peak terrain elevation (meters) within `radius_km` of the point.
    Returns float("inf") if the terrain database is unavailable.
    """
    lookup = _get_lookup()
    if lookup is None:
        return float("inf")

    if radius_km <= 0:
        value = lookup.get_elevation(latitude, longitude)
        return value if value is not None else float("inf")

    samples = _points_in_radius(latitude, longitude, radius_km)
    elevations = lookup.get_elevations(samples)
    valid = [value for value in elevations if value is not None]
    if not valid:
        return float("inf")
    return max(valid)


def _points_in_radius(lat: float, lon: float, radius_km: int) -> List[Tuple[float, float]]:
    lat_per_km = 1.0 / 111.0
    cos_lat = math.cos(math.radians(lat))
    lon_per_km = lat_per_km if cos_lat == 0 else 1.0 / (111.0 * cos_lat)
    lat_step = radius_km * lat_per_km / 2
    lon_step = radius_km * lon_per_km / 2

    points: List[Tuple[float, float]] = []
    for i in range(-2, 3):
        for j in range(-2, 3):
            check_lat = lat + i * lat_step
            check_lon = lon + j * lon_step
            if -90 <= check_lat <= 90 and -180 <= check_lon <= 180:
                points.append((check_lat, check_lon))
    return points


def _approx_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2)
