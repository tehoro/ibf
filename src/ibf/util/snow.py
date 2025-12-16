"""
Snow level estimation utilities based on wet-bulb zero diagnostics.

Adapted from the Weather MCP Server helper, this module exposes functions for
computing wet-bulb temperatures and estimating snow levels from vertical
profiles (pressure, temperature, RH, geopotential height).
"""

from __future__ import annotations

import logging
import math
from typing import Iterable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Thermodynamic constants
Rd = 287.05  # J/kg/K
Rv = 461.5
cpd = 1004.0
cpv = 1850.0
eps = Rd / Rv


def Lv(Tk: float) -> float:
    """Latent heat of vaporization (J/kg) with linearized temperature dependence."""
    return 2.501e6 - 2361.0 * (Tk - 273.15)


def esat_pa(Tc: float) -> float:
    """Saturation vapor pressure over liquid water (Pa)."""
    return 611.2 * math.exp((17.67 * Tc) / (Tc + 243.5))


def inv_esat_to_TdC(e_pa: float) -> float:
    """Return dewpoint (°C) from vapor pressure (Pa)."""
    e_hpa = e_pa / 100.0
    lnratio = math.log(e_hpa / 6.112)
    return (243.5 * lnratio) / (17.67 - lnratio)


def sat_mixing_ratio(p_pa: float, Tc: float) -> float:
    """Saturation mixing ratio (kg/kg) at pressure p and temperature T."""
    e = esat_pa(Tc)
    return eps * e / (p_pa - e)


def mixing_ratio_from_rh(p_pa: float, Tc: float, rh_pct: float) -> float:
    """Mixing ratio r (kg/kg) from temperature, RH (%), and pressure."""
    e = (rh_pct / 100.0) * esat_pa(Tc)
    return eps * e / (p_pa - e)


def rh_from_T_Td(Tc: float, Tdc: float) -> float:
    """Compute RH% from temperature and dewpoint (both Celsius)."""
    e = esat_pa(Tdc)
    es = esat_pa(Tc)
    return max(0.0, min(100.0, 100.0 * e / es))


def moist_enthalpy_per_kg_dry(Tk: float, r: float) -> float:
    """Moist enthalpy (per kg of dry air)."""
    return cpd * Tk + r * (cpv * Tk + Lv(Tk))


def wet_bulb_dj(Tc: float, rh_pct: float, p_pa: float, tol: float = 1e-3) -> float:
    """
    Wet-bulb temperature (°C) using an enthalpy balance approach (Davies–Jones style).
    """
    Tk = Tc + 273.15
    r = mixing_ratio_from_rh(p_pa, Tc, rh_pct)

    e = (rh_pct / 100.0) * esat_pa(Tc)
    Td = inv_esat_to_TdC(e)
    Tw_low = Td
    Tw_high = Tc

    if abs(rh_pct - 100.0) < 1e-6:
        return Tc

    Tw_lo_K = Tw_low + 273.15
    Tw_hi_K = Tw_high + 273.15
    h_parcel = moist_enthalpy_per_kg_dry(Tk, r)

    def f(TwK: float) -> float:
        rsw = sat_mixing_ratio(p_pa, TwK - 273.15)
        return h_parcel - moist_enthalpy_per_kg_dry(TwK, rsw)

    f_lo = f(Tw_lo_K)
    f_hi = f(Tw_hi_K)
    if f_lo < 0:
        Tw_lo_K = max(180.0, Tw_lo_K - 0.5)
        f_lo = f(Tw_lo_K)
    if f_hi > 0:
        Tw_hi_K = Tw_hi_K + 0.5
        f_hi = f(Tw_hi_K)

    for _ in range(60):
        Tw_mid = 0.5 * (Tw_lo_K + Tw_hi_K)
        f_mid = f(Tw_mid)
        if abs(f_mid) < 1e-6 or (Tw_hi_K - Tw_lo_K) < tol:
            return Tw_mid - 273.15
        if f_mid > 0:
            Tw_lo_K = Tw_mid
        else:
            Tw_hi_K = Tw_mid

    return 0.5 * (Tw_lo_K + Tw_hi_K) - 273.15


def estimate_snow_level_msl(
    *,
    z_station_m: float,
    p_station_pa: float,
    t2m_c: float,
    td2m_c: float,
    pressures_hpa: Iterable[float],
    temps_c: Iterable[float],
    rhs_pct: Iterable[float],
    geop_heights_m: Iterable[float],
    wb_target_c: float = 0.5,
    precip_rate_mm_per_hr: Optional[float] = None,
    apply_precip_adjustment: bool = True,
) -> float:
    """
    Estimate snow-level height above mean sea level (m) using the wet-bulb zero method.
    """
    p_arr = np.asarray(pressures_hpa, dtype=float) * 100.0
    T_arr = np.asarray(temps_c, dtype=float)
    RH_arr = np.asarray(rhs_pct, dtype=float)
    Z_arr = np.asarray(geop_heights_m, dtype=float)

    if not (len(p_arr) == len(T_arr) == len(RH_arr) == len(Z_arr)):
        raise ValueError("Profile arrays must have equal length.")

    rh2m = rh_from_T_Td(t2m_c, td2m_c)
    surface = {
        "z": z_station_m,
        "Tw": wet_bulb_dj(t2m_c, rh2m, p_station_pa),
    }

    prof_Tw = np.array(
        [wet_bulb_dj(float(T), float(RH), float(p)) for T, RH, p in zip(T_arr, RH_arr, p_arr)]
    )

    z_all = np.concatenate([[surface["z"]], Z_arr])
    Tw_all = np.concatenate([[surface["Tw"]], prof_Tw])
    order = np.argsort(z_all)
    z_all = z_all[order]
    Tw_all = Tw_all[order]

    if Tw_all[0] <= 0.0:
        snow_level = z_all[0]
    else:
        target = wb_target_c
        crossing_z = np.nan
        for k in range(len(z_all) - 1):
            y0 = Tw_all[k] - target
            y1 = Tw_all[k + 1] - target
            if y0 == 0.0:
                crossing_z = z_all[k]
                break
            if y0 * y1 <= 0.0:
                z0, z1 = z_all[k], z_all[k + 1]
                crossing_z = z0 + (target - Tw_all[k]) * (z1 - z0) / (Tw_all[k + 1] - Tw_all[k])
                break
        snow_level = crossing_z

    if apply_precip_adjustment and np.isfinite(snow_level):
        adj = 0.0
        if precip_rate_mm_per_hr is not None:
            r = float(precip_rate_mm_per_hr)
            if r >= 20.0:
                adj = 300.0
            elif r >= 10.0:
                adj = 200.0
            elif r >= 5.0:
                adj = 100.0
        snow_level = max(z_station_m, snow_level - adj)

    return float(snow_level) if np.isfinite(snow_level) else float("nan")


