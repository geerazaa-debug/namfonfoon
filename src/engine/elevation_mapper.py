"""Ground elevation lookup and low-lying factor from sampled DEM points.

Samples would come from a DEM (e.g. SRTM / Copernicus GLO-30). Interpolation is
inverse-distance weighting over the k nearest samples - adequate for a coarse grid,
not a substitute for proper raster sampling.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from src.data.validator import GeoPoint
from src.engine.geofence import haversine_km


class ElevationSample(BaseModel):
    location: GeoPoint
    elevation_m: float


class ElevationMapper:
    def __init__(self, samples: Sequence[ElevationSample], k: int = 4) -> None:
        if not samples:
            raise ValueError("ElevationMapper needs at least one sample")
        self.samples = list(samples)
        self.k = k

    def elevation_at(self, p: GeoPoint) -> float:
        dists = sorted(((haversine_km(p, s.location), s.elevation_m) for s in self.samples), key=lambda x: x[0])
        nearest = dists[: self.k]
        if nearest[0][0] < 1e-6:
            return nearest[0][1]
        weights = [1 / d**2 for d, _ in nearest]
        return sum(w * e for w, (_, e) in zip(weights, nearest, strict=False)) / sum(weights)

    def local_mean(self, p: GeoPoint, radius_km: float) -> float | None:
        local = [s.elevation_m for s in self.samples if haversine_km(p, s.location) <= radius_km]
        return sum(local) / len(local) if local else None

    def low_lying_factor(self, p: GeoPoint, radius_km: float = 2.0, scale_m: float = 3.0) -> float:
        """0 = at/above surrounding mean, 1 = scale_m or more below it."""
        mean = self.local_mean(p, radius_km)
        if mean is None:
            return 0.0
        return max(0.0, min(1.0, (mean - self.elevation_at(p)) / scale_m))
