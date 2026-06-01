"""
tests/test_iv_tenor_tolerance.py
----------------------------------
Tests for DTE tolerance enforcement in calculate_atm_iv_by_tenor.

Key scenarios
-------------
- 60D cannot use a 35D expiration (diff=25 > tolerance=14)
- 90D cannot use a 35D expiration (diff=55 > tolerance=21)
- Missing long-tenor data returns null instead of reusing the nearest short expiration
- Within-tolerance expirations compute IV correctly
- Exact boundary: dte_diff == tolerance → accepted (inclusive)
- Just-outside boundary: dte_diff == tolerance + 1 → rejected
- _tenor_meta is always present, even on empty chain
"""

from __future__ import annotations

import datetime

import pandas as pd
import pytest

from app.data.options_utils import calculate_atm_iv_by_tenor, TENOR_TOLERANCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRICING_DATE = datetime.date(2026, 1, 1)
_SPOT         = 100.0
_IV_FRAC      = 0.20   # 20 % IV stored as decimal fraction


def _make_chain(
    expirations: list[datetime.date],
    spot: float = _SPOT,
    iv_frac: float = _IV_FRAC,
) -> pd.DataFrame:
    """Minimal synthetic options chain: ATM call+put at each expiration."""
    rows = []
    for exp in expirations:
        for opt_type in ("call", "put"):
            rows.append(
                {
                    "underlying_ticker": "TEST",
                    "expiration":        exp.isoformat(),
                    "strike":            spot,
                    "option_type":       opt_type,
                    "implied_volatility": iv_frac,
                    "bid":               1.0,
                    "ask":               1.2,
                    "mid":               1.1,
                    "last":              1.1,
                    "delta":             0.5,
                    "gamma":             0.01,
                    "theta":             -0.05,
                    "vega":              0.1,
                    "open_interest":     100,
                    "volume":            50,
                }
            )
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=[
            "underlying_ticker", "expiration", "strike", "option_type",
            "implied_volatility", "bid", "ask", "mid", "last",
            "delta", "gamma", "theta", "vega", "open_interest", "volume",
        ]
    )


def _offset(days: int) -> datetime.date:
    return _PRICING_DATE + datetime.timedelta(days=days)


# ---------------------------------------------------------------------------
# Core rejection tests
# ---------------------------------------------------------------------------

class TestOutOfTolerance:
    """Nearest expiration outside tolerance → IV must be None."""

    def test_60d_rejects_35d_expiration(self):
        """60D tenor tolerance is ±14d; 35D is 25d off → must be rejected."""
        chain  = _make_chain([_offset(35)])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        assert result["iv60"] is None, (
            "iv60 should be null — 35DTE expiration is 25d off, tolerance is ±14d"
        )
        meta = result["_tenor_meta"]["iv60"]
        assert meta["within_tolerance"] is False
        assert meta["dte_diff"] == 25         # |60 - 35|
        assert meta["actual_dte"] == 35
        assert meta["target_dte"] == 60

    def test_90d_rejects_35d_expiration(self):
        """90D tenor tolerance is ±21d; 35D is 55d off → must be rejected."""
        chain  = _make_chain([_offset(35)])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        assert result["iv90"] is None, (
            "iv90 should be null — 35DTE expiration is 55d off, tolerance is ±21d"
        )
        meta = result["_tenor_meta"]["iv90"]
        assert meta["within_tolerance"] is False
        assert meta["dte_diff"] == 55         # |90 - 35|
        assert meta["actual_dte"] == 35
        assert meta["target_dte"] == 90

    def test_60d_and_90d_both_reject_35d_expiration(self):
        """With only a 35D expiration, both iv60 and iv90 must be null."""
        chain  = _make_chain([_offset(35)])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        assert result["iv60"] is None
        assert result["iv90"] is None


# ---------------------------------------------------------------------------
# Missing long-tenor tests
# ---------------------------------------------------------------------------

class TestMissingLongTenors:
    """Chain with only near-term expirations → long tenors return None."""

    def test_near_term_chain_nulls_long_tenors(self):
        """Only 7d, 14d, 30d expirations — iv60 and iv90 must be null."""
        chain = _make_chain([_offset(7), _offset(14), _offset(30)])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        assert result["iv7"]  is not None, "iv7 should succeed"
        assert result["iv14"] is not None, "iv14 should succeed"
        assert result["iv30"] is not None, "iv30 should succeed"

        # Nearest for iv60 is 30d (diff=30 > tolerance=14) → null
        assert result["iv60"] is None, "iv60 should be null — nearest is 30DTE (30d off)"
        # Nearest for iv90 is 30d (diff=60 > tolerance=21) → null
        assert result["iv90"] is None, "iv90 should be null — nearest is 30DTE (60d off)"

    def test_missing_long_tenor_meta_shows_rejection(self):
        """_tenor_meta for rejected tenors must record the actual expiration found."""
        chain = _make_chain([_offset(7), _offset(30)])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        for key in ("iv60", "iv90"):
            meta = result["_tenor_meta"][key]
            assert meta["within_tolerance"] is False
            assert meta["actual_dte"] is not None, "actual_dte should name the nearest exp"
            assert meta["expiration"]  is not None, "expiration should name the nearest exp"

    def test_stale_iv_not_reused_across_tenors(self):
        """Regression: iv60 must not silently inherit iv30's expiration."""
        chain  = _make_chain([_offset(30)])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        # iv30 is within ±10d of 30d → should get a value
        assert result["iv30"] is not None
        # iv60 is 30d off from 60d target → outside ±14d → must be null
        assert result["iv60"] is None


