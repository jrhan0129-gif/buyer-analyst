"""
short_interest.py — Short interest tracker for buy-side research
Usage:
    python3 skills/short_interest.py --ticker AAPL
    python3 skills/short_interest.py --ticker TSLA NIO RIVN

Source: yfinance (.info fields + institutional holder data)
Note: Exchange-reported short interest has a reporting lag (typically 2 weeks for US).
      Treat as directional positioning context, not precise real-time data.

Output: JSON with short_pct_float, shares_short, short_ratio (days-to-cover),
        float_shares, institutional_ownership, and trend context.
"""

import argparse
import json
import sys
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    print(json.dumps({"error": "yfinance not installed", "source": "short_interest"}))
    sys.exit(1)


def fetch_short_interest(ticker):
    try:
        t = yf.Ticker(ticker)
        info = t.info

        shares_short       = info.get("sharesShort")
        short_pct_float    = info.get("shortPercentOfFloat")
        short_ratio        = info.get("shortRatio")
        float_shares       = info.get("floatShares")
        shares_outstanding = info.get("sharesOutstanding")
        avg_volume         = info.get("averageVolume")
        inst_pct           = info.get("heldPercentInstitutions")
        insider_pct        = info.get("heldPercentInsiders")
        short_date         = info.get("dateShortInterest")

        date_str = None
        if short_date:
            try:
                date_str = datetime.fromtimestamp(short_date).strftime("%Y-%m-%d")
            except Exception:
                date_str = str(short_date)

        dtc_computed = None
        if shares_short and avg_volume and avg_volume > 0:
            dtc_computed = round(shares_short / avg_volume, 1)

        crowding_signal = "unknown"
        if short_pct_float is not None:
            pct = float(short_pct_float) * 100
            if pct >= 20:
                crowding_signal = "HIGH — short squeeze / crowding risk elevated"
            elif pct >= 10:
                crowding_signal = "MODERATE — notable short positioning"
            elif pct >= 5:
                crowding_signal = "LOW-MODERATE"
            else:
                crowding_signal = "LOW"

        result = {
            "ticker":               ticker.upper(),
            "name":                 info.get("shortName", ticker),
            "as_of_date":           date_str,
            "shares_short":         shares_short,
            "short_pct_float":      round(float(short_pct_float) * 100, 2) if short_pct_float else None,
            "short_ratio_days_to_cover": short_ratio,
            "dtc_computed":         dtc_computed,
            "float_shares":         float_shares,
            "shares_outstanding":   shares_outstanding,
            "avg_daily_volume":     avg_volume,
            "inst_ownership_pct":   round(float(inst_pct) * 100, 2) if inst_pct else None,
            "insider_ownership_pct":round(float(insider_pct) * 100, 2) if insider_pct else None,
            "crowding_signal":      crowding_signal,
            "current_price":        info.get("currentPrice") or info.get("regularMarketPrice"),
            "market_cap":           info.get("marketCap"),
        }
        return result, None
    except Exception as e:
        return {"ticker": ticker.upper(), "name": ticker.upper(), "short_pct_float": None,
                "short_ratio_days_to_cover": None, "crowding_signal": "error"}, str(e)


def main():
    parser = argparse.ArgumentParser(description="Short interest tracker")
    parser.add_argument("--ticker", nargs="+", required=True,
                        help="Ticker(s) to check (e.g. AAPL TSLA NIO)")
    args = parser.parse_args()

    results = []
    warnings = []

    for t in args.ticker:
        data, err = fetch_short_interest(t)
        if err:
            warnings.append(f"{t}: {err}")
        results.append(data)

    output = {
        "source":       "short_interest_yfinance",
        "retrieved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note":         (
            "Short interest from yfinance (exchange-reported, ~2-week lag for US). "
            "days_to_cover assumes average daily volume. "
            "Use as directional positioning context only — not precise real-time data."
        ),
        "positions":    results,
        "warnings":     warnings,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
