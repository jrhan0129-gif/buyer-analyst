import sys
import json
import yfinance as yf
from datetime import datetime

SCRIPT_VERSION = "2.1"

FINANCIAL_KEYWORDS = ['bank', 'insurance', 'financial']
NORMAL_LEVERAGE_THRESHOLD = 2.0
FINANCIAL_LEVERAGE_THRESHOLD = 15.0


def check_data_freshness(stock):
    try:
        fiscal_year_end = stock.info.get('lastFiscalYearEnd', None)
        if fiscal_year_end:
            last_date = datetime.fromtimestamp(fiscal_year_end)
            days_stale = (datetime.now() - last_date).days
            if days_stale > 180:
                return False, f"Last fiscal year end was {days_stale} days ago"
        return True, "ok"
    except Exception:
        return None, "Unable to verify data freshness"


def detect_burn_acceleration(stock):
    try:
        cf = stock.quarterly_cashflow
        if cf is None or cf.empty:
            return None, "Quarterly cash flow data unavailable"
        ocf_row = None
        for label in ['Operating Cash Flow', 'Total Cash From Operating Activities']:
            if label in cf.index:
                ocf_row = cf.loc[label]
                break
        if ocf_row is None:
            return None, "Operating cash flow row not found"
        values = ocf_row.dropna().values
        if len(values) < 2:
            return None, "Insufficient quarterly data"
        mrq = float(values[0])
        prior_avg = float(sum(values[1:4]) / len(values[1:4]))
        if mrq < 0 and prior_avg < 0 and mrq < prior_avg:
            pct = abs((mrq - prior_avg) / abs(prior_avg)) * 100
            return True, {"mrq_bn": round(mrq / 1e8, 2),
                          "prior_avg_bn": round(prior_avg / 1e8, 2),
                          "acceleration_pct": round(pct, 1)}
        return False, {"mrq_bn": round(mrq / 1e8, 2),
                       "prior_avg_bn": round(prior_avg / 1e8, 2)}
    except Exception as e:
        return None, f"Error: {str(e)}"


# Plausibility band in percentage points (compared against om_pct = om * 100).
# Catches implausibly extreme values from yfinance denominator distortion.
# Does NOT flag normal profitable companies — e-commerce ~20%, software ~40%, etc.
OPERATING_MARGIN_PLAUSIBILITY_BAND_DEFAULT = (-5.0, 60.0)

# Sector-specific overrides (matched via substring against yfinance sector string, lowercased).
# Extend this dict as new sectors are encountered; keys should stay yfinance-style.
OPERATING_MARGIN_SECTOR_OVERRIDES = {
    "technology": (-5.0, 70.0),
    "communication services": (-5.0, 70.0),
    "healthcare": (-5.0, 70.0),
    "real estate": (-5.0, 45.0),   # Keep signal; skip would hide impairment risk
    "consumer cyclical": (-5.0, 35.0),
    "consumer defensive": (-5.0, 30.0),
    "industrials": (-5.0, 35.0),
    "basic materials": (-5.0, 35.0),
    "energy": (-5.0, 40.0),
    "utilities": (-5.0, 35.0),
    "auto manufacturers": (-5.0, 25.0),
}

# Industry-level overrides (checked BEFORE sector). Key = yfinance industry substring (lowercased).
# These handle sub-sectors where the broad sector band is too narrow.
OPERATING_MARGIN_INDUSTRY_OVERRIDES = {
    "gold": (-10.0, 70.0),           # gold miners: high operating leverage at elevated prices
    "silver": (-10.0, 65.0),
    "copper": (-10.0, 55.0),
    "mining": (-10.0, 60.0),          # general mining at commodity peaks
    "oil & gas e&p": (-10.0, 55.0),
    "oil & gas integrated": (-10.0, 45.0),
    "biotechnology": (-50.0, 70.0),   # biotech: pre-revenue = deeply negative OPM
    "software": (-30.0, 70.0),        # SaaS: loss-making or very profitable
    "semiconductors": (-5.0, 65.0),
    "airlines": (-15.0, 25.0),
    "reit": (-10.0, 80.0),            # REITs: triple-net leases = very high margins
    "real estate services": (-5.0, 50.0),
    "asset management": (-5.0, 60.0),  # AUM-based fee income = high OPM
    "insurance": (-10.0, 40.0),
    "capital markets": (-10.0, 50.0),
}
# Sectors where operating margin check is skipped: metric is structurally misleading.
# Covers both broad yfinance sector strings and narrower industry strings.
OPERATING_MARGIN_SECTOR_SKIP = {
    "financial services", "financials",
    "bank", "insurance",
}

