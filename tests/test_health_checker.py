"""
Regression tests for skills/health_checker.py

Coverage:
  1. _get_margin_band() routing — all sector overrides, skip sectors, default fallback
  2. Consumer Cyclical 22.4% → no anomaly (PDD regression guard)
  3. Financial Services 41% → no anomaly (operating margin skip path)
  4. "bank" keyword → skip path
  5. "insurance" keyword → skip path (detect_margin_fragility layer, not just _get_margin_band)
  6. Extreme upper bound (80%) → data_anomaly + correct message
  7. Anomaly detail structure completeness
  8. Real Estate boundary: 44.9% passes, 46% triggers anomaly
  9. Default fallback boundary: 30% passes, 70% triggers anomaly
  10. Extreme lower bound (-10%) → data_anomaly

All tests call pure functions directly — no yfinance network calls.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills"))

from health_checker import _get_margin_band, detect_margin_fragility


# ---------------------------------------------------------------------------
# Test 1: _get_margin_band() full routing table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sector, expected", [
    ("Technology",             (-5.0, 70.0)),
    ("Communication Services", (-5.0, 70.0)),
    ("Healthcare",             (-5.0, 70.0)),
    ("Real Estate",            (-5.0, 45.0)),
    ("Consumer Cyclical",      (-5.0, 35.0)),
    ("Consumer Defensive",     (-5.0, 30.0)),
    ("Industrials",            (-5.0, 35.0)),
    ("Basic Materials",        (-5.0, 35.0)),
    ("Energy",                 (-5.0, 40.0)),
    ("Financial Services",     None),
    ("Financials",             None),
    ("bank",                   None),
    ("insurance",              None),
    ("Unknown",                (-5.0, 60.0)),
    ("",                       (-5.0, 60.0)),
])
def test_get_margin_band_routing(sector, expected):
    """_get_margin_band must return the correct band or None for each sector."""
    assert _get_margin_band(sector) == expected, (
        f"sector='{sector}': expected {expected}, got {_get_margin_band(sector)}"
    )


# ---------------------------------------------------------------------------
# Test 2: Consumer Cyclical 22.4% → no anomaly (PDD regression guard)
# ---------------------------------------------------------------------------

def test_consumer_cyclical_normal_margin_no_anomaly():
    """
    PDD's 22.4% triggered a false MARGIN_DATA_ANOMALY before the fix
    (old band was (-5, 2)). After fix: Consumer Cyclical band is (-5, 35).
    22.4% is within range — must not return 'data_anomaly'.
    """
    info = {"grossMargins": 0.563, "operatingMargins": 0.224}
    result, _ = detect_margin_fragility(info, sector="Consumer Cyclical")
    assert result != "data_anomaly", (
        "22.4% operating margin for Consumer Cyclical must not trigger data_anomaly"
    )


# ---------------------------------------------------------------------------
# Test 3: Financial Services 41% → no anomaly (skip path)
# ---------------------------------------------------------------------------

def test_financial_services_skips_operating_margin_check():
    """Financial Services: operating margin check is skipped entirely."""
    info = {"grossMargins": 0.0, "operatingMargins": 0.411}
    result, _ = detect_margin_fragility(info, sector="Financial Services")
    assert result != "data_anomaly", (
        "Financial Services must skip operating margin check"
    )


# ---------------------------------------------------------------------------
# Test 4: "bank" keyword → skip path
# ---------------------------------------------------------------------------

def test_bank_keyword_skips_operating_margin():
    """'bank' is in OPERATING_MARGIN_SECTOR_SKIP — implausible margin must not anomaly."""
    info = {"grossMargins": 0.30, "operatingMargins": 0.95}
    result, _ = detect_margin_fragility(info, sector="bank")
    assert result != "data_anomaly", "'bank' sector must skip operating margin check"


# ---------------------------------------------------------------------------
# Test 5: "insurance" keyword → skip path (detect_margin_fragility layer)
# ---------------------------------------------------------------------------

def test_insurance_keyword_skips_operating_margin():
    """
    'insurance' is in OPERATING_MARGIN_SECTOR_SKIP.
    Test hits detect_margin_fragility directly (not just _get_margin_band)
    to confirm the skip propagates correctly through the full function.
    """
    info = {"grossMargins": 0.30, "operatingMargins": 0.88}
    result, _ = detect_margin_fragility(info, sector="insurance")
    assert result != "data_anomaly", "'insurance' sector must skip operating margin check"


# ---------------------------------------------------------------------------
# Test 6: Extreme upper bound (80%) → data_anomaly + correct message
# ---------------------------------------------------------------------------

def test_extreme_upper_margin_triggers_anomaly():
    """
    Consumer Cyclical band is (-5, 35). 80% exceeds upper bound.
    Must return 'data_anomaly' with 'Implausibly extreme' in anomaly_reason.
    """
    info = {"grossMargins": 0.56, "operatingMargins": 0.80}
    result, detail = detect_margin_fragility(info, sector="Consumer Cyclical")

    assert result == "data_anomaly", (
        "80% operating margin for Consumer Cyclical must trigger data_anomaly"
    )
    assert "Implausibly extreme" in detail.get("anomaly_reason", ""), (
        f"anomaly_reason must contain 'Implausibly extreme'. Got: {detail}"
    )


# ---------------------------------------------------------------------------
# Test 7: Anomaly detail structure completeness
# ---------------------------------------------------------------------------

def test_anomaly_detail_structure_completeness():
    """
    When data_anomaly is returned, detail dict must contain all fields
    that downstream consumers rely on: gross_margin_pct, operating_margin_pct,
    sector, anomaly_reason.
    Catches regressions where dict keys are renamed or dropped.
    """
    info = {"grossMargins": 0.56, "operatingMargins": 0.80}
    result, detail = detect_margin_fragility(info, sector="Consumer Cyclical")

    assert result == "data_anomaly"
    for key in ("gross_margin_pct", "operating_margin_pct", "sector", "anomaly_reason"):
        assert key in detail, f"Missing key '{key}' in anomaly detail: {detail}"

    assert detail["sector"] == "Consumer Cyclical"
    assert isinstance(detail["gross_margin_pct"], float)
    assert isinstance(detail["operating_margin_pct"], float)
    assert isinstance(detail["anomaly_reason"], str)


# ---------------------------------------------------------------------------
# Test 8: Real Estate boundary — 44.9% passes, 46% triggers anomaly
# ---------------------------------------------------------------------------

def test_real_estate_boundary():
    """
    Real Estate band is (-5.0, 45.0).
    44.9% is within band → no anomaly.
    46.0% exceeds upper bound → data_anomaly.
    Verifies the boundary was not written as ≤ or shifted by off-by-one.
    """
    base_info = {"grossMargins": 0.40}

    result_in, _ = detect_margin_fragility(
        {**base_info, "operatingMargins": 0.449}, sector="Real Estate"
    )
    assert result_in != "data_anomaly", "44.9% for Real Estate must be within band (-5, 45)"

    result_out, _ = detect_margin_fragility(
        {**base_info, "operatingMargins": 0.46}, sector="Real Estate"
    )
    assert result_out == "data_anomaly", "46% for Real Estate must exceed upper bound"


# ---------------------------------------------------------------------------
# Test 9: Default fallback boundary — 30% passes, 70% triggers anomaly
# ---------------------------------------------------------------------------

def test_default_fallback_boundary():
    """
    Default band (-5.0, 60.0) applies for unknown sectors.
    30% is within band → no anomaly.
    70% exceeds upper bound → data_anomaly.
    """
    base_info = {"grossMargins": 0.40}

    result_in, _ = detect_margin_fragility(
        {**base_info, "operatingMargins": 0.30}, sector="Unknown"
    )
    assert result_in != "data_anomaly", "30% for 'Unknown' must be within default band (-5, 60)"

    result_out, _ = detect_margin_fragility(
        {**base_info, "operatingMargins": 0.70}, sector="Unknown"
    )
    assert result_out == "data_anomaly", "70% for 'Unknown' must exceed default upper bound (60)"


# ---------------------------------------------------------------------------
# Test 10: Extreme lower bound (-10%) → data_anomaly
# ---------------------------------------------------------------------------

def test_extreme_lower_bound_triggers_anomaly():
    """
    All sector bands share lower bound -5.0.
    -10% operating margin falls below -5% floor → must trigger data_anomaly.
    Uses Consumer Cyclical to avoid the skip path.
    """
    info = {"grossMargins": 0.40, "operatingMargins": -0.10}
    result, detail = detect_margin_fragility(info, sector="Consumer Cyclical")

    assert result == "data_anomaly", (
        "-10% must fall below lower bound (-5%) → data_anomaly"
    )
    assert "Implausibly extreme" in detail.get("anomaly_reason", ""), (
        f"anomaly_reason must contain 'Implausibly extreme'. Got: {detail}"
    )
