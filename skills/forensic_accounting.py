#!/usr/bin/env python3
"""
forensic_accounting.py — Beneish M-Score, Accruals Quality, Working Capital Forensics
Part of the Buyer Analyst evidence pipeline.

Role: Quantitative forensic screening — not a final verdict.
      All signals must be verified against primary filing disclosures before PM Verdict.

Design boundary:
  This script covers QUANTITATIVE signals only (M-Score, accruals ratio, WC trends).
  "Accounting policy change affecting comparability" is an EXTERNAL trigger:
    Step 3 Text Pre-Scan identifies the policy change in the filing text, then routes
    the analyst here to run the quantitative corroboration screen.

Trigger conditions (§2.3 Forensic Gate):
  External (text layer, Step 3):
    - Accounting policy change affecting comparability (depreciation/amortization,
      revenue recognition, capitalization policy, consolidation scope, or any other
      policy shift that changes period-over-period comparability)
  Quantitative (this script handles):
    - Accruals-driven earnings pattern (earnings materially outpacing operating CF)
    - Working capital deterioration (DSO/DIO expanding, DPO compressing)
    - Pre-falsification forensic screen on any company with 2+ years of primary filing data

Notes:
  - Financial values use ×10^8 units (labeled _bn). For USD-reporting companies this is
    not standard billions (×10^9) — treat as a relative magnitude indicator, not absolute.
  - DSO/DIO/DPO use end-of-period balances (not average). Suitable for triage screening;
    use average balance method for more precise analysis against primary filings.
  - sector is carried in meta for context only; it does not drive thresholds in this version.

Usage:
    python3 skills/forensic_accounting.py TICKER [--json]
"""

import sys
import json
import argparse
import yfinance as yf
from datetime import datetime

SCRIPT_VERSION = "1.1"

# ── Beneish M-Score (Beneish 1999, 8-variable probit model) ──────────────────
M_SCORE_INTERCEPT = -4.84
M_SCORE_WEIGHTS = {
    "DSRI":  0.920,   # Days Sales Receivable Index       (high weight — revenue quality)
    "GMI":   0.528,   # Gross Margin Index
    "AQI":   0.404,   # Asset Quality Index
    "SGI":   0.892,   # Sales Growth Index                (high weight)
    "DEPI":  0.115,   # Depreciation Index
    "SGAI": -0.172,   # SG&A Index
    "TATA":  4.679,   # Total Accruals to Total Assets    (highest weight)
    "LVGI": -0.327,   # Leverage Index
}
M_SCORE_MANIPULATION_THRESHOLD = -1.78   # above → MANIPULATION_RISK
M_SCORE_WATCH_THRESHOLD        = -2.22   # between → WATCH; below → CLEAN
M_SCORE_MIN_COMPONENTS         = 5       # fewer → score not reported

# ── Accruals ratio thresholds ─────────────────────────────────────────────────
ACCRUALS_HIGH     = 0.10
ACCRUALS_MODERATE = 0.05

# ── Working capital: minimum % change to flag as material ────────────────────
WC_MATERIAL_CHANGE = 0.10

# ── Capex / D&A ratio thresholds ────────────────────────────────────────────
CAPEX_DA_UNDERINVESTMENT = 0.8   # Capex/D&A < 0.8 → capacity shrinking
CAPEX_DA_HEAVY_EXPANSION = 2.0   # Capex/D&A > 2.0 → heavy expansion phase


# ── Column sort / period helpers ──────────────────────────────────────────────

def _sort_cols(df):
    """Sort DataFrame columns descending by date (most recent first)."""
    if df is None or df.empty:
        return df
    try:
        return df.sort_index(axis=1, ascending=False)
    except Exception:
        return df  # fall back to original order if sorting fails


def _period_labels(df):
    """
    Return (period_t, period_t1) ISO-date strings from a sorted DataFrame.
    period_t  = most recent column (t)
    period_t1 = second column     (t-1)
    Returns (None, None) if df is empty or has fewer than 2 columns.
    """
    if df is None or df.empty:
        return None, None
    try:
        cols = df.columns.tolist()
        p0 = str(cols[0])[:10] if len(cols) > 0 else None
        p1 = str(cols[1])[:10] if len(cols) > 1 else None
        return p0, p1
    except Exception:
        return None, None


# ── Data extraction ───────────────────────────────────────────────────────────

def _safe_get(df, candidates):
    """Return the first matching row label from df.index as a Series. None if not found."""
    for key in candidates:
        if key in df.index:
            return df.loc[key]
    return None


def _float(series, col=0):
    """Safely extract a float from a sorted pandas Series at positional index col."""
    if series is None:
        return None
    try:
        v = series.iloc[col]
        if v is None:
            return None
        f = float(v)
        return None if (f != f) else f   # NaN guard
    except Exception:
        return None


def _fetch_financials(ticker_str):
    stock = yf.Ticker(ticker_str)
    info  = stock.info
    fin   = _sort_cols(stock.financials)     # income statement
    bal   = _sort_cols(stock.balance_sheet)  # balance sheet
    cf    = _sort_cols(stock.cashflow)        # cash flow statement
    return info, fin, bal, cf