# Industries with captive finance arms — LEVERAGE_ESCALATION threshold should be relaxed
# because D/E reflects financial-services book, not industrial leverage
CAPTIVE_FINANCE_INDUSTRIES = {
    "farm & heavy construction machinery",  # Deere, CNH
    "auto manufacturers",  # GM, Ford (GMAC/Ford Motor Credit)
    "conglomerates",  # GE, etc.
}


# ─── Sector-aware flag skip rules (harness_editor P0 #2, 2026-04-22) ─────
# Balance-sheet-driven and profitable cyclical businesses should not trigger
# SaaS-style "burn / runway / margin fragility" flags. Each rule is a predicate
# (sector_lower, industry_lower, info) -> bool; True = skip this flag.
_FINANCIAL_SECTOR_KEYS = ("financial services", "financials")
_BALANCE_SHEET_INDUSTRY_KEYS = (
    "insurance", "asset management", "capital markets",
    "bank", "credit services",
)

def _is_balance_sheet_driven(s, ind):
    return (any(k in s for k in _FINANCIAL_SECTOR_KEYS)
            or any(k in ind for k in _BALANCE_SHEET_INDUSTRY_KEYS))

def _is_profitable_oem(ind, info):
    return ("auto manufacturers" in ind
            and (info.get("operatingMargins") or 0) > 0.02)

def _is_cash_positive_airline(ind, info):
    return ("airlines" in ind
            and (info.get("operatingCashflow") or 0) > 0)

CHECK_SKIP_RULES = {
    "BURN_DATA_UNAVAILABLE": [
        lambda s, ind, info: _is_balance_sheet_driven(s, ind),
        lambda s, ind, info: _is_profitable_oem(ind, info),
        lambda s, ind, info: _is_cash_positive_airline(ind, info),
    ],
    "RUNWAY_STRESS": [
        lambda s, ind, info: _is_balance_sheet_driven(s, ind),
        lambda s, ind, info: _is_profitable_oem(ind, info),
        lambda s, ind, info: _is_cash_positive_airline(ind, info),
    ],
    "CASH_COVERAGE_WEAK": [
        # Insurance has C-ROSS / solvency II; asset managers have AUM-based models
        lambda s, ind, info: _is_balance_sheet_driven(s, ind),
    ],
    "MARGIN_FRAGILITY": [
        # Belt-and-suspenders: margin band already returns None for financials,
        # but cover edge cases where industry classification misfires
        lambda s, ind, info: _is_balance_sheet_driven(s, ind),
    ],
}

def _should_skip_flag(flag_id, sector, industry, info):
    rules = CHECK_SKIP_RULES.get(flag_id, [])
    s = (sector or "").lower()
    ind = (industry or "").lower()
    for rule in rules:
        if rule(s, ind, info):
            return True
    return False


def _get_margin_band(sector: str, industry: str = ""):
    """Return (lo, hi) band. Checks industry first, then sector, then default.
    Returns None to signal skip (financials)."""
    s = sector.lower() if sector else ""
    ind = industry.lower() if industry else ""
    if any(k in s for k in OPERATING_MARGIN_SECTOR_SKIP):
        return None
    # Industry override takes priority (more specific)
    for key, band in OPERATING_MARGIN_INDUSTRY_OVERRIDES.items():
        if key in ind:
            return band
    for key, band in OPERATING_MARGIN_SECTOR_OVERRIDES.items():
        if key in s:
            return band
    return OPERATING_MARGIN_PLAUSIBILITY_BAND_DEFAULT


# Sector-specific gross margin thresholds: (critical, healthy)
GM_SECTOR_THRESHOLDS = {
    "technology": (0.30, 0.45),
    "communication services": (0.30, 0.45),
    "healthcare": (0.30, 0.45),
    "consumer cyclical": (0.20, 0.30),
    "consumer defensive": (0.20, 0.30),
    "discount stores": (0.08, 0.15),  # Warehouse clubs (COST, WMT) have structurally low GM
    "grocery stores": (0.20, 0.30),
    "industrials": (0.10, 0.20),
    "basic materials": (0.10, 0.45),  # Wide range: mining (iron ore/copper >40%) vs chemicals (<15%)
    "energy": (0.10, 0.20),
    "real estate": (0.12, 0.22),
    "financial services": (None, None),  # Banks/insurers/payment networks don't report meaningful gross margin
    "financials": (None, None),
    "credit services": (None, None),  # Visa, Mastercard — GM >95% is structural, not informative
    "financial data": (None, None),  # Bloomberg, MSCI, etc.
    "auto manufacturers": (0.10, 0.22),  # Auto GM structurally lower; >20% is excellent
    "utilities": (0.20, 0.40),  # Regulated utilities have wide GM range
}
GM_DEFAULT_THRESHOLDS = (0.25, 0.40)


