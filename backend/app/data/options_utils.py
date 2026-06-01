"""
data/options_utils.py
---------------------
Shared helpers for options chain data.

  normalize_polygon_contract   raw Polygon v3 snapshot → 15-col dict
  calculate_atm_iv_by_tenor    chain DataFrame → {iv7, iv14, iv30, iv60, iv90}
  bs_price_and_greeks          Black-Scholes price + Greeks (call or put)

Both RealDataProvider (normalization + IV computation) and MockDataProvider
(BS synthetic chain) import from this module.

IV convention
-------------
All DataFrames produced by this project store ``implied_volatility`` as a
decimal fraction (0.25 = 25 %), matching the Polygon API native format.

``calculate_atm_iv_by_tenor`` converts to **percentage** on return so the
dict values are in the same unit as the ``_IV_SURFACE`` snapshot constants
used throughout the rest of the codebase (e.g. ``iv30: 15.8``).
"""

from __future__ import annotations

import datetime
import math
import re

import pandas as pd

# ---------------------------------------------------------------------------
# Schema — 15-column options chain
# ---------------------------------------------------------------------------

OPTION_COLUMNS: list[str] = [
    "underlying_ticker",   # str
    "expiration",          # ISO string "YYYY-MM-DD"
    "strike",              # float
    "option_type",         # "call" | "put"
    "bid",                 # float | None
    "ask",                 # float | None
    "mid",                 # float | None
    "last",                # float | None
    "implied_volatility",  # decimal fraction (0.25 = 25%)
    "delta",               # float | None
    "gamma",               # float | None
    "theta",               # float | None  (per calendar day, negative for long)
    "vega",                # float | None  (per 1 pct-point IV change = per 0.01 σ)
    "open_interest",       # int | None
    "volume",              # int | None
]

# Tenor breakpoints used for IV surface
_TENORS: list[tuple[str, int]] = [
    ("iv7",  7),
    ("iv14", 14),
    ("iv30", 30),
    ("iv60", 60),
    ("iv90", 90),
]

# Maximum |actual_dte − target_dte| before a tenor is rejected (set to None).
# Prevents stale IV from being assigned to the wrong part of the curve when
# expirations are sparse (e.g. the only available expiration is 35 DTE but
# the 60D and 90D tenors need 46–74 DTE and 69–111 DTE respectively).
TENOR_TOLERANCE: dict[str, int] = {
    "iv7":  4,
    "iv14": 7,
    "iv30": 10,
    "iv60": 14,
    "iv90": 21,
}

# ATM selection parameters.  Instead of a single nearest strike we take the
# median IV across up to N_ATM_STRIKES closest strikes within the moneyness
# band.  This prevents one bad Polygon model-IV contract from poisoning the
# entire tenor (e.g. QQQ June options where a single contract had IV ≈ 0.17%).
_N_ATM_STRIKES   = 5      # max strikes per option-type to include in median
_MONEYNESS_BAND  = 0.06   # ±6% of spot — wide enough for low-liquidity stocks

# Sanity bounds on decimal-fraction IV.  Anything outside is rejected as bad
# model output from Polygon (e.g. deeply ITM contracts, computation errors).
_IV_MIN_FRAC = 0.005   # 0.5 % — below this is model noise / zero-bid artefact
_IV_MAX_FRAC = 3.0     # 300 % — above this is clearly wrong


# ===========================================================================
# Polygon normalization
# ===========================================================================

def normalize_polygon_contract(raw: dict) -> dict:
    """
    Map a single Polygon v3 options snapshot contract to the 15-column schema.

    Parameters
    ----------
    raw : dict
        One element from the ``results`` list of a
        ``GET /v3/snapshot/options/{underlyingAsset}`` response.

    Returns
    -------
    dict
        Keys matching ``OPTION_COLUMNS``.  Missing / null fields → ``None``.
    """
    details    = raw.get("details")          or {}
    greeks     = raw.get("greeks")           or {}
    last_quote = raw.get("last_quote")       or {}
    day        = raw.get("day")              or {}
    underlying = raw.get("underlying_asset") or {}

    bid = _flt(last_quote.get("bid"))
    ask = _flt(last_quote.get("ask"))
    mid = _flt(last_quote.get("midpoint"))
    if mid is None and bid is not None and ask is not None:
        mid = round((bid + ask) / 2.0, 4)

    last = _flt(day.get("close")) or _flt(day.get("last"))

    return {
        "underlying_ticker": _extract_underlying_ticker(raw),
        "expiration":        _extract_expiration(raw),
        "strike":            _flt(details.get("strike_price")),
        "option_type":       _normalise_contract_type(details.get("contract_type")),
        "bid":               bid,
        "ask":               ask,
        "mid":               mid,
        "last":              last,
        "implied_volatility": _flt(raw.get("implied_volatility")),
        "delta":             _flt(greeks.get("delta")),
        "gamma":             _flt(greeks.get("gamma")),
        "theta":             _flt(greeks.get("theta")),
        "vega":              _flt(greeks.get("vega")),
        "open_interest":     _int(raw.get("open_interest")),
        "volume":            _int(day.get("volume")),
    }


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _extract_underlying_ticker(raw: dict) -> str | None:
    """Primary: underlying_asset.ticker.  Fallback: parse option symbol."""
    u = (raw.get("underlying_asset") or {}).get("ticker")
    if u:
        return str(u)
    # Option symbol format: O:SPY240119C00456000 → SPY
    sym = str((raw.get("details") or {}).get("ticker") or "")
    if sym.startswith("O:"):
        m = re.match(r"O:([A-Z]+)\d", sym)
        if m:
            return m.group(1)
    return None