def _extract_is(fin):
    """Income statement fields.  t = col 0 (most recent), t-1 = col 1."""
    if fin is None or fin.empty or fin.shape[1] < 2:
        return {}
    revenue      = _safe_get(fin, ["Total Revenue", "Revenue"])
    cogs         = _safe_get(fin, ["Cost Of Revenue", "Cost Of Goods Sold",
                                   "Cost of Revenue"])
    gross_profit = _safe_get(fin, ["Gross Profit"])
    net_income   = _safe_get(fin, ["Net Income", "Net Income Common Stockholders",
                                   "Net Income Including Noncontrolling Interests"])
    sga          = _safe_get(fin, ["Selling General And Administration",
                                   "Selling General Administrative",
                                   "General And Administrative Expense",
                                   "Selling And Marketing Expense"])
    depreciation = _safe_get(fin, ["Reconciled Depreciation",
                                   "Depreciation Amortization Depletion",
                                   "Depreciation And Amortization In Income Statement",
                                   "Depreciation"])
    return {
        "revenue_t":       _float(revenue,      0),
        "revenue_t1":      _float(revenue,      1),
        "cogs_t":          _float(cogs,         0),
        "cogs_t1":         _float(cogs,         1),
        "gross_profit_t":  _float(gross_profit, 0),
        "gross_profit_t1": _float(gross_profit, 1),
        "net_income_t":    _float(net_income,   0),
        "sga_t":           _float(sga,          0),
        "sga_t1":          _float(sga,          1),
        "depreciation_t":  _float(depreciation, 0),
        "depreciation_t1": _float(depreciation, 1),
    }


def _extract_bs(bal):
    """Balance sheet fields.  t = col 0, t-1 = col 1."""
    if bal is None or bal.empty or bal.shape[1] < 2:
        return {}
    total_assets   = _safe_get(bal, ["Total Assets"])
    current_assets = _safe_get(bal, ["Current Assets", "Total Current Assets"])
    ppe            = _safe_get(bal, ["Net PPE", "Property Plant Equipment Net",
                                     "Net Property Plant And Equipment"])
    lt_debt        = _safe_get(bal, ["Long Term Debt",
                                     "Long Term Debt And Capital Lease Obligation"])
    current_liab   = _safe_get(bal, ["Current Liabilities", "Total Current Liabilities"])
    ar             = _safe_get(bal, ["Accounts Receivable", "Net Receivables",
                                     "Receivables", "Gross Accounts Receivable"])
    inventory      = _safe_get(bal, ["Inventory", "Inventories"])
    ap             = _safe_get(bal, ["Accounts Payable", "Payables",
                                     "Payables And Accrued Expenses"])
    return {
        "total_assets_t":    _float(total_assets,   0),
        "total_assets_t1":   _float(total_assets,   1),
        "current_assets_t":  _float(current_assets, 0),
        "current_assets_t1": _float(current_assets, 1),
        "ppe_t":             _float(ppe,            0),
        "ppe_t1":            _float(ppe,            1),
        "lt_debt_t":         _float(lt_debt,        0),
        "lt_debt_t1":        _float(lt_debt,        1),
        "current_liab_t":    _float(current_liab,   0),
        "current_liab_t1":   _float(current_liab,   1),
        "ar_t":              _float(ar,             0),
        "ar_t1":             _float(ar,             1),
        "inventory_t":       _float(inventory,      0),
        "inventory_t1":      _float(inventory,      1),
        "ap_t":              _float(ap,             0),
        "ap_t1":             _float(ap,             1),
    }


def _extract_cf(cf):
    """Cash flow fields.  t = col 0."""
    if cf is None or cf.empty or cf.shape[1] < 1:
        return {}
    ocf = _safe_get(cf, ["Operating Cash Flow",
                          "Total Cash From Operating Activities",
                          "Cash Flow From Continuing Operating Activities"])
    capex = _safe_get(cf, ["Capital Expenditure", "Capital Expenditures",
                            "Purchase Of PPE"])
    da = _safe_get(cf, ["Depreciation Amortization Depletion",
                         "Depreciation And Amortization",
                         "Depreciation & Amortization"])
    chg_ar = _safe_get(cf, ["Change In Receivables", "Changes In Account Receivables",
                              "Increase Decrease In Accounts Receivable"])
    chg_inv = _safe_get(cf, ["Change In Inventory", "Increase Decrease In Inventory"])
    chg_ap = _safe_get(cf, ["Change In Payable", "Change In Payables And Accrued Expense",
                              "Increase Decrease In Accounts Payable"])
    return {
        "ocf_t": _float(ocf, 0),
        "capex_t": _float(capex, 0),
        "da_t": _float(da, 0),
        "chg_ar_t": _float(chg_ar, 0),
        "chg_inv_t": _float(chg_inv, 0),
        "chg_ap_t": _float(chg_ap, 0),
    }


# ── Beneish M-Score ───────────────────────────────────────────────────────────

def _score_reliability(comp_count):
    if comp_count == 8:
        return "high"
    if comp_count >= 6:
        return "medium"
    return "low"