def _get_gm_thresholds(sector: str):
    s = sector.lower() if sector else ""
    for key, thresholds in GM_SECTOR_THRESHOLDS.items():
        if key in s:
            return thresholds
    return GM_DEFAULT_THRESHOLDS


def detect_margin_fragility(info, sector: str = "", industry: str = ""):
    gm = info.get('grossMargins', None)
    om = info.get('operatingMargins', None)
    if gm is None:
        return None, "Gross margin data unavailable"

    notes = []
    gm_critical, gm_healthy = _get_gm_thresholds(sector)

    # Skip GM check for sectors where gross margin is not meaningful (banks, insurers)
    if gm_critical is None or gm_healthy is None:
        notes.append(f"Gross margin check skipped — not meaningful for {sector or 'this sector'}")
        # Still check operating margin below
    elif gm < gm_critical:
        notes.append(f"Gross margin critically low: {round(gm * 100, 1)}% (sector threshold: {round(gm_critical * 100)}%)")
    elif gm < gm_healthy:
        notes.append(f"Gross margin below healthy threshold: {round(gm * 100, 1)}% (sector threshold: {round(gm_healthy * 100)}%)")

    # Operating margin: sector-aware plausibility check
    if om is not None:
        om_pct = round(om * 100, 1)
        band = _get_margin_band(sector, industry)
        if band is None:
            pass  # Financial/insurance sector: operating margin not a meaningful check
        else:
            lo, hi = band
            if om_pct < lo or om_pct > hi:
                return "data_anomaly", {
                    "gross_margin_pct": round(gm * 100, 1),
                    "operating_margin_pct": om_pct,
                    "sector": sector or "unknown",
                    "anomaly_reason": (
                        f"Implausibly extreme operating margin: {om_pct}% falls outside "
                        f"sector plausibility band ({lo}% to {hi}%"
                        f" for '{sector or 'default'}')."
                        " Likely denominator distortion in source data."
                        " Confirm margin from source filings or report tables before judgment."
                    )
                }
            elif om_pct < -20.0:
                notes.append(f"Operating margin deeply negative: {om_pct}%")

    return bool(notes), {"gross_margin_pct": round(gm * 100, 1),
                         "operating_margin_pct": round(om * 100, 1) if om is not None else None,
                         "notes": notes}


def detect_leverage_risk(info, sector):
    de = info.get('debtToEquity', None)
    if de is None:
        return None, "Debt/equity data unavailable"
    de_ratio = de / 100 if de > 10 else de
    is_financial = any(k in sector.lower() for k in FINANCIAL_KEYWORDS)
    industry = (info.get('industry', '') or '').lower()
    has_captive_finance = any(k in industry for k in CAPTIVE_FINANCE_INDUSTRIES)
    # Captive finance industries use relaxed threshold (financial-level)
    if has_captive_finance:
        threshold = FINANCIAL_LEVERAGE_THRESHOLD
    elif is_financial:
        threshold = FINANCIAL_LEVERAGE_THRESHOLD
    else:
        threshold = NORMAL_LEVERAGE_THRESHOLD
    if de_ratio > threshold:
        detail = {"debt_to_equity": round(de_ratio, 2),
                  "threshold": threshold,
                  "is_financial_sector": is_financial}
        if has_captive_finance:
            detail["note"] = "Captive finance arm detected — D/E reflects financial book, not industrial leverage"
        return True, detail
    return False, {"debt_to_equity": round(de_ratio, 2), "threshold": threshold}


# Sector-specific current ratio thresholds: (critical, warning)
CR_SECTOR_THRESHOLDS = {
    "technology": (0.8, 1.2),
    "communication services": (0.8, 1.2),
    "consumer cyclical": (0.7, 1.0),
    "consumer defensive": (0.7, 1.0),
    "industrials": (0.7, 1.0),
    "basic materials": (0.7, 1.0),
    "energy": (0.7, 1.0),
    "real estate": (0.5, 0.8),
    "financial services": (0.5, 0.8),
    "financials": (0.5, 0.8),
}
CR_DEFAULT_THRESHOLDS = (0.8, 1.2)