# ---------------------------------------------------------------------------
# Within-tolerance tests
# ---------------------------------------------------------------------------

class TestWithinTolerance:
    """Expirations inside tolerance compute IV correctly."""

    def test_exact_match_all_tenors(self):
        """Expirations at exactly target DTEs → all tenors populated."""
        chain = _make_chain([_offset(7), _offset(14), _offset(30), _offset(60), _offset(90)])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        for key in ("iv7", "iv14", "iv30", "iv60", "iv90"):
            assert result[key] is not None, f"{key} should be populated"
            # IV frac 0.20 → 20.0%
            assert abs(result[key] - 20.0) < 0.01, f"{key} IV should be ~20%"

    def test_within_tolerance_meta_correct(self):
        """within_tolerance=True and dte_diff correct for an on-target expiration."""
        chain  = _make_chain([_offset(30)])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        meta = result["_tenor_meta"]["iv30"]
        assert meta["within_tolerance"] is True
        assert meta["dte_diff"] == 0
        assert meta["actual_dte"] == 30
        assert meta["expiration"] == _offset(30).isoformat()


# ---------------------------------------------------------------------------
# Boundary tests
# ---------------------------------------------------------------------------

class TestBoundary:
    """Exact boundary: dte_diff == tolerance → accepted (inclusive)."""

    @pytest.mark.parametrize("tenor_key,target_dte,tol", [
        ("iv7",  7,  4),
        ("iv14", 14, 7),
        ("iv30", 30, 10),
        ("iv60", 60, 14),
        ("iv90", 90, 21),
    ])
    def test_at_boundary_accepted(self, tenor_key: str, target_dte: int, tol: int):
        """Expiration exactly at tolerance boundary must be accepted."""
        exp    = _offset(target_dte + tol)        # diff == tol exactly
        chain  = _make_chain([exp])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        meta = result["_tenor_meta"][tenor_key]
        assert meta["dte_diff"] == tol
        assert meta["within_tolerance"] is True
        assert result[tenor_key] is not None, (
            f"{tenor_key}: diff={tol} == tolerance={tol} should be accepted"
        )

    @pytest.mark.parametrize("tenor_key,target_dte,tol", [
        ("iv7",  7,  4),
        ("iv14", 14, 7),
        ("iv30", 30, 10),
        ("iv60", 60, 14),
        ("iv90", 90, 21),
    ])
    def test_one_past_boundary_rejected(self, tenor_key: str, target_dte: int, tol: int):
        """Expiration one day past tolerance boundary must be rejected."""
        exp    = _offset(target_dte + tol + 1)    # diff == tol + 1
        chain  = _make_chain([exp])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        meta = result["_tenor_meta"][tenor_key]
        assert meta["dte_diff"] == tol + 1
        assert meta["within_tolerance"] is False
        assert result[tenor_key] is None, (
            f"{tenor_key}: diff={tol + 1} > tolerance={tol} should be rejected"
        )


# ---------------------------------------------------------------------------
# Meta completeness
# ---------------------------------------------------------------------------

class TestMetaCompleteness:
    """_tenor_meta must be present and complete in all cases."""

    def test_meta_present_on_empty_chain(self):
        result = calculate_atm_iv_by_tenor(pd.DataFrame(), _SPOT, _PRICING_DATE)
        assert "_tenor_meta" in result
        for key in ("iv7", "iv14", "iv30", "iv60", "iv90"):
            assert key in result["_tenor_meta"]
            meta = result["_tenor_meta"][key]
            assert "target_dte" in meta
            assert "within_tolerance" in meta

    def test_meta_present_on_no_future_expirations(self):
        """Chain with only past expirations → all tenors None, meta complete."""
        past  = _PRICING_DATE - datetime.timedelta(days=5)
        chain = _make_chain([past])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)

        assert "_tenor_meta" in result
        for key in ("iv7", "iv14", "iv30", "iv60", "iv90"):
            assert result[key] is None
            meta = result["_tenor_meta"][key]
            assert meta["within_tolerance"] is False

    def test_meta_keys_match_tenor_tolerance_constant(self):
        """All keys in _tenor_meta match TENOR_TOLERANCE keys."""
        chain  = _make_chain([_offset(30)])
        result = calculate_atm_iv_by_tenor(chain, _SPOT, _PRICING_DATE)
        assert set(result["_tenor_meta"].keys()) == set(TENOR_TOLERANCE.keys())