def _compute_m_score(is_f, bs_f, cf_f):
    """
    Compute Beneish M-Score and its 8 components.
    Missing index-type components (all except TATA) default to neutral 1.0 (no-change).
    Missing TATA defaults to 0.0 (no accruals).
    Returns: (components dict, score or None, comp_count int, missing list, notes list)
    """
    components = {}
    notes      = []

    rev_t  = is_f.get("revenue_t");       rev_t1  = is_f.get("revenue_t1")
    cogs_t = is_f.get("cogs_t");          cogs_t1 = is_f.get("cogs_t1")
    gp_t   = is_f.get("gross_profit_t");  gp_t1   = is_f.get("gross_profit_t1")
    sga_t  = is_f.get("sga_t");           sga_t1  = is_f.get("sga_t1")
    dep_t  = is_f.get("depreciation_t");  dep_t1  = is_f.get("depreciation_t1")
    ni_t   = is_f.get("net_income_t")

    ta_t   = bs_f.get("total_assets_t");  ta_t1   = bs_f.get("total_assets_t1")
    ca_t   = bs_f.get("current_assets_t"); ca_t1  = bs_f.get("current_assets_t1")
    ppe_t  = bs_f.get("ppe_t");           ppe_t1  = bs_f.get("ppe_t1")
    ltd_t  = bs_f.get("lt_debt_t");       ltd_t1  = bs_f.get("lt_debt_t1")
    cl_t   = bs_f.get("current_liab_t");  cl_t1   = bs_f.get("current_liab_t1")
    ar_t   = bs_f.get("ar_t");            ar_t1   = bs_f.get("ar_t1")
    ocf_t  = cf_f.get("ocf_t")

    # DSRI: (AR_t / Rev_t) / (AR_t1 / Rev_t1)
    if all(v not in (None, 0) for v in [ar_t, rev_t, ar_t1, rev_t1]):
        components["DSRI"] = round((ar_t / rev_t) / (ar_t1 / rev_t1), 4)
    else:
        notes.append("DSRI: unavailable (accounts receivable or revenue missing)")

    # GMI: (GP_t1 / Rev_t1) / (GP_t / Rev_t)
    gp_t_v  = gp_t  if gp_t  is not None else (
               (rev_t  - cogs_t)  if rev_t  is not None and cogs_t  is not None else None)
    gp_t1_v = gp_t1 if gp_t1 is not None else (
               (rev_t1 - cogs_t1) if rev_t1 is not None and cogs_t1 is not None else None)
    if all(v not in (None, 0) for v in [gp_t_v, rev_t, gp_t1_v, rev_t1]):
        components["GMI"] = round((gp_t1_v / rev_t1) / (gp_t_v / rev_t), 4)
    else:
        notes.append("GMI: unavailable (gross profit or revenue missing)")

    # AQI: [1-(PPE_t+CA_t)/TA_t] / [1-(PPE_t1+CA_t1)/TA_t1]
    if all(v not in (None, 0) for v in [ppe_t, ca_t, ta_t, ppe_t1, ca_t1, ta_t1]):
        aqi_num = 1 - (ppe_t  + ca_t)  / ta_t
        aqi_den = 1 - (ppe_t1 + ca_t1) / ta_t1
        if aqi_den != 0:
            components["AQI"] = round(aqi_num / aqi_den, 4)
        else:
            notes.append("AQI: prior-year denominator zero")
    else:
        notes.append("AQI: unavailable (PPE, current assets, or total assets missing)")

    # SGI: Rev_t / Rev_t1
    if rev_t is not None and rev_t1 not in (None, 0):
        components["SGI"] = round(rev_t / rev_t1, 4)
    else:
        notes.append("SGI: unavailable (revenue missing for one or both periods)")

    # DEPI: [dep_t1/(dep_t1+ppe_t1)] / [dep_t/(dep_t+ppe_t)]
    if all(v is not None for v in [dep_t, dep_t1, ppe_t, ppe_t1]):
        den_t  = dep_t  + ppe_t
        den_t1 = dep_t1 + ppe_t1
        if den_t != 0 and den_t1 != 0:
            rate_t  = dep_t  / den_t
            rate_t1 = dep_t1 / den_t1
            if rate_t != 0:
                components["DEPI"] = round(rate_t1 / rate_t, 4)
            else:
                notes.append("DEPI: current-period depreciation rate is zero")
        else:
            notes.append("DEPI: zero denominator (depreciation + PPE = 0)")
    else:
        notes.append("DEPI: unavailable (depreciation or PPE missing)")

    # SGAI: (SGA_t / Rev_t) / (SGA_t1 / Rev_t1)
    if all(v not in (None, 0) for v in [sga_t, rev_t, sga_t1, rev_t1]):
        components["SGAI"] = round((sga_t / rev_t) / (sga_t1 / rev_t1), 4)
    else:
        notes.append("SGAI: unavailable (SGA or revenue missing)")

    # LVGI: [(LTD_t + CL_t) / TA_t] / [(LTD_t1 + CL_t1) / TA_t1]
    # LTD defaults to 0 when absent (company may carry no long-term debt)
    if all(v is not None for v in [cl_t, ta_t, cl_t1, ta_t1]) and ta_t != 0 and ta_t1 != 0:
        ltd_tv  = ltd_t  if ltd_t  is not None else 0.0
        ltd_t1v = ltd_t1 if ltd_t1 is not None else 0.0
        lev_t   = (ltd_tv  + cl_t)  / ta_t
        lev_t1  = (ltd_t1v + cl_t1) / ta_t1
        if lev_t1 != 0:
            components["LVGI"] = round(lev_t / lev_t1, 4)
        else:
            notes.append("LVGI: prior-year leverage is zero")
    else:
        notes.append("LVGI: unavailable (current liabilities or total assets missing)")

    # TATA: (NI_t - OCF_t) / TA_t
    if all(v is not None for v in [ni_t, ocf_t, ta_t]) and ta_t != 0:
        components["TATA"] = round((ni_t - ocf_t) / ta_t, 4)
    else:
        notes.append("TATA: unavailable (net income, operating CF, or total assets missing)")

    comp_count = len(components)
    missing    = [name for name in M_SCORE_WEIGHTS if name not in components]

    if comp_count < M_SCORE_MIN_COMPONENTS:
        return components, None, comp_count, missing, notes

    # Score: missing index components → neutral 1.0; missing TATA → neutral 0.0
    score = M_SCORE_INTERCEPT
    for name, weight in M_SCORE_WEIGHTS.items():
        if name in components:
            score += weight * components[name]
        elif name == "TATA":
            score += weight * 0.0
            notes.append("TATA: substituted with neutral 0.0 (no-accruals assumption)")
        else:
            score += weight * 1.0
            notes.append(f"{name}: substituted with neutral 1.0 (no-change assumption) "
                         f"— weight {weight:+.3f}")

    return components, round(score, 4), comp_count, missing, notes