def _get_cr_thresholds(sector: str):
    s = sector.lower() if sector else ""
    for key, thresholds in CR_SECTOR_THRESHOLDS.items():
        if key in s:
            return thresholds
    return CR_DEFAULT_THRESHOLDS


SHORT_DEBT_RATIO_THRESHOLD = 0.40
CASH_COVERAGE_THRESHOLD = 1.0


def _get_current_debt(ticker_str):
    """Extract current debt from balance sheet (info often lacks it)."""
    try:
        stock = yf.Ticker(ticker_str)
        info = stock.info or {}
        # Try info first
        cd = info.get('currentDebt')
        if cd is not None:
            return cd
        # Fall back to balance sheet
        bal = stock.balance_sheet
        if bal is not None and not bal.empty:
            for label in ["Current Debt", "Current Debt And Capital Lease Obligation",
                          "Current Capital Lease Obligation", "Short Long Term Debt"]:
                if label in bal.index:
                    val = bal.loc[label].iloc[0]
                    if val is not None and not (val != val):  # NaN guard
                        return float(val)
    except Exception:
        pass
    return None


def detect_debt_maturity_risk(info, ticker_str=None):
    """Flag when short-term debt is disproportionately high relative to total debt."""
    current_debt = info.get('currentDebt', None)
    if current_debt is None and ticker_str:
        current_debt = _get_current_debt(ticker_str)
    total_debt = info.get('totalDebt', None)
    if current_debt is None or total_debt is None or total_debt <= 0:
        return None, "Short-term or total debt data unavailable"
    ratio = current_debt / total_debt
    if ratio > SHORT_DEBT_RATIO_THRESHOLD:
        return True, {"short_debt_ratio": round(ratio, 3),
                      "current_debt_bn": round(current_debt / 1e8, 2),
                      "total_debt_bn": round(total_debt / 1e8, 2),
                      "threshold": SHORT_DEBT_RATIO_THRESHOLD}
    return False, {"short_debt_ratio": round(ratio, 3),
                   "current_debt_bn": round(current_debt / 1e8, 2),
                   "total_debt_bn": round(total_debt / 1e8, 2)}


def detect_cash_coverage_risk(info, ticker_str=None):
    """Flag when cash is insufficient to cover current debt obligations."""
    total_cash = info.get('totalCash', None)
    current_debt = info.get('currentDebt', None)
    if current_debt is None and ticker_str:
        current_debt = _get_current_debt(ticker_str)
    if total_cash is None or current_debt is None or current_debt <= 0:
        return None, "Cash or current debt data unavailable"
    ratio = total_cash / current_debt
    if ratio < CASH_COVERAGE_THRESHOLD:
        return True, {"cash_coverage_ratio": round(ratio, 3),
                      "total_cash_bn": round(total_cash / 1e8, 2),
                      "current_debt_bn": round(current_debt / 1e8, 2),
                      "threshold": CASH_COVERAGE_THRESHOLD}
    return False, {"cash_coverage_ratio": round(ratio, 3),
                   "total_cash_bn": round(total_cash / 1e8, 2),
                   "current_debt_bn": round(current_debt / 1e8, 2)}


def detect_liquidity_stress(info, sector: str = ""):
    cr = info.get('currentRatio', None)
    fcf = info.get('freeCashflow', None)
    notes = []
    data = {}
    cr_critical, cr_warning = _get_cr_thresholds(sector)
    if cr is not None:
        data['current_ratio'] = cr
        if cr < cr_critical:
            notes.append(f"Current ratio critically low: {cr} (sector threshold: {cr_critical})")
        elif cr < cr_warning:
            notes.append(f"Current ratio below safety margin: {cr} (sector threshold: {cr_warning})")
    if fcf is not None:
        data['fcf_bn'] = round(fcf / 1e8, 2)
        if fcf < 0:
            notes.append(f"Negative FCF: {round(fcf / 1e8, 2)}bn")
    return bool(notes), {"indicators": data, "notes": notes}