def should_check_snow_level(precipitation_mm: float, weather_code: int, temperature_c: float) -> bool:
    """
    Return True if conditions warrant a snow-level calculation.

    This mirrors the logic from the Weather MCP Server:
      - precip > 0
      - weather code not already a freezing/snow type
      - temperature < 15C
    """
    freezing_codes = {56, 57, 66, 67, 71, 73, 75, 77, 85, 86}
    return (
        precipitation_mm > 0
        and weather_code not in freezing_codes
        and temperature_c < 15.0
    )


def extract_pressure_profile(
    hourly_data: dict,
    index: int,
    *,
    pressure_levels_hpa: Iterable[float],
    surface_pressure_hpa: float,
    temperature_prefix: str = "temperature_{level}hPa",
    humidity_prefix: str = "relative_humidity_{level}hPa",
    geopotential_prefix: str = "geopotential_height_{level}hPa",
) -> Optional[dict[str, list[float]]]:
    """
    Extract temperature/RH/geopotential arrays for each pressure level at the given index.

    Returns None if any data is missing.
    """
    temps: list[Optional[float]] = []
    rhs: list[Optional[float]] = []
    geop: list[Optional[float]] = []

    for level in pressure_levels_hpa:
        temp_key = temperature_prefix.format(level=int(level))
        rh_key = humidity_prefix.format(level=int(level))
        geo_key = geopotential_prefix.format(level=int(level))

        temp_series = hourly_data.get(temp_key, [])
        rh_series = hourly_data.get(rh_key, [])
        geo_series = hourly_data.get(geo_key, [])

        temps.append(temp_series[index] if index < len(temp_series) else None)
        rhs.append(rh_series[index] if index < len(rh_series) else None)
        geop.append(geo_series[index] if index < len(geo_series) else None)

    if any(value is None for value in (*temps, *rhs, *geop)):
        return None

    return {
        "surface_pressure_hpa": surface_pressure_hpa,
        "pressures_hpa": list(pressure_levels_hpa),
        "temps_c": [float(v) for v in temps],
        "rhs_pct": [float(v) for v in rhs],
        "geop_heights_m": [float(v) for v in geop],
    }


def compute_hourly_snow_level(
    *,
    precipitation_mm: float,
    weather_code: int,
    temperature_c: float,
    dewpoint_c: float,
    location_elevation_m: float,
    surface_pressure_hpa: float,
    pressure_profile: dict[str, list[float]],
    units: str = "metric",
    max_terrain_m: Optional[float] = None,
    precip_adjust: bool = True,
) -> int:
    """
    High-level helper that applies all decision logic, runs estimate_snow_level_msl,
    and returns a rounded/filtered snow level suitable for display.

    Returns -1 if not applicable or filtered out.
    """
    if not should_check_snow_level(precipitation_mm, weather_code, temperature_c):
        return -1

    snow_level_m = estimate_snow_level_msl(
        z_station_m=location_elevation_m,
        p_station_pa=surface_pressure_hpa * 100.0,
        t2m_c=temperature_c,
        td2m_c=dewpoint_c,
        pressures_hpa=pressure_profile["pressures_hpa"],
        temps_c=pressure_profile["temps_c"],
        rhs_pct=pressure_profile["rhs_pct"],
        geop_heights_m=pressure_profile["geop_heights_m"],
        precip_rate_mm_per_hr=precipitation_mm,
        apply_precip_adjustment=precip_adjust,
    )

    if not math.isfinite(snow_level_m) or snow_level_m > 3000.0:
        return -1

    if max_terrain_m is not None:
        terrain_threshold = max_terrain_m - 300.0
        if snow_level_m > terrain_threshold:
            return -1
        if snow_level_m > location_elevation_m + 1200.0:
            return -1

    if units == "us":
        return int(round((snow_level_m * 3.28084) / 500.0) * 500)
    return int(round(snow_level_m / 100.0) * 100)


__all__ = [
    "estimate_snow_level_msl",
    "wet_bulb_dj",
    "rh_from_T_Td",
    "sat_mixing_ratio",
    "should_check_snow_level",
    "extract_pressure_profile",
    "compute_hourly_snow_level",
]