def _m_signal(score):
    if score is None:
        return "INSUFFICIENT_DATA"
    if score > M_SCORE_MANIPULATION_THRESHOLD:
        return "MANIPULATION_RISK"
    if score > M_SCORE_WATCH_THRESHOLD:
        return "WATCH"
    return "CLEAN"


# ── Accruals Quality ──────────────────────────────────────────────────────────

def _compute_accruals(is_f, bs_f, cf_f):
    """
    Accruals ratio = (Net Income − Operating CF) / Average Total Assets
    Positive → earnings ahead of cash flow (lower earnings quality)
    Negative → cash flow exceeds earnings (conservative accounting)
    Returns: (result dict or None, error string or None)
    """
    ni    = is_f.get("net_income_t")
    ocf   = cf_f.get("ocf_t")
    ta_t  = bs_f.get("total_assets_t")
    ta_t1 = bs_f.get("total_assets_t1")

    if ni is None or ocf is None:
        return None, "net income or operating cash flow unavailable"
    if ta_t is None:
        return None, "total assets unavailable"

    accruals = ni - ocf
    avg_ta   = (ta_t + ta_t1) / 2 if ta_t1 is not None else ta_t
    if avg_ta == 0:
        return None, "average total assets is zero"

    ratio = accruals / avg_ta

    if ratio > ACCRUALS_HIGH:
        signal = "HIGH"
        interp = "Earnings materially exceed cash flow — accruals-driven. Verify revenue recognition policy in primary filing."
    elif ratio > ACCRUALS_MODERATE:
        signal = "MODERATE"
        interp = "Moderate positive accruals — monitor for sustained trend across periods."
    elif ratio < -ACCRUALS_HIGH:
        signal = "STRONGLY_NEGATIVE"
        interp = "Operating CF substantially exceeds earnings — conservative accounting signal."
    elif ratio < -ACCRUALS_MODERATE:
        signal = "MODERATELY_NEGATIVE"
        interp = "Cash flow modestly exceeds earnings — low accruals quality concern."
    else:
        signal = "LOW"
        interp = "Cash flow broadly supports reported earnings — low accruals concern."

    return {
        "ratio":               round(ratio, 4),
        "ratio_pct":           round(ratio * 100, 2),
        "accruals_bn":         round(accruals / 1e8, 2),
        "net_income_bn":       round(ni  / 1e8, 2),
        "operating_cf_bn":     round(ocf / 1e8, 2),
        "avg_total_assets_bn": round(avg_ta / 1e8, 2),
        "signal":              signal,
        "interpretation":      interp,
    }, None


# ── Working Capital ───────────────────────────────────────────────────────────

def _trend(cur, pri, higher_is_bad=True):
    if cur is None or pri is None or pri == 0:
        return "UNAVAILABLE"
    chg = (cur - pri) / abs(pri)
    if abs(chg) < WC_MATERIAL_CHANGE:
        return "STABLE"
    if higher_is_bad:
        return "DETERIORATING" if chg > 0 else "IMPROVING"
    return "DETERIORATING" if chg < 0 else "IMPROVING"


