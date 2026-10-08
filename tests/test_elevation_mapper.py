import pytest

from src.data.validator import GeoPoint
from src.engine.elevation_mapper import ElevationMapper, ElevationSample

P = GeoPoint(lat=13.75, lon=100.5)


def test_elevation_low_lying_factor():
    samples = [
        ElevationSample(location=GeoPoint(lat=13.75, lon=100.50), elevation_m=0.0),
        ElevationSample(location=GeoPoint(lat=13.76, lon=100.50), elevation_m=3.0),
        ElevationSample(location=GeoPoint(lat=13.74, lon=100.50), elevation_m=3.0),
        ElevationSample(location=GeoPoint(lat=13.75, lon=100.51), elevation_m=3.0),
    ]
    m = ElevationMapper(samples)
    assert m.elevation_at(P) == 0.0
    # local mean 2.25 m, point at 0 -> 2.25 / 3.0
    assert m.low_lying_factor(P, radius_km=2.0, scale_m=3.0) == pytest.approx(0.75)


def test_nan_coordinate_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GeoPoint(lat=float("nan"), lon=100)