def _extract_expiration(raw: dict) -> str | None:
    exp = (raw.get("details") or {}).get("expiration_date") or raw.get("expiration_date")
    return str(exp) if exp else None


def _normalise_contract_type(raw) -> str | None:
    if raw is None:
        return None
    s = str(raw).lower().strip()
    if s in ("call", "c"):
        return "call"
    if s in ("put", "p"):
        return "put"
    return s or None


def _flt(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ===========================================================================
# ATM IV by tenor
# ===========================================================================

def calculate_atm_iv_by_tenor(
    chain: pd.DataFrame,
    underlying_price: float,
    pricing_date: datetime.date | None = None,
) -> dict[str, float | None]:
    """
    Calculate near-ATM implied volatility for five standard tenors.

    For each tenor T the function:

    1. Finds the expiration closest to ``pricing_date + T days``.
    2. Checks the DTE diff against ``TENOR_TOLERANCE[tenor]``.
       If the nearest expiration is outside tolerance, IV is set to ``None``
       for that tenor (the expiration is **not** reused).
    3. Within the chosen expiration, locates the strike nearest to
       ``underlying_price`` for each option type (call, put).
    4. Averages the two IVs (or uses whichever is available).
    5. Returns the result as a **percentage** (fraction × 100).

    Tolerance table
    ---------------
    iv7:  ± 4 calendar days
    iv14: ± 7 calendar days
    iv30: ±10 calendar days
    iv60: ±14 calendar days
    iv90: ±21 calendar days

    Parameters
    ----------
    chain : DataFrame
        Options chain with at minimum ``expiration``, ``strike``,
        ``option_type``, and ``implied_volatility`` columns.
        ``expiration`` must be ISO date strings (``"YYYY-MM-DD"``).
        ``implied_volatility`` must be stored as a decimal fraction
        (0.25 = 25 %).
    underlying_price : float
        Current underlying spot price.
    pricing_date : date, optional
        Date DTE is measured from.  Defaults to today.

    Returns
    -------
    dict
        Keys: ``iv7``, ``iv14``, ``iv30``, ``iv60``, ``iv90``.
        Values: IV as a **percentage** (e.g. ``25.0`` means 25 %).
        ``None`` when the tenor cannot be computed (no data, no IV, or
        nearest expiration outside tolerance).

        Also contains a ``_tenor_meta`` key with per-tenor diagnostics::

            {
                "iv30": {
                    "target_dte":       30,
                    "actual_dte":       28,
                    "dte_diff":          2,
                    "expiration":   "2026-06-25",
                    "within_tolerance": True,
                },
                ...
            }

        Callers that only use ``result["iv30"]`` etc. are unaffected by the
        extra ``_tenor_meta`` key.  The key is never persisted to the IV store
        (``IVSurfaceStore.upsert`` reads only the ``iv*`` keys).
    """
    if pricing_date is None:
        pricing_date = datetime.date.today()

    result: dict = {k: None for k, _ in _TENORS}
    meta:   dict = {}

    def _empty_meta() -> None:
        for key, dte in _TENORS:
            meta[key] = {
                "target_dte":       dte,
                "actual_dte":       None,
                "dte_diff":         None,
                "expiration":       None,
                "within_tolerance": False,
            }

    if chain.empty or underlying_price <= 0:
        _empty_meta()
        result["_tenor_meta"] = meta
        return result

    # Parse expiration dates column-wise (avoid row-by-row conversion)
    chain = chain.copy()
    try:
        chain["_exp_date"] = pd.to_datetime(chain["expiration"]).dt.date
    except Exception:
        _empty_meta()
        result["_tenor_meta"] = meta
        return result

    # Only consider future expirations
    expirations: list[datetime.date] = sorted(
        {d for d in chain["_exp_date"].dropna() if d > pricing_date}
    )
    if not expirations:
        _empty_meta()
        result["_tenor_meta"] = meta
        return result

    for key, dte in _TENORS:
        target      = pricing_date + datetime.timedelta(days=dte)
        closest_exp = min(expirations, key=lambda d: abs((d - target).days))
        actual_dte  = (closest_exp - pricing_date).days
        dte_diff    = abs(actual_dte - dte)
        tolerance   = TENOR_TOLERANCE[key]
        within      = dte_diff <= tolerance

        meta[key] = {
            "target_dte":       dte,
            "actual_dte":       actual_dte,
            "dte_diff":         dte_diff,
            "expiration":       closest_exp.isoformat(),
            "within_tolerance": within,
        }

        if not within:
            # Reject — do not reuse out-of-tolerance expiration
            continue

        exp_slice = chain[chain["_exp_date"] == closest_exp]

        iv_vals: list[float] = []
        for opt_type in ("call", "put"):
            leg = exp_slice[exp_slice["option_type"] == opt_type].dropna(
                subset=["implied_volatility", "strike"]
            )
            if leg.empty:
                continue

            # Filter to ±_MONEYNESS_BAND of spot, then take the N closest
            # strikes by absolute distance.  Using a neighbourhood and the
            # median rejects single bad-contract outliers (e.g. Polygon model
            # IV near zero on a deep-ITM contract that happens to be closest).
            moneyness = (leg["strike"] / underlying_price - 1.0).abs()
            leg_nearby = (
                leg[moneyness <= _MONEYNESS_BAND]
                .copy()
                .assign(_dist=(leg["strike"] - underlying_price).abs())
                .nsmallest(_N_ATM_STRIKES, "_dist")
            )
            if leg_nearby.empty:
                # Fallback: moneyness band too tight — just use closest strike
                leg_nearby = leg.copy().assign(
                    _dist=(leg["strike"] - underlying_price).abs()
                ).nsmallest(1, "_dist")

            raw_ivs = leg_nearby["implied_volatility"].dropna()
            # Apply sanity bounds before median
            valid   = raw_ivs[(raw_ivs >= _IV_MIN_FRAC) & (raw_ivs <= _IV_MAX_FRAC)]
            if not valid.empty:
                iv_vals.append(float(valid.median()))

        if iv_vals:
            avg_frac = sum(iv_vals) / len(iv_vals)
            # Convert fraction → percentage.
            # Guard: if already stored as percentage (> 5), skip the ×100.
            result[key] = round(avg_frac * 100.0, 2) if avg_frac <= 5.0 else round(avg_frac, 2)

    result["_tenor_meta"] = meta
    return result


# ===========================================================================
# Black-Scholes pricer
# ===========================================================================

_SQRT2   = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / _SQRT2PI


def bs_price_and_greeks(
    S:           float,
    K:           float,
    T:           float,
    r:           float,
    sigma:       float,
    option_type: str,
) -> tuple[float, float, float, float, float]:
    """
    European Black-Scholes option price and first-order Greeks.

    Parameters
    ----------
    S           : underlying spot price
    K           : strike price
    T           : time to expiration in **years**
    r           : continuous risk-free rate (decimal, e.g. 0.05)
    sigma       : implied volatility as decimal fraction (e.g. 0.25)
    option_type : ``"call"`` or ``"put"``

    Returns
    -------
    (price, delta, gamma, theta, vega)

    * ``price``  — option fair value
    * ``delta``  — dV/dS
    * ``gamma``  — d²V/dS²
    * ``theta``  — dV/dt per **calendar day** (negative for long options)
    * ``vega``   — dV per **1 percentage-point** IV change (i.e. per 0.01 σ)
    """
    is_call = option_type.lower() == "call"

    # --- At / beyond expiry --------------------------------------------------
    if T <= 0.0 or sigma <= 0.0:
        intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
        if T <= 0.0:
            delta = (1.0 if S > K else 0.0) if is_call else (-1.0 if S < K else 0.0)
        else:
            delta = 0.0
        return intrinsic, delta, 0.0, 0.0, 0.0

    # --- Black-Scholes -------------------------------------------------------
    sqrt_T = math.sqrt(T)
    d1     = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2     = d1 - sigma * sqrt_T
    disc   = math.exp(-r * T)

    if is_call:
        price = S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
        delta = _norm_cdf(d1)
    else:
        price = K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0

    gamma = _norm_pdf(d1) / (S * sigma * sqrt_T)

    # Theta: per calendar day
    t_base = -S * _norm_pdf(d1) * sigma / (2.0 * sqrt_T)
    if is_call:
        theta = (t_base - r * K * disc * _norm_cdf(d2))  / 365.0
    else:
        theta = (t_base + r * K * disc * _norm_cdf(-d2)) / 365.0

    # Vega: per 1 percentage-point (0.01 σ) change
    vega = S * sqrt_T * _norm_pdf(d1) * 0.01

    return price, delta, gamma, theta, vega