def _compute_wc(is_f, bs_f):
    """
    DSO = AR / Revenue × 365      (higher → receivables building faster than sales)
    DIO = Inventory / COGS × 365  (higher → inventory bloat)
    DPO = AP / COGS × 365         (lower  → payables compressing, potential stress)
    CCC = DSO + DIO − DPO         (higher → worse working capital cycle)
    Uses end-of-period balances (triage approximation; not average balance method).
    """
    rev_t  = is_f.get("revenue_t");   rev_t1  = is_f.get("revenue_t1")
    cogs_t = is_f.get("cogs_t");      cogs_t1 = is_f.get("cogs_t1")
    ar_t   = bs_f.get("ar_t");        ar_t1   = bs_f.get("ar_t1")
    inv_t  = bs_f.get("inventory_t"); inv_t1  = bs_f.get("inventory_t1")
    ap_t   = bs_f.get("ap_t");        ap_t1   = bs_f.get("ap_t1")

    def _days(num, den):
        return round(num / den * 365, 1) if (num is not None and den and den != 0) else None

    dso_t  = _days(ar_t,  rev_t);   dso_t1  = _days(ar_t1,  rev_t1)
    dio_t  = _days(inv_t, cogs_t);  dio_t1  = _days(inv_t1, cogs_t1)
    dpo_t  = _days(ap_t,  cogs_t);  dpo_t1  = _days(ap_t1,  cogs_t1)
    ccc_t  = round(dso_t  + dio_t  - dpo_t,  1) if all(v is not None for v in [dso_t,  dio_t,  dpo_t])  else None
    ccc_t1 = round(dso_t1 + dio_t1 - dpo_t1, 1) if all(v is not None for v in [dso_t1, dio_t1, dpo_t1]) else None

    dso_trend = _trend(dso_t, dso_t1, higher_is_bad=True)
    dio_trend = _trend(dio_t, dio_t1, higher_is_bad=True)
    dpo_trend = _trend(dpo_t, dpo_t1, higher_is_bad=False)
    ccc_trend = _trend(ccc_t, ccc_t1, higher_is_bad=True)

    # Detailed flags stay in working_capital_flags (aggregated into one active_flag entry)
    wc_flags = []
    if dso_trend == "DETERIORATING":
        wc_flags.append(f"DSO {dso_t1}d → {dso_t}d: receivables building faster than sales")
    if dio_trend == "DETERIORATING":
        wc_flags.append(f"DIO {dio_t1}d → {dio_t}d: inventory buildup")
    if dpo_trend == "DETERIORATING":
        wc_flags.append(f"DPO {dpo_t1}d → {dpo_t}d: payables compressing — potential supplier pressure or financial stress")
    if ccc_trend == "DETERIORATING":
        wc_flags.append(f"CCC {ccc_t1}d → {ccc_t}d: working capital cycle worsening")

    return {
        "dso_current": dso_t,  "dso_prior": dso_t1,  "dso_trend": dso_trend,
        "dio_current": dio_t,  "dio_prior": dio_t1,  "dio_trend": dio_trend,
        "dpo_current": dpo_t,  "dpo_prior": dpo_t1,  "dpo_trend": dpo_trend,
        "ccc_current": ccc_t,  "ccc_prior": ccc_t1,  "ccc_trend": ccc_trend,
        "working_capital_flags": wc_flags,
    }


# ── Capex / D&A Analysis ─────────────────────────────────────────────────────

def _compute_capex_da(cf_f, is_f=None):
    """
    Capex/D&A ratio: measures reinvestment intensity relative to asset consumption.
    < 0.8 → capacity shrinking (underinvestment)
    > 2.0 → heavy expansion (capex burden)
    """
    capex = cf_f.get("capex_t")
    da = cf_f.get("da_t")
    if capex is None or da is None or da == 0:
        return None, "Capex or D&A data unavailable"
    abs_capex = abs(capex)
    ratio = abs_capex / abs(da)
    # Detect capex→opex reclassification: capex drops sharply while COGS surges
    if is_f is None:
        is_f = {}
    cogs_t = is_f.get("cogs_t")
    cogs_t1 = is_f.get("cogs_t1")
    capex_prior = cf_f.get("capex_t")  # already current period
    capex_opex_flag = False
    if cogs_t and cogs_t1 and cogs_t1 != 0:
        cogs_growth = (cogs_t - cogs_t1) / abs(cogs_t1)
        if ratio < CAPEX_DA_UNDERINVESTMENT and cogs_growth > 1.0:
            # Capex low + COGS doubled → likely capex→opex reclassification
            capex_opex_flag = True

    if capex_opex_flag:
        signal = "CAPEX_OPEX_RECLASSIFICATION"
        interp = (f"Capex/D&A ratio {ratio:.2f} appears low, but COGS grew {cogs_growth*100:.0f}% — "
                  "company may have shifted from capex (equipment lease) to opex (service procurement). "
                  "Do not interpret as underinvestment without verifying capex policy change in MD&A.")
    elif ratio < CAPEX_DA_UNDERINVESTMENT:
        signal = "UNDERINVESTMENT"
        interp = (f"Capex/D&A ratio {ratio:.2f} < {CAPEX_DA_UNDERINVESTMENT} — "
                  "company is not replacing depreciated assets. Capacity may be shrinking.")
    elif ratio > CAPEX_DA_HEAVY_EXPANSION:
        signal = "HEAVY_EXPANSION"
        interp = (f"Capex/D&A ratio {ratio:.2f} > {CAPEX_DA_HEAVY_EXPANSION} — "
                  "heavy investment phase. Verify ROIC on new capex vs WACC.")
    else:
        signal = "NORMAL"
        interp = f"Capex/D&A ratio {ratio:.2f} — reinvestment pace broadly matches depreciation."
    return {
        "ratio": round(ratio, 3),
        "capex_bn": round(abs_capex / 1e8, 2),
        "da_bn": round(abs(da) / 1e8, 2),
        "signal": signal,
        "interpretation": interp,
    }, None


# ── WC Sub-Item Decomposition ───────────────────────────────────────────────