def run_health_check(ticker):
    try:
        # ── Bug #4 fix (2026-04-22): retry + ── ──────────────────────────
        # Bug #3 fix: guard against silent yfinance failures. An empty or
        # trivial info dict must not be treated as "clean" — must explicitly
        # flag DATA_UNAVAILABLE and return status=data_unavailable.
        import time as _time
        stock = None
        info = {}
        last_err = None
        for attempt in range(3):
            try:
                stock = yf.Ticker(ticker)
                info = stock.info or {}
                if info and len(info) >= 10:
                    break  # Got substantive data
            except Exception as e:
                last_err = e
            if attempt < 2:
                _time.sleep(2 ** attempt)  # 1s, 2s backoff

        # Bug #3 guard: if info is empty or trivially small, yfinance silently failed
        if not info or len(info) < 10:
            return {
                "status": "data_unavailable",
                "meta": {
                    "script_version": SCRIPT_VERSION, "ticker": ticker,
                    "run_date": datetime.now().strftime("%Y-%m-%d"),
                    "role": "Risk triage only — yfinance returned insufficient data",
                    "last_error": str(last_err) if last_err else None,
                },
                "active_flags": [{
                    "id": "DATA_UNAVAILABLE",
                    "severity": "HIGH",
                    "detail": (f"yfinance returned {len(info)} fields after 3 retries. "
                               f"Cannot run health check on this ticker in this session."),
                    "next_action": "Retry later, or verify ticker symbol, or use data_utils.get_yf_info cache.",
                }],
                "skipped_flags": [],
                "mandatory_visual_tasks": [],
                "candidate_tier": "UNKNOWN — data unavailable",
                "flag_count": {"total": 1, "high_severity": 1, "skipped": 0},
            }

        sector = info.get('sector', info.get('industry', 'Unknown'))
        industry = info.get('industry', '')
        company_name = info.get('longName', ticker)
        active_flags = []
        skipped_flags = []  # Track what we skipped per sector rules (transparency for harness_editor)
        mandatory_visual_tasks = []

        def _emit(flag_dict):
            """Gate flag emission through sector skip rules."""
            flag_id = flag_dict.get("id")
            if _should_skip_flag(flag_id, sector, industry, info):
                skipped_flags.append({"id": flag_id, "reason": f"sector-aware skip (sector={sector}, industry={industry})"})
                return
            active_flags.append(flag_dict)

        # Data freshness
        fresh, freshness_note = check_data_freshness(stock)
        if fresh is False:
            active_flags.append({"id": "DATA_STALE", "severity": "WARNING",
                                  "detail": freshness_note})

        # Burn acceleration
        burn_accel, burn_detail = detect_burn_acceleration(stock)
        if burn_accel is True:
            _emit({"id": "BURN_ACCELERATION", "severity": "HIGH",
                   "detail": burn_detail,
                   "next_action": "Audit cash flow statement / liquidity disclosure"})
            mandatory_visual_tasks.append("Audit cash flow statement or liquidity disclosure")
        elif burn_accel is None:
            _emit({"id": "BURN_DATA_UNAVAILABLE", "severity": "WARNING",
                   "detail": burn_detail})

        # Margin fragility
        margin_result, margin_detail = detect_margin_fragility(info, sector, industry)
        if margin_result == "data_anomaly":
            _emit({"id": "MARGIN_DATA_ANOMALY", "severity": "WARNING",
                   "detail": margin_detail,
                   "next_action": (
                       "Confirm operating margin from source filings or report tables before "
                       "assigning MARGIN_FRAGILITY. If other profitability flags are also active "
                       "(e.g., gross margin weakness), PM may combine signals and escalate to a "
                       "targeted visual review of the margin disclosure."
                   )})
        elif margin_result is True:
            _emit({"id": "MARGIN_FRAGILITY", "severity": "ANOMALY",
                   "detail": margin_detail,
                   "next_action": "Audit gross margin bridge chart or cost breakdown table"})
            mandatory_visual_tasks.append("Audit gross margin bridge / cost breakdown table")

        # Leverage
        leverage_risk, leverage_detail = detect_leverage_risk(info, sector)
        if leverage_risk is True:
            _emit({"id": "LEVERAGE_ESCALATION", "severity": "ANOMALY",
                   "detail": leverage_detail,
                   "next_action": "Audit Capital Adequacy note or regulatory capital / funding structure disclosure"})
            mandatory_visual_tasks.append("Audit Capital Adequacy note / funding structure disclosure")

        # Liquidity stress
        liq_stress, liq_detail = detect_liquidity_stress(info, sector=sector)
        if liq_stress:
            _emit({"id": "RUNWAY_STRESS", "severity": "HIGH",
                   "detail": liq_detail,
                   "next_action": "Audit liquidity disclosure and near-term debt maturity schedule"})
            mandatory_visual_tasks.append("Audit liquidity disclosure / debt maturity schedule")

        # Debt maturity risk
        debt_mat, debt_mat_detail = detect_debt_maturity_risk(info, ticker_str=ticker)
        if debt_mat is True:
            _emit({"id": "DEBT_MATURITY_RISK", "severity": "ANOMALY",
                   "detail": debt_mat_detail,
                   "next_action": "Audit debt maturity schedule and refinancing capacity"})
            mandatory_visual_tasks.append("Audit debt maturity schedule and refinancing capacity")

        # Cash coverage risk
        cash_cov, cash_cov_detail = detect_cash_coverage_risk(info, ticker_str=ticker)
        if cash_cov is True:
            _emit({"id": "CASH_COVERAGE_WEAK", "severity": "HIGH",
                   "detail": cash_cov_detail,
                   "next_action": "Audit liquidity disclosure — cash insufficient to cover current debt"})
            mandatory_visual_tasks.append("Audit liquidity disclosure — cash vs current debt")

        # Candidate Tier
        high_sev = [f for f in active_flags if f.get('severity') in ['HIGH', 'ANOMALY', 'STRESS']]
        if len(high_sev) >= 2:
            candidate_tier = "Potential Tier 2/3 (Pending PM Verification)"
        elif len(high_sev) == 1:
            candidate_tier = "Potential Tier 1/2 boundary (Pending PM Verification)"
        else:
            candidate_tier = "Potential Tier 1 (Pending PM Verification)"

        has_stale = any(f['id'] in ['DATA_STALE', 'BURN_DATA_UNAVAILABLE'] for f in active_flags)
        status = "partial_data" if has_stale else "ok"

        return {
            "status": status,
            "meta": {"script_version": SCRIPT_VERSION, "ticker": ticker,
                     "company": company_name, "sector": sector, "industry": industry,
                     "run_date": datetime.now().strftime("%Y-%m-%d"),
                     "role": "Risk triage only — not a final verdict. PM verification required."},
            "candidate_tier": candidate_tier,
            "active_flags": active_flags,
            "skipped_flags": skipped_flags,
            "mandatory_visual_tasks": mandatory_visual_tasks,
            "flag_count": {"total": len(active_flags), "high_severity": len(high_sev),
                           "skipped": len(skipped_flags)}
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "ticker": ticker
        }