def _compute_wc_decomposition(cf_f, is_f):
    """
    Identify which WC sub-item (AR, Inventory, AP) is the main driver
    of working capital change, using cashflow statement line items.
    """
    chg_ar = cf_f.get("chg_ar_t")
    chg_inv = cf_f.get("chg_inv_t")
    chg_ap = cf_f.get("chg_ap_t")
    rev_t = is_f.get("revenue_t")

    items = {}
    notes = []
    if chg_ar is not None:
        items["change_in_receivables_bn"] = round(chg_ar / 1e8, 2)
        if chg_ar < 0 and rev_t and rev_t > 0:
            ar_pct_of_rev = abs(chg_ar) / rev_t * 100
            if ar_pct_of_rev > 3:
                notes.append(f"AR increase = {ar_pct_of_rev:.1f}% of revenue — receivables building")
    if chg_inv is not None:
        items["change_in_inventory_bn"] = round(chg_inv / 1e8, 2)
        if chg_inv < 0:
            notes.append(f"Inventory increase {round(abs(chg_inv) / 1e8, 2)}bn — potential buildup")
    if chg_ap is not None:
        items["change_in_payables_bn"] = round(chg_ap / 1e8, 2)
        if chg_ap < 0:
            notes.append(f"Payables decrease {round(abs(chg_ap) / 1e8, 2)}bn — supplier pressure or deleveraging")

    if not items:
        return None, "WC sub-item data unavailable from cashflow"

    drivers = []
    if chg_ar is not None:
        drivers.append(("receivables", chg_ar))
    if chg_inv is not None:
        drivers.append(("inventory", chg_inv))
    if chg_ap is not None:
        drivers.append(("payables", chg_ap))
    if drivers:
        dominant = min(drivers, key=lambda x: x[1])
        items["dominant_cash_drain"] = dominant[0] if dominant[1] < 0 else None
    items["notes"] = notes
    return items, None


# ── Main analysis ─────────────────────────────────────────────────────────────

def run_forensic_analysis(ticker_str):
    try:
        info, fin, bal, cf = _fetch_financials(ticker_str)
        company_name = info.get("longName", ticker_str)
        sector       = info.get("sector", info.get("industry", "Unknown"))

        # Period labels: try income statement first, fall back to balance sheet
        period_t, period_t1 = _period_labels(fin)
        if period_t is None:
            period_t, period_t1 = _period_labels(bal)

        is_f = _extract_is(fin)
        bs_f = _extract_bs(bal)
        cf_f = _extract_cf(cf)

        # ── M-Score ──
        components, m_score, comp_count, missing, m_notes = _compute_m_score(is_f, bs_f, cf_f)
        signal      = _m_signal(m_score)
        reliability = _score_reliability(comp_count) if m_score is not None else "insufficient"

        # ── Accruals ──
        accruals, accruals_err = _compute_accruals(is_f, bs_f, cf_f)

        # ── Working capital ──
        wc = _compute_wc(is_f, bs_f)

        # ── Capex / D&A ──
        capex_da, capex_da_err = _compute_capex_da(cf_f, is_f)

        # ── WC sub-item decomposition ──
        wc_decomp, wc_decomp_err = _compute_wc_decomposition(cf_f, is_f)

        # ── Active flags (PM-facing) ──
        active_flags = []

        if signal == "MANIPULATION_RISK":
            active_flags.append({
                "id": "BENEISH_MANIPULATION_RISK", "severity": "HIGH",
                "detail": (f"M-Score {m_score} exceeds {M_SCORE_MANIPULATION_THRESHOLD} threshold "
                           f"(reliability: {reliability}, {comp_count}/8 components)"),
                "next_action": (
                    "§2.3 blocking condition: audit revenue recognition policy, "
                    "accounts receivable aging, and accruals disclosure in primary filing "
                    "before Step 6 valuation proceeds."
                ),
            })
        elif signal == "WATCH":
            active_flags.append({
                "id": "BENEISH_WATCH", "severity": "WARNING",
                "detail": (f"M-Score {m_score} in watch zone "
                           f"({M_SCORE_WATCH_THRESHOLD} to {M_SCORE_MANIPULATION_THRESHOLD}) "
                           f"(reliability: {reliability}, {comp_count}/8 components)"),
                "next_action": (
                    "Log in Falsification Case. Check for accounting policy changes affecting "
                    "comparability. Cross-check revenue and gross margin against primary filing."
                ),
            })

        if accruals and accruals["signal"] == "HIGH":
            active_flags.append({
                "id": "ACCRUALS_HIGH", "severity": "WARNING",
                "detail": f"Accruals ratio {accruals['ratio_pct']}% — {accruals['interpretation']}",
                "next_action": (
                    "Inspect revenue recognition policy and accounting policy change notes "
                    "in primary filing. §2.3 blocking condition applies if co-occurring "
                    "with BENEISH_MANIPULATION_RISK."
                ),
            })

        # WC flags aggregated into a single active_flag entry; detail sub-items stay in
        # working_capital.working_capital_flags for structured access
        wc_flag_list = wc.get("working_capital_flags", [])
        if wc_flag_list:
            wc_detail = wc_flag_list
            # Enrich with sub-item decomposition if available
            if wc_decomp and wc_decomp.get("dominant_cash_drain"):
                wc_detail = wc_flag_list + [
                    f"Dominant WC cash drain: {wc_decomp['dominant_cash_drain']}"
                ]
            active_flags.append({
                "id": "WORKING_CAPITAL_DETERIORATION", "severity": "WARNING",
                "detail": wc_detail,
                "next_action": (
                    "Cross-check with MD&A cash conversion commentary. Assess receivables quality, "
                    "customer payment terms, and supplier relationship disclosures."
                ),
            })

        # Capex/D&A flags
        if capex_da and capex_da["signal"] == "CAPEX_OPEX_RECLASSIFICATION":
            active_flags.append({
                "id": "CAPEX_OPEX_RECLASSIFICATION", "severity": "WARNING",
                "detail": capex_da["interpretation"],
                "next_action": (
                    "Verify in MD&A whether company shifted from equipment lease (capex) to "
                    "service procurement (opex). If confirmed, COGS increase is structural "
                    "reclassification, not cost deterioration. Do not flag as underinvestment."
                ),
            })
        elif capex_da and capex_da["signal"] == "UNDERINVESTMENT":
            active_flags.append({
                "id": "CAPEX_UNDERINVESTMENT", "severity": "WARNING",
                "detail": capex_da["interpretation"],
                "next_action": (
                    "Assess whether underinvestment is intentional (harvest mode) or "
                    "signals declining competitiveness. Check capex guidance in MD&A."
                ),
            })
        elif capex_da and capex_da["signal"] == "HEAVY_EXPANSION":
            active_flags.append({
                "id": "CAPEX_HEAVY_EXPANSION", "severity": "WARNING",
                "detail": capex_da["interpretation"],
                "next_action": (
                    "Verify ROIC on incremental capex vs WACC. Check whether expansion "
                    "is value-accretive or obligatory reinvestment."
                ),
            })

        data_gaps = comp_count < 8 or not is_f or not bs_f or not cf_f
        status    = "partial_data" if data_gaps else "ok"

        return {
            "status": status,
            "meta": {
                "script_version": SCRIPT_VERSION,
                "ticker":         ticker_str,
                "company":        company_name,
                "sector":         sector,
                "period_t":       period_t,
                "period_t1":      period_t1,
                "run_date":       datetime.now().strftime("%Y-%m-%d"),
                "role": (
                    "Quantitative forensic screening only — not a final verdict. "
                    "Verify all signals against primary filing disclosures before PM Verdict."
                ),
            },
            "m_score": {
                "value":                m_score,
                "signal":               signal,
                "partial_score":        comp_count < 8,
                "score_reliability":    reliability,
                "components_available": f"{comp_count}/8",
                "missing_components":   missing,
                "components":           components,
                "thresholds": {
                    "MANIPULATION_RISK": f"> {M_SCORE_MANIPULATION_THRESHOLD}",
                    "WATCH":             f"{M_SCORE_WATCH_THRESHOLD} to {M_SCORE_MANIPULATION_THRESHOLD}",
                    "CLEAN":             f"< {M_SCORE_WATCH_THRESHOLD}",
                },
                "computation_notes": m_notes,
            },
            "accruals":        accruals if accruals else {"error": accruals_err},
            "capex_da":        capex_da if capex_da else {"error": capex_da_err},
            "wc_decomposition": wc_decomp if wc_decomp else {"error": wc_decomp_err},
            "working_capital": wc,
            "forensic_summary": {
                "active_flags": active_flags,
                "flag_count":   len(active_flags),
                "pm_note": (
                    "M-Score and accruals ratio are probabilistic screening tools, not verdicts. "
                    "BENEISH_MANIPULATION_RISK → §2.3 blocking condition: audit the relevant "
                    "primary filing section before Step 6. "
                    "BENEISH_WATCH / ACCRUALS_HIGH / WORKING_CAPITAL_DETERIORATION → log as "
                    "Text Evidence findings; address in Falsification Case. "
                    "Policy change detection is an external trigger (Step 3 Text Pre-Scan); "
                    "this script provides quantitative corroboration only."
                ),
            },
        }

    except Exception as e:
        return {"status": "error", "message": str(e), "ticker": ticker_str}


# ── Human-readable output ─────────────────────────────────────────────────────

def _v(x, suffix="", na="n/a"):
    return f"{x}{suffix}" if x is not None else na


def print_report(report):
    if report.get("status") == "error":
        print(f"ERROR: {report.get('message', 'Unknown error')}", file=sys.stderr)
        return

    m   = report["meta"]
    pt  = m.get("period_t",  "t")
    pt1 = m.get("period_t1", "t-1")

    print(f"\n========== [{m['ticker']}] Forensic Accounting Report ==========")
    print(f"Company : {m['company']}")
    print(f"Sector  : {m['sector']}  |  Run: {m['run_date']}  |  Script v{m['script_version']}")
    print(f"Periods : t={pt}  |  t-1={pt1}")
    print(f"Status  : {report['status'].upper()}")
    print(f"Role    : {m['role']}")
    print(f"\n{'─' * 62}")

    # ── M-Score ──
    ms = report["m_score"]
    signal_label = {
        "MANIPULATION_RISK": "[HIGH]  MANIPULATION RISK",
        "WATCH":             "[WARN]  WATCH ZONE",
        "CLEAN":             "[ OK ]  CLEAN",
        "INSUFFICIENT_DATA": "[ N/A ] INSUFFICIENT DATA",
    }.get(ms["signal"], ms["signal"])
    rel_label = {
        "high":         "HIGH (8/8 components)",
        "medium":       "MEDIUM (6-7/8 components)",
        "low":          "LOW (5/8 components)",
        "insufficient": "INSUFFICIENT (<5 components — score not computed)",
    }

    print(f"\n── Beneish M-Score ──────────────────────────────────────")
    print(f"  Score       : {_v(ms['value'])}")
    print(f"  Signal      : {signal_label}")
    print(f"  Reliability : {rel_label.get(ms['score_reliability'], ms['score_reliability'])}")
    print(f"  Partial     : {ms['partial_score']}")
    if ms["missing_components"]:
        print(f"  Missing     : {', '.join(ms['missing_components'])}")
    if ms["components"]:
        rows = [f"{k}={v}" for k, v in ms["components"].items()]
        print(f"  Components  : {' | '.join(rows)}")
    thres = ms["thresholds"]
    print(f"  Thresholds  : MANIPULATION_RISK {thres['MANIPULATION_RISK']} | "
          f"WATCH {thres['WATCH']} | CLEAN {thres['CLEAN']}")
    if ms["computation_notes"]:
        for note in ms["computation_notes"]:
            print(f"    * {note}")

    # ── Accruals ──
    ac = report["accruals"]
    print(f"\n── Accruals Quality  (t={pt}) ───────────────────────────")
    if "error" in ac:
        print(f"  Unavailable: {ac['error']}")
    else:
        sig_label = {
            "HIGH":              "[WARN]  HIGH — accruals-driven earnings",
            "MODERATE":          "[WARN]  MODERATE — monitor trend",
            "LOW":               "[ OK ]  LOW — cash flow supports earnings",
            "MODERATELY_NEGATIVE": "[ OK ]  MODERATELY NEGATIVE — CF > earnings",
            "STRONGLY_NEGATIVE": "[ OK ]  STRONGLY NEGATIVE — conservative accounting",
        }.get(ac["signal"], ac["signal"])
        print(f"  Signal   : {sig_label}")
        print(f"  Ratio    : {ac['ratio_pct']}%")
        print(f"  NI={_v(ac.get('net_income_bn'), 'bn')}  "
              f"OCF={_v(ac.get('operating_cf_bn'), 'bn')}  "
              f"Avg-TA={_v(ac.get('avg_total_assets_bn'), 'bn')}  (units ×10^8)")
        print(f"  {ac['interpretation']}")

    # ── Capex / D&A ──
    cd = report.get("capex_da", {})
    print(f"\n── Capex / D&A Ratio  (t={pt}) ─────────────────────────")
    if "error" in cd:
        print(f"  Unavailable: {cd['error']}")
    else:
        sig_icon = {"UNDERINVESTMENT": "[!]", "HEAVY_EXPANSION": "[!]", "NORMAL": "[ ]"}.get(cd["signal"], "[-]")
        print(f"  Ratio    : {cd['ratio']}  {sig_icon} {cd['signal']}")
        print(f"  Capex={_v(cd.get('capex_bn'), 'bn')}  D&A={_v(cd.get('da_bn'), 'bn')}  (units ×10^8)")
        print(f"  {cd['interpretation']}")

    # ── WC Sub-Item Decomposition ──
    wd = report.get("wc_decomposition", {})
    print(f"\n── WC Sub-Item Decomposition  (t={pt}) ─────────────────")
    if "error" in wd:
        print(f"  Unavailable: {wd['error']}")
    else:
        for key in ("change_in_receivables_bn", "change_in_inventory_bn", "change_in_payables_bn"):
            if key in wd:
                label = key.replace("_bn", "").replace("change_in_", "").replace("_", " ").title()
                print(f"  {label}: {wd[key]}bn  (units ×10^8)")
        if wd.get("dominant_cash_drain"):
            print(f"  Dominant cash drain: {wd['dominant_cash_drain']}")
        for note in wd.get("notes", []):
            print(f"    * {note}")

    # ── Working Capital ──
    wc = report["working_capital"]
    print(f"\n── Working Capital  (t-1={pt1} → t={pt}) ───────────────")
    icons = {"DETERIORATING": "[!]", "IMPROVING": "[+]", "STABLE": "[ ]", "UNAVAILABLE": "[-]"}
    for label, key in [("DSO", "dso"), ("DIO", "dio"), ("DPO", "dpo"), ("CCC", "ccc")]:
        cur   = wc.get(f"{key}_current")
        prior = wc.get(f"{key}_prior")
        trend = wc.get(f"{key}_trend", "UNAVAILABLE")
        icon  = icons.get(trend, "[-]")
        print(f"  {label}: {_v(prior, 'd')} → {_v(cur, 'd')}  {icon} {trend}")

    # ── Summary ──
    fs = report["forensic_summary"]
    print(f"\n── Forensic Flags: {fs['flag_count']} active ─────────────────────────")
    if fs["active_flags"]:
        for f in fs["active_flags"]:
            print(f"  [{f['severity']}] {f['id']}")
            detail = f["detail"]
            if isinstance(detail, list):
                for item in detail:
                    print(f"    - {item}")
            else:
                print(f"    {detail}")
            print(f"    Action: {f['next_action']}")
    else:
        print("  No forensic flags triggered.")

    print(f"\n{'─' * 62}")
    print(f"PM note: {fs['pm_note']}\n")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Forensic screen: Beneish M-Score, accruals quality, working capital trends"
    )
    parser.add_argument("ticker", help="Ticker symbol (e.g. AAPL, 02318.HK)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON only (suppresses human-readable report)")
    args = parser.parse_args()

    result = run_forensic_analysis(args.ticker)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)