def print_report(report):
    if report.get('status') == 'error':
        print(f"ERROR: {report.get('message', 'Unknown error')}")
        return

    m = report['meta']
    print(f"\n========== [{m['ticker']}] Financial Risk Triage Report ==========")
    print(f"Company: {m['company']} | Sector: {m['sector']} | {m['run_date']}")
    print(f"Status: {report['status'].upper()} | Script v{m['script_version']}")
    print(f"Role: {m['role']}")
    print(f"\n{'─' * 60}")
    print(f"Candidate Tier: {report['candidate_tier']}")
    print(f"Active Flags: {report['flag_count']['total']} total "
          f"({report['flag_count']['high_severity']} high-severity)")

    icons = {"HIGH": "[HIGH]", "ANOMALY": "[ANOMALY]", "STRESS": "[STRESS]", "WARNING": "[WARNING]"}
    if report['active_flags']:
        print(f"\n-- Active Flags --")
        for f in report['active_flags']:
            print(f"  {icons.get(f['severity'], '[INFO]')} {f['id']}")
            detail = f.get('detail', {})
            if isinstance(detail, dict):
                for k, v in detail.items():
                    if k == 'notes':
                        for n in v:
                            print(f"      -> {n}")
                    else:
                        print(f"      {k}: {v}")
            elif isinstance(detail, str):
                print(f"      {detail}")
            if 'next_action' in f:
                print(f"      Required action: {f['next_action']}")

    if report['mandatory_visual_tasks']:
        print(f"\n-- Mandatory Visual Audit Tasks --")
        for i, t in enumerate(report['mandatory_visual_tasks'], 1):
            print(f"  {i}. {t}")
    else:
        print(f"\nNo mandatory visual audit tasks triggered.")

    print(f"\n{'─' * 60}")
    print(f"PM must verify candidate_tier before §4.8 classification is assigned or relied upon.\n")


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    report = run_health_check(ticker)
    if '--json' in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
