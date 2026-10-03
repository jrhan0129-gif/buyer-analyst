from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

try:
    import yfinance as yf
    YF_AVAILABLE = True
except Exception:
    YF_AVAILABLE = False

try:
    from financetoolkit import Toolkit  # type: ignore
    FTK_AVAILABLE = True
except Exception:
    FTK_AVAILABLE = False


# =========================
# Data layer
# =========================

@dataclass
class BaseMarketData:
    ticker: Optional[str]
    current_price: Optional[float]
    market_cap: Optional[float]
    enterprise_value: Optional[float]
    shares_outstanding: Optional[float]
    currency: Optional[str]
    sector: Optional[str]
    industry: Optional[str]
    source: str
    status: str
    notes: List[str]


@dataclass
class AdvancedQuantData:
    ticker: Optional[str]
    roic: Optional[float]
    altman_z_score: Optional[float]
    cash_flow_warning: Optional[bool]
    source: str
    status: str
    notes: List[str]
    raw_metrics: Dict[str, Any]


@dataclass
class PeerCompSnapshot:
    ticker: str
    price_to_sales: Optional[float]
    ev_to_gross_profit: Optional[float]
    market_cap: Optional[float]
    enterprise_value: Optional[float]
    gross_profit: Optional[float]
    source: str


@dataclass
class DataReconPacket:
    base_data_agent: BaseMarketData
    advanced_quant_agent: AdvancedQuantData
    peer_snapshots: List[PeerCompSnapshot]
    conflict_check: Dict[str, Any]


class BaseDataAgent:
    """Real-time market facts via yfinance. Must fail loudly rather than guess."""

    def fetch(self, ticker: Optional[str]) -> BaseMarketData:
        if not ticker:
            return BaseMarketData(
                ticker=None,
                current_price=None,
                market_cap=None,
                enterprise_value=None,
                shares_outstanding=None,
                currency=None,
                sector=None,
                industry=None,
                source="yfinance",
                status="skipped",
                notes=["No ticker provided."],
            )

        if not YF_AVAILABLE:
            return BaseMarketData(
                ticker=ticker,
                current_price=None,
                market_cap=None,
                enterprise_value=None,
                shares_outstanding=None,
                currency=None,
                sector=None,
                industry=None,
                source="yfinance",
                status="unavailable",
                notes=["yfinance not installed."],
            )

        try:
            stock = yf.Ticker(ticker)
            info = stock.info or {}
            fast = getattr(stock, "fast_info", {}) or {}

            current_price = _coalesce(
                fast.get("lastPrice"),
                fast.get("last_price"),
                info.get("currentPrice"),
                info.get("regularMarketPrice"),
            )
            market_cap = _coalesce(
                fast.get("marketCap"),
                fast.get("market_cap"),
                info.get("marketCap"),
            )
            enterprise_value = info.get("enterpriseValue")
            shares_outstanding = _coalesce(
                fast.get("shares"),
                info.get("sharesOutstanding"),
            )
            currency = info.get("currency")
            sector = info.get("sector")
            industry = info.get("industry")

            notes = []
            if current_price is None:
                notes.append("Current price unavailable from yfinance.")
            if market_cap is None:
                notes.append("Market cap unavailable from yfinance.")

            return BaseMarketData(
                ticker=ticker,
                current_price=_safe_float(current_price),
                market_cap=_safe_float(market_cap),
                enterprise_value=_safe_float(enterprise_value),
                shares_outstanding=_safe_float(shares_outstanding),
                currency=currency,
                sector=sector,
                industry=industry,
                source="yfinance",
                status="ok" if current_price is not None or market_cap is not None else "partial",
                notes=notes,
            )
        except Exception as e:
            return BaseMarketData(
                ticker=ticker,
                current_price=None,
                market_cap=None,
                enterprise_value=None,
                shares_outstanding=None,
                currency=None,
                sector=None,
                industry=None,
                source="yfinance",
                status="error",
                notes=[f"yfinance fetch failed: {e}"],
            )

    def fetch_peer_snapshots(self, peer_tickers: List[str]) -> List[PeerCompSnapshot]:
        snapshots: List[PeerCompSnapshot] = []
        if not peer_tickers or not YF_AVAILABLE:
            return snapshots

        for ticker in peer_tickers:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info or {}
                market_cap = _safe_float(info.get("marketCap"))
                enterprise_value = _safe_float(info.get("enterpriseValue"))
                gross_profit = _safe_float(info.get("grossProfits"))
                ps = _safe_float(
                    info.get("priceToSalesTrailing12Months")
                    or info.get("priceToSalesTrailing12Months")
                )
                evgp = None
                if enterprise_value is not None and gross_profit not in (None, 0):
                    evgp = enterprise_value / gross_profit

                snapshots.append(
                    PeerCompSnapshot(
                        ticker=ticker,
                        price_to_sales=ps,
                        ev_to_gross_profit=evgp,
                        market_cap=market_cap,
                        enterprise_value=enterprise_value,
                        gross_profit=gross_profit,
                        source="yfinance",
                    )
                )
            except Exception:
                snapshots.append(
                    PeerCompSnapshot(
                        ticker=ticker,
                        price_to_sales=None,
                        ev_to_gross_profit=None,
                        market_cap=None,
                        enterprise_value=None,
                        gross_profit=None,
                        source="yfinance",
                    )
                )
        return snapshots


class AdvancedQuantAgent:
    """Higher-value ratios via FinanceToolkit. Enhanced evidence, never overrides market facts."""

    def fetch(self, ticker: Optional[str]) -> AdvancedQuantData:
        if not ticker:
            return AdvancedQuantData(
                ticker=None,
                roic=None,
                altman_z_score=None,
                cash_flow_warning=None,
                source="financetoolkit",
                status="skipped",
                notes=["No ticker provided."],
                raw_metrics={},
            )

        if not FTK_AVAILABLE:
            return AdvancedQuantData(
                ticker=ticker,
                roic=None,
                altman_z_score=None,
                cash_flow_warning=None,
                source="financetoolkit",
                status="unavailable",
                notes=["FinanceToolkit not installed or import failed."],
                raw_metrics={},
            )

        # FinanceToolkit APIs vary by version. Keep the integration resilient.
        notes: List[str] = []
        raw_metrics: Dict[str, Any] = {}
        roic = None
        altman = None
        cash_flow_warning = None

        try:
            toolkit = Toolkit([ticker], start_date="2020-01-01")
            raw_metrics["toolkit_initialized"] = True

            try:
                ratios = toolkit.ratios.collect_profitability_ratios()
                raw_metrics["profitability_ratios_available"] = True
                # Best-effort extraction across versions/data layouts
                roic = _extract_metric(ratios, ["Return on Invested Capital", "ROIC", "returnOnInvestedCapital"])
            except Exception as e:
                notes.append(f"ROIC unavailable: {e}")

            try:
                models = toolkit.models.get_altman_z_score()
                raw_metrics["altman_available"] = True
                altman = _extract_metric(models, ["Altman Z-Score", "Altman Z Score", "altman_z_score"])
            except Exception as e:
                notes.append(f"Altman Z unavailable: {e}")

            cash_flow_warning = (altman is not None and altman < 1.8)
            status = "ok" if (roic is not None or altman is not None) else "partial"
            return AdvancedQuantData(
                ticker=ticker,
                roic=_safe_float(roic),
                altman_z_score=_safe_float(altman),
                cash_flow_warning=cash_flow_warning,
                source="financetoolkit",
                status=status,
                notes=notes,
                raw_metrics=raw_metrics,
            )
        except Exception as e:
            return AdvancedQuantData(
                ticker=ticker,
                roic=None,
                altman_z_score=None,
                cash_flow_warning=None,
                source="financetoolkit",
                status="error",
                notes=[f"FinanceToolkit fetch failed: {e}"],
                raw_metrics=raw_metrics,
            )


class DataReconLayer:
    def __init__(self) -> None:
        self.base_agent = BaseDataAgent()
        self.advanced_agent = AdvancedQuantAgent()

    def collect(self, ticker: Optional[str], peer_tickers: List[str]) -> DataReconPacket:
        base = self.base_agent.fetch(ticker)
        advanced = self.advanced_agent.fetch(ticker)
        peers = self.base_agent.fetch_peer_snapshots(peer_tickers)

        has_conflict = False
        rules = []
        if base.status == "error" and advanced.status in {"ok", "partial"}:
            rules.append("Base market facts unavailable; advanced metrics kept as supplemental only.")
        else:
            rules.append("Realtime market data priority for price / market cap / current multiples.")
            rules.append("FinanceToolkit metrics are supplemental and never override realtime facts.")

        return DataReconPacket(
            base_data_agent=base,
            advanced_quant_agent=advanced,
            peer_snapshots=peers,
            conflict_check={
                "has_conflict": has_conflict,
                "resolution_rule": "realtime_market_data_priority",
                "notes": rules,
            },
        )


# =========================
# Valuation layer
# =========================

@dataclass
class ValuationInputs:
    revenue: float
    gross_margin: float
    fcf: float
    growth_rate: float
    ticker: Optional[str] = None
    cash_balance: float = 0.0
    net_debt: float = 0.0
    shares_outstanding: float = 0.0
    current_price: float = 0.0
    segment_count: int = 1
    compute_cost_ratio: Optional[float] = None
    rule_of_40: Optional[float] = None
    fcf_growth_rate: float = 0.15
    discount_rate: float = 0.12
    terminal_growth_rate: float = 0.03
    projection_years: int = 5
    ps_bear: float = 6.0
    ps_base: float = 10.0
    ps_bull: float = 16.0
    evgp_bear: float = 12.0
    evgp_base: float = 18.0
    evgp_bull: float = 24.0
    peer_tickers: List[str] = None
    use_live_peers: bool = False
    share_scale: float = 1.0  # Auto-detected unit correction: equity_value × share_scale / shares = per-share price
    sector: str = ""
    bps: float = 0.0  # Book value per share (for P/B valuation)
    eps: float = 0.0  # Earnings per share (for P/E valuation)
    pb_bear: float = 0.3
    pb_base: float = 0.5
    pb_bull: float = 0.8
    pe_bear: float = 5.0
    pe_base: float = 8.0
    pe_bull: float = 12.0
    explicit_fcf: Optional[List[float]] = None  # Multi-year FCF override (years 1-N)
    sell_side_wacc: Optional[float] = None       # For WACC cross-check
    segments: Optional[List[Dict[str, Any]]] = None  # SOTP segments


@dataclass
class MethodScore:
    method: str
    score: int
    reasons: List[str]


@dataclass
class MethodRanking:
    primary_method: str
    secondary_method: str
    deprioritized_method: str
    ranking: List[MethodScore]
    stage_label: str


@dataclass
class DCFResult:
    enterprise_value: float
    pv_cash_flows: float
    pv_terminal_value: float
    implied_equity_value: float
    implied_share_price: Optional[float]
    tv_pct_of_ev: Optional[float] = None
    tv_confidence: str = "ok"  # ok / warning / low_confidence
    exit_multiple_ev: Optional[dict] = None      # {"bear_6x": X, "base_8x": Y, "bull_10x": Z}
    exit_multiple_price: Optional[float] = None   # base case per-share from exit multiple


@dataclass
class DCFSensitivityResult:
    growth_rates: List[float]
    discount_rates: List[float]
    enterprise_value_matrix: List[List[float]]
    note: str


@dataclass
class RunwayResult:
    annual_burn: float
    runway_years: Optional[float]
    runway_months: Optional[float]
    status: str


@dataclass
class RelativeValuationRange:
    method: str
    bear: float
    base: float
    bull: float


@dataclass
class WACCCrossCheck:
    independent_coe: float      # Rf + beta * ERP
    beta: Optional[float]
    sell_side_wacc: float
    divergence_bps: float
    warning: bool
    note: str


@dataclass
class SOTPResult:
    segments: List[Dict[str, Any]]
    total_segment_value: float
    net_cash_adjustment: float
    minority_adjustment: float
    sotp_equity_value: float
    sotp_per_share: Optional[float]
    vs_market_cap_pct: Optional[float]
    margin_sanity_notes: List[str]


@dataclass
class SOTPGuide:
    recommended: bool
    note: str


@dataclass
class PMInstruction:
    primary_method: str
    secondary_method: str
    rejected_method: str
    rationale: str


@dataclass
class ValuationOutput:
    inputs: Dict[str, Any]
    data_recon_packet: Dict[str, Any]
    derived_metrics: Dict[str, Any]
    method_ranking: Dict[str, Any]
    valuation_results: Dict[str, Any]
    pm_instruction: Dict[str, Any]


class ValuationRouter:
    def __init__(self, inputs: ValuationInputs):
        self.inputs = inputs
        self.compute_cost_ratio = (
            inputs.compute_cost_ratio
            if inputs.compute_cost_ratio is not None
            else max(0.0, min(1.0, 1 - inputs.gross_margin))
        )

    def classify_stage(self) -> str:
        high_growth = self.inputs.growth_rate >= 0.25
        mature_growth = self.inputs.growth_rate < 0.10
        positive_fcf = self.inputs.fcf > 0
        ccr_heavy = (self.inputs.compute_cost_ratio is not None
                     and self.compute_cost_ratio >= 0.55)
        low_gm = self.inputs.gross_margin < 0.35
        multi_segment = self.inputs.segment_count >= 2

        labels = []
        labels.append("Positive FCF" if positive_fcf else "Negative FCF")
        labels.append("High Growth" if high_growth else "Mature / Low Growth" if mature_growth else "Mid Growth")
        if ccr_heavy:
            labels.append("Compute-heavy")
        elif low_gm:
            labels.append("Margin-pressured / High-COGS")
        else:
            labels.append("Asset-light / Higher Gross Margin")
        if multi_segment:
            labels.append("Multi-segment")
        return " / ".join(labels)

    def _is_balance_sheet_sector(self) -> bool:
        s = (self.inputs.sector or "").lower()
        return any(kw in s for kw in ("real estate", "financials", "financial services",
                                       "banks", "insurance", "reits"))

    def rank_methods(self) -> MethodRanking:
        scorebook = {
            "DCF": MethodScore("DCF", 0, []),
            "P/S": MethodScore("P/S", 0, []),
            "EV/GP": MethodScore("EV/GP", 0, []),
            "SOTP": MethodScore("SOTP", 0, []),
        }

        i = self.inputs
        ccr = self.compute_cost_ratio

        # ── Sector override: balance-sheet-driven industries ──
        if self._is_balance_sheet_sector():
            scorebook["P/B"] = MethodScore("P/B", 8, [
                "资产密集型/金融行业：P/B (NAV折让) 为标准主估值锚。"
            ])
            scorebook["P/E"] = MethodScore("P/E", 4, [
                "P/E 作为盈利能力交叉验证。"
            ])
            scorebook["DCF"].score -= 6
            scorebook["DCF"].reasons.append(
                "资产密集型行业OCF波动大、FCF常为负，传统DCF不可靠。"
            )
            scorebook["P/S"].score -= 4
            scorebook["P/S"].reasons.append(
                "收入受结算节奏/利息周期驱动，P/S不适用于此类行业。"
            )
            scorebook["EV/GP"].score -= 4
            scorebook["EV/GP"].reasons.append(
                "毛利结构与科技/平台公司不同，EV/GP不适用于此类行业。"
            )
            ranking = sorted(scorebook.values(), key=lambda x: x.score, reverse=True)
            return MethodRanking(
                primary_method=ranking[0].method,
                secondary_method=ranking[1].method,
                deprioritized_method=ranking[-1].method,
                ranking=ranking,
                stage_label=self.classify_stage(),
            )

        if i.fcf > 0:
            scorebook["DCF"].score += 4
            scorebook["DCF"].reasons.append("FCF 为正，适合使用现金流折现作为主锚。")
        else:
            scorebook["DCF"].score -= 4
            scorebook["DCF"].reasons.append("FCF 为负，传统 DCF 可靠性显著下降。")
            scorebook["P/S"].score += 2
            scorebook["EV/GP"].score += 2

        if i.growth_rate >= 0.30:
            scorebook["P/S"].score += 3
            scorebook["P/S"].reasons.append("高增长阶段，市场更常用收入倍数作为过渡估值框架。")
        elif i.growth_rate >= 0.15:
            scorebook["P/S"].score += 1
            scorebook["DCF"].score += 1
        else:
            scorebook["DCF"].score += 2
            scorebook["DCF"].reasons.append("低增速更适合回归现金流与盈利能力估值。")
            scorebook["P/S"].score -= 1

        if ccr >= 0.55:
            scorebook["EV/GP"].score += 4
            scorebook["EV/GP"].reasons.append("算力/COGS 负担重 (CCR≥0.55)，EV/GP 比 P/S 更能惩罚低质量收入。")
            scorebook["P/S"].score -= 2
            scorebook["P/S"].reasons.append("收入质量偏弱，单看 P/S 容易高估。")
        elif i.gross_margin < 0.35:
            scorebook["EV/GP"].score += 4
            scorebook["EV/GP"].reasons.append("毛利承压（行业特征，GM<35%），EV/GP 更适合捕捉真实盈利能力。")
            scorebook["P/S"].score -= 2
            scorebook["P/S"].reasons.append("低毛利环境下 P/S 容易高估内在价值。")
        elif i.gross_margin >= 0.65:
            scorebook["P/S"].score += 2
            scorebook["P/S"].reasons.append("高毛利、轻资产商业模式更适配 P/S。")

        if i.segment_count >= 2:
            scorebook["SOTP"].score += 4
            scorebook["SOTP"].reasons.append("多业务拼盘，适合用分部估值避免一锅炖。")

        if i.rule_of_40 is not None:
            if i.rule_of_40 >= 40:
                scorebook["P/S"].score += 1
                scorebook["P/S"].reasons.append("Rule of 40 较优，对高质量成长股的收入倍数更有支撑。")
            else:
                scorebook["P/S"].score -= 1
                scorebook["EV/GP"].score += 1

        ranking = sorted(scorebook.values(), key=lambda x: x.score, reverse=True)
        return MethodRanking(
            primary_method=ranking[0].method,
            secondary_method=ranking[1].method,
            deprioritized_method=ranking[-1].method,
            ranking=ranking,
            stage_label=self.classify_stage(),
        )


class AnthropicStyleDCFEngine:
    """Single-file DCF engine, embedded so dcf_modeler.py becomes optional."""

    def __init__(self, inputs: ValuationInputs):
        self.inputs = inputs

    def run(self) -> Optional[DCFResult]:
        i = self.inputs
        if i.fcf <= 0 and not i.explicit_fcf:
            return None
        if i.discount_rate <= i.terminal_growth_rate:
            raise ValueError("discount_rate 必须大于 terminal_growth_rate，否则终值无意义。")

        # Explicit multi-year FCF mode or single-FCF projection
        if i.explicit_fcf:
            projected_fcfs = list(i.explicit_fcf)
            n_years = len(projected_fcfs)
            print(f"[DCF] Using explicit FCF for {n_years} years: {projected_fcfs}", file=sys.stderr)
        else:
            projected_fcfs = []
            current_fcf = i.fcf
            n_years = i.projection_years
            for _ in range(n_years):
                current_fcf *= (1 + i.fcf_growth_rate)
                projected_fcfs.append(current_fcf)

        pv_cash_flows = sum(
            fcf / ((1 + i.discount_rate) ** year)
            for year, fcf in enumerate(projected_fcfs, start=1)
        )
        terminal_fcf = projected_fcfs[-1] * (1 + i.terminal_growth_rate)
        terminal_value = terminal_fcf / (i.discount_rate - i.terminal_growth_rate)
        pv_terminal_value = terminal_value / ((1 + i.discount_rate) ** n_years)
        enterprise_value = pv_cash_flows + pv_terminal_value

        # Terminal value % of EV
        tv_pct = (pv_terminal_value / enterprise_value * 100) if enterprise_value != 0 else None
        if tv_pct is not None and tv_pct > 60:
            tv_conf = "low_confidence"
            print(f"[DCF WARNING] Terminal value = {tv_pct:.1f}% of EV — exceeds 60%, DCF flagged low_confidence", file=sys.stderr)
        elif tv_pct is not None and tv_pct > 50:
            tv_conf = "warning"
            print(f"[DCF WARNING] Terminal value = {tv_pct:.1f}% of EV — long-duration assumptions dominate", file=sys.stderr)
        else:
            tv_conf = "ok"

        equity_value = enterprise_value - i.net_debt
        if i.shares_outstanding > 0 and i.share_scale > 0:
            share_price = (equity_value * i.share_scale) / i.shares_outstanding
        else:
            share_price = None
            print(
                "[WARNING] DCF per-share price suppressed: "
                "UNIT_INFERENCE_FAILED or invalid shares_outstanding. "
                "EV and equity value are still valid.",
                file=sys.stderr,
            )

        # Exit multiple alternative when TV dominates (>75%)
        exit_multiple_ev = None
        exit_multiple_price = None
        if tv_pct is not None and tv_pct > 75:
            # Use EV/EBITDA exit multiple instead of perpetuity growth
            # Default exit multiples: bear=6x, base=8x, bull=10x (mature company)
            last_fcf = projected_fcfs[-1]
            exit_mults = [6.0, 8.0, 10.0]
            exit_evs = []
            for m in exit_mults:
                tv_exit = last_fcf * m
                pv_tv_exit = tv_exit / ((1 + i.discount_rate) ** n_years)
                ev_exit = pv_cash_flows + pv_tv_exit
                exit_evs.append(round(ev_exit, 2))
            exit_multiple_ev = {"bear_6x": exit_evs[0], "base_8x": exit_evs[1], "bull_10x": exit_evs[2]}

            # Compute equity / share for base case
            base_equity = exit_evs[1] - i.net_debt
            if i.shares_outstanding > 0 and i.share_scale > 0:
                exit_multiple_price = round((base_equity * i.share_scale) / i.shares_outstanding, 2)

            print(f"[DCF EXIT MULTIPLE] TV>{tv_pct:.0f}% → exit multiple EV: "
                  f"bear={exit_evs[0]:.0f}, base={exit_evs[1]:.0f}, bull={exit_evs[2]:.0f}",
                  file=sys.stderr)

        return DCFResult(
            enterprise_value=enterprise_value,
            pv_cash_flows=pv_cash_flows,
            pv_terminal_value=pv_terminal_value,
            implied_equity_value=equity_value,
            implied_share_price=share_price,
            tv_pct_of_ev=round(tv_pct, 1) if tv_pct is not None else None,
            tv_confidence=tv_conf,
            exit_multiple_ev=exit_multiple_ev,
            exit_multiple_price=exit_multiple_price,
        )

    def sensitivity_matrix(self) -> Optional[DCFSensitivityResult]:
        i = self.inputs
        if i.fcf <= 0:
            return None

        growth_rates = [i.fcf_growth_rate - 0.05, i.fcf_growth_rate, i.fcf_growth_rate + 0.05]
        discount_rates = [i.discount_rate - 0.02, i.discount_rate, i.discount_rate + 0.02]
        matrix: List[List[float]] = []

        for w in discount_rates:
            row: List[float] = []
            for g in growth_rates:
                if w <= i.terminal_growth_rate:
                    row.append(float("nan"))
                    continue
                current_fcf = i.fcf
                projected = []
                for _ in range(i.projection_years):
                    current_fcf *= (1 + g)
                    projected.append(current_fcf)
                pv_cf = sum(cf / ((1 + w) ** yr) for yr, cf in enumerate(projected, start=1))
                tv = projected[-1] * (1 + i.terminal_growth_rate) / (w - i.terminal_growth_rate)
                pv_tv = tv / ((1 + w) ** i.projection_years)
                row.append(round(pv_cf + pv_tv, 2))
            matrix.append(row)

        return DCFSensitivityResult(
            growth_rates=growth_rates,
            discount_rates=discount_rates,
            enterprise_value_matrix=matrix,
            note="关注低增速 + 高折现率象限，作为安全边际底线。",
        )


# --- Unit inference constants (scope: _infer_share_scale only) ---
# Used exclusively to detect value_unit_scale (revenue/FCF denominator unit).
# NOT a general-purpose valuation P/S rule — do not cite in investment analysis.
# hi < lo × 1000 mathematically guarantees adjacent candidates (differing by 1000×)
# cannot simultaneously match, making the multiple-matched branch defensive-only.
# Exposed as module-level names so tests can monkeypatch to exercise that branch.
_PS_PLAUSIBILITY_LO: float = 0.3
_PS_PLAUSIBILITY_HI: float = 60.0


class ValuationEngine:
    def __init__(self, inputs: ValuationInputs, data_packet: DataReconPacket):
        self.inputs = inputs
        self.data_packet = data_packet
        self.router = ValuationRouter(inputs)
        self.dcf_engine = AnthropicStyleDCFEngine(inputs)
        self.next_year_revenue = inputs.revenue * (1 + inputs.growth_rate)
        self.next_year_gross_profit = self.next_year_revenue * inputs.gross_margin
        self._inject_live_market_fields()
        self._calibrate_multiple_bands_from_peers()

    def _inject_live_market_fields(self) -> None:
        base = self.data_packet.base_data_agent
        if self.inputs.current_price <= 0 and base.current_price is not None:
            self.inputs.current_price = base.current_price
        if self.inputs.shares_outstanding <= 0 and base.shares_outstanding is not None:
            self.inputs.shares_outstanding = base.shares_outstanding
        self._infer_share_scale()

    def _infer_share_scale(self) -> None:
        """
        Auto-detect the unit mismatch between (revenue/fcf) and shares_outstanding.

        shares_unit_scale — from: implied_abs_shares / shares_input
          ratio < 10    → shares entered as absolute counts
          ratio ≈ 1e6   → shares entered in millions
          ratio ≈ 1e9   → shares entered in billions
          else          → ambiguous → UNIT_INFERENCE_FAILED

        value_unit_scale — from: live_mc / (revenue × candidate) landing in [0.3, 60]
          Candidates: 1e9 (billions), 1e6 (millions), 1e3 (thousands), 1.0 (absolute)
          Range [0.3, 60] is mathematically guaranteed unique: hi(60) < lo(0.3) × 1000,
          so adjacent candidates (differing by 1000×) cannot both match.
          0 or 2+ matches → UNIT_INFERENCE_FAILED

        share_scale = value_unit_scale / shares_unit_scale
        Sentinel: share_scale = 0.0 → DCF per-share price suppressed downstream.
        """
        base = self.data_packet.base_data_agent
        live_mc = base.market_cap
        live_price = base.current_price if (base.current_price and base.current_price > 0) \
            else (self.inputs.current_price if self.inputs.current_price > 0 else None)

        # --- HKD → RMB FX adjustment for .HK tickers with RMB-denominated inputs ---
        if (live_mc and self.inputs.ticker and self.inputs.ticker.endswith(".HK")
                and base.currency == "HKD"):
            fx_rate = None
            if YF_AVAILABLE:
                try:
                    fx_rate = yf.Ticker("HKDCNY=X").fast_info.get("lastPrice")
                except Exception:
                    pass
            if fx_rate is None:
                fx_rate = 0.927
                print(f"[FX] HKDCNY=X fetch failed, using fallback rate {fx_rate}", file=sys.stderr)
            else:
                print(f"[FX] HKDCNY=X live rate = {fx_rate:.4f}", file=sys.stderr)
            live_mc = live_mc * fx_rate
            if live_price:
                live_price = live_price * fx_rate
            print(f"[FX] Converted live_mc to RMB: {live_mc:,.0f}", file=sys.stderr)

        shares = self.inputs.shares_outstanding
        revenue = self.inputs.revenue

        missing = [k for k, v in [
            ("live_mc", live_mc), ("live_price", live_price),
            ("shares_outstanding", shares if shares > 0 else None),
            ("revenue", revenue if revenue > 0 else None),
        ] if not v]
        if missing:
            print(
                f"[UNIT INFERENCE SKIPPED] insufficient live inputs: {missing}. "
                f"share_scale remains 1.0 — DCF per-share price may be incorrect.",
                file=sys.stderr,
            )
            return

        implied_abs_shares = live_mc / live_price
        unit_ratio = implied_abs_shares / shares

        # --- shares_unit_scale ---
        if unit_ratio < 10:
            shares_unit_scale = 1.0
            shares_unit_label = "absolute"
        elif 5e4 < unit_ratio < 5e7:
            shares_unit_scale = 1e6
            shares_unit_label = "millions"
        elif 5e7 < unit_ratio < 5e10:
            shares_unit_scale = 1e9
            shares_unit_label = "billions"
        else:
            shares_unit_scale = None
            shares_unit_label = f"ambiguous (ratio={unit_ratio:.2e})"

        # --- value_unit_scale: [0.3, 60] guarantees no adjacent-candidate overlap ---
        _candidates = [(1e9, "billions"), (1e6, "millions"), (1e3, "thousands"), (1.0, "absolute")]
        matched = []
        for candidate_unit, label in _candidates:
            implied_ps = live_mc / (revenue * candidate_unit)
            if _PS_PLAUSIBILITY_LO < implied_ps < _PS_PLAUSIBILITY_HI:
                matched.append((candidate_unit, label, implied_ps))

        if len(matched) == 1:
            value_unit_scale, value_unit_label, matched_ps = matched[0]
        else:
            value_unit_scale = None
            value_unit_label = ("none matched" if len(matched) == 0
                                else f"multiple matched: {[m[1] for m in matched]}")
            matched_ps = None

        # --- Resolve or hard-fail ---
        if shares_unit_scale is not None and value_unit_scale is not None:
            computed_scale = value_unit_scale / shares_unit_scale
            self.inputs.share_scale = computed_scale
            print(
                f"[UNIT INFERENCE]\n"
                f"  shares_input       = {shares}\n"
                f"  implied_abs_shares = {implied_abs_shares:,.0f}\n"
                f"  unit_ratio         = {unit_ratio:.2e}  →  shares in {shares_unit_label}\n"
                f"  shares_unit_scale  = {shares_unit_scale:.0e}\n"
                f"  value_unit_scale   = {value_unit_scale:.0e}  ({value_unit_label},"
                f" implied P/S={matched_ps:.1f}x)\n"
                f"  share_scale        = {computed_scale:.0f}  (= value_unit / shares_unit)\n"
                f"  heuristic          = revenue × {value_unit_scale:.0e} / live_mc"
                f" → P/S {matched_ps:.1f}x ∈ [0.3, 60]",
                file=sys.stderr,
            )
        else:
            self.inputs.share_scale = 0.0
            print(
                f"[UNIT INFERENCE FAILED]\n"
                f"  shares_input       = {shares}\n"
                f"  implied_abs_shares = {implied_abs_shares:,.0f}\n"
                f"  unit_ratio         = {unit_ratio:.2e}  →  shares unit: {shares_unit_label}\n"
                f"  value_unit_scale   = {value_unit_label}\n"
                f"  share_scale        = 0.0  (SUPPRESSED)\n"
                f"  → DCF per-share price will NOT be computed. Resolve unit ambiguity manually.",
                file=sys.stderr,
            )

    def _calibrate_multiple_bands_from_peers(self) -> None:
        if not self.inputs.use_live_peers:
            return
        peers = self.data_packet.peer_snapshots
        ps_values = [p.price_to_sales for p in peers if p.price_to_sales is not None and p.price_to_sales > 0]
        evgp_values = [p.ev_to_gross_profit for p in peers if p.ev_to_gross_profit is not None and p.ev_to_gross_profit > 0]

        if ps_values:
            m = median(ps_values)
            self.inputs.ps_bear = round(max(1.0, m * 0.7), 2)
            self.inputs.ps_base = round(m, 2)
            self.inputs.ps_bull = round(m * 1.3, 2)
        if evgp_values:
            m = median(evgp_values)
            self.inputs.evgp_bear = round(max(1.0, m * 0.7), 2)
            self.inputs.evgp_base = round(m, 2)
            self.inputs.evgp_bull = round(m * 1.3, 2)

    def cash_runway(self) -> Optional[RunwayResult]:
        i = self.inputs
        if i.fcf >= 0:
            return None
        annual_burn = abs(i.fcf)
        if i.cash_balance <= 0:
            return RunwayResult(
                annual_burn=annual_burn,
                runway_years=None,
                runway_months=None,
                status="No cash balance provided; cannot estimate runway.",
            )
        years = i.cash_balance / annual_burn
        months = years * 12
        if years < 1:
            status = "High risk: runway below 12 months."
        elif years < 2:
            status = "Watchlist: runway between 12 and 24 months."
        else:
            status = "Runway appears manageable, but dilution risk remains."
        return RunwayResult(annual_burn=annual_burn, runway_years=years, runway_months=months, status=status)

    def pb_range(self) -> Optional[RelativeValuationRange]:
        i = self.inputs
        if i.bps <= 0:
            return None
        return RelativeValuationRange(
            method="P/B",
            bear=i.bps * i.pb_bear,
            base=i.bps * i.pb_base,
            bull=i.bps * i.pb_bull,
        )

    def pe_range(self) -> Optional[RelativeValuationRange]:
        i = self.inputs
        if i.eps <= 0:
            return None
        return RelativeValuationRange(
            method="P/E",
            bear=i.eps * i.pe_bear,
            base=i.eps * i.pe_base,
            bull=i.eps * i.pe_bull,
        )

    def ps_range(self) -> RelativeValuationRange:
        i = self.inputs
        return RelativeValuationRange(
            method="P/S",
            bear=self.next_year_revenue * i.ps_bear,
            base=self.next_year_revenue * i.ps_base,
            bull=self.next_year_revenue * i.ps_bull,
        )

    def evgp_range(self) -> RelativeValuationRange:
        i = self.inputs
        return RelativeValuationRange(
            method="EV/GP",
            bear=self.next_year_gross_profit * i.evgp_bear,
            base=self.next_year_gross_profit * i.evgp_base,
            bull=self.next_year_gross_profit * i.evgp_bull,
        )

    def wacc_cross_check(self) -> Optional[WACCCrossCheck]:
        """Cross-check sell-side WACC against independent estimate using yfinance beta."""
        i = self.inputs
        if i.sell_side_wacc is None:
            return None
        base = self.data_packet.base_data_agent
        beta = None
        if YF_AVAILABLE and i.ticker:
            try:
                stock = yf.Ticker(i.ticker)
                beta = _safe_float((stock.info or {}).get("beta"))
            except Exception:
                pass
        if beta is None:
            return WACCCrossCheck(
                independent_coe=0.0, beta=None,
                sell_side_wacc=i.sell_side_wacc,
                divergence_bps=0.0, warning=False,
                note="Beta unavailable — cannot compute independent WACC estimate."
            )
        rf = 0.035   # risk-free rate assumption
        erp = 0.055  # equity risk premium assumption
        independent_coe = rf + beta * erp
        divergence = abs(independent_coe - i.sell_side_wacc) * 10000  # bps
        warning = divergence > 200
        note = (
            f"Independent CoE = Rf({rf*100:.1f}%) + β({beta:.2f}) × ERP({erp*100:.1f}%) = {independent_coe*100:.1f}%. "
            f"Sell-side WACC = {i.sell_side_wacc*100:.1f}%. "
            f"Divergence = {divergence:.0f}bps."
        )
        if warning:
            note += " WACC_DIVERGENCE: >200bps gap — sell-side discount rate may be reverse-engineered."
        return WACCCrossCheck(
            independent_coe=round(independent_coe, 4),
            beta=round(beta, 3),
            sell_side_wacc=i.sell_side_wacc,
            divergence_bps=round(divergence, 0),
            warning=warning,
            note=note,
        )

    def sotp_valuation(self) -> Optional[SOTPResult]:
        """Compute sum-of-the-parts valuation from segment data."""
        i = self.inputs
        if not i.segments:
            return None
        seg_results = []
        margin_notes = []
        total_value = 0.0
        for seg in i.segments:
            name = seg.get("name", "Unknown")
            rev = seg.get("rev", 0)
            gm = seg.get("gm", 0)
            method = seg.get("method", "PS").upper()
            multiple = seg.get("multiple", 1.0)
            gp = rev * gm
            # Margin sanity check (§2.2 SOTP segment cross-check)
            stated_profit = seg.get("profit")
            if stated_profit is not None and gp != 0:
                diff_pct = abs(gp - stated_profit) / abs(gp) * 100
                if diff_pct > 5:
                    margin_notes.append(
                        f"{name}: stated_margin({gm:.1%}) × revenue({rev:.0f}) = {gp:.0f}, "
                        f"but stated_profit = {stated_profit:.0f} — {diff_pct:.1f}% gap"
                    )
            if method == "PS":
                seg_value = rev * multiple
            elif method in ("EVGP", "EV/GP"):
                seg_value = gp * multiple
            else:
                seg_value = rev * multiple  # fallback to PS
            total_value += seg_value
            seg_results.append({
                "name": name,
                "revenue": rev,
                "gross_margin": gm,
                "gross_profit": round(gp, 2),
                "method": method,
                "multiple": multiple,
                "segment_value": round(seg_value, 2),
            })
        net_cash = i.cash_balance - i.net_debt if i.net_debt > 0 else i.cash_balance
        minority = seg.get("minority", 0) if i.segments else 0  # from last seg or 0
        # Allow explicit minority from any segment's data
        for s in i.segments:
            if "minority" in s:
                minority = s["minority"]
                break
        equity_value = total_value + net_cash - minority
        per_share = None
        if i.shares_outstanding > 0:
            per_share = round(equity_value / i.shares_outstanding, 2)
            if i.share_scale > 0 and i.share_scale != 1.0:
                per_share = round(equity_value * i.share_scale / i.shares_outstanding, 2)
        base = self.data_packet.base_data_agent
        vs_mc = None
        if base.market_cap and base.market_cap > 0 and equity_value > 0:
            vs_mc = round((equity_value / base.market_cap - 1) * 100, 1)
        return SOTPResult(
            segments=seg_results,
            total_segment_value=round(total_value, 2),
            net_cash_adjustment=round(net_cash, 2),
            minority_adjustment=round(minority, 2),
            sotp_equity_value=round(equity_value, 2),
            sotp_per_share=per_share,
            vs_market_cap_pct=vs_mc,
            margin_sanity_notes=margin_notes,
        )

    def sotp_guide(self) -> SOTPGuide:
        if self.inputs.segment_count >= 2:
            return SOTPGuide(
                recommended=True,
                note="检测到多业务结构，建议将收入拆为平台/SaaS、流量/广告、服务/低质收入分别赋予不同倍数。",
            )
        return SOTPGuide(recommended=False, note="当前未见明显多分部结构，SOTP 暂作为补充而非主锚。")

    def pm_instruction(self, ranking: MethodRanking) -> PMInstruction:
        rationales = {
            "DCF": "公司已进入造血期，主估值锚点应回归现金流现值，而非仅凭收入倍数讲故事。",
            "P/S": "公司仍处高速成长阶段，且收入质量相对较高，收入倍数仍是更贴近市场定价语言的主尺。",
            "EV/GP": "考虑到算力/COGS 对收入质量的侵蚀，必须用 EV/GP 重新定价，不能放任 P/S 放大泡沫。",
            "SOTP": "业务结构复杂，必须分部定价，否则高质量业务会被低质量业务拖累，或反之。",
            "P/B": "资产密集型/金融行业以账面价值为估值锚点，P/B(NAV折让)是标准方法，辅以P/E交叉验证。",
            "P/E": "盈利能力为核心估值锚点，P/E反映市场对可持续盈利的定价。",
        }
        return PMInstruction(
            primary_method=ranking.primary_method,
            secondary_method=ranking.secondary_method,
            rejected_method=ranking.deprioritized_method,
            rationale=rationales[ranking.primary_method],
        )

    def _detect_cyclical_warning(self) -> Optional[str]:
        industry = (self.data_packet.base_data_agent.industry or "").lower()
        _CYCLICAL_KEYWORDS = ("coal", "oil", "gas", "mining", "steel", "metals", "chemicals")
        if any(kw in industry for kw in _CYCLICAL_KEYWORDS):
            return (
                "CYCLICAL_WARNING: DCF is based on current-year FCF which may represent "
                "peak or trough conditions. Consider normalized mid-cycle FCF or EV/EBITDA."
            )
        return None

    def build_output(self) -> ValuationOutput:
        ranking = self.router.rank_methods()
        dcf_result = self.dcf_engine.run()
        dcf_sensitivity = self.dcf_engine.sensitivity_matrix()
        runway_result = self.cash_runway()
        ps_result = self.ps_range()
        evgp_result = self.evgp_range()
        pb_result = self.pb_range()
        pe_result = self.pe_range()
        sotp_guide_result = self.sotp_guide()
        sotp_val_result = self.sotp_valuation()
        wacc_check = self.wacc_cross_check()
        pm = self.pm_instruction(ranking)
        cyclical_warning = self._detect_cyclical_warning()

        return ValuationOutput(
            inputs=asdict(self.inputs),
            data_recon_packet=asdict(self.data_packet),
            derived_metrics={
                "next_year_revenue": round(self.next_year_revenue, 4),
                "next_year_gross_profit": round(self.next_year_gross_profit, 4),
                "revenue_quality_proxy": round(self.inputs.gross_margin, 4),
                "compute_cost_proxy": round(self.router.compute_cost_ratio, 4),
                "cyclical_warning": cyclical_warning,
            },
            method_ranking={
                "stage_label": ranking.stage_label,
                "primary_method": ranking.primary_method,
                "secondary_method": ranking.secondary_method,
                "deprioritized_method": ranking.deprioritized_method,
                "ranking": [asdict(item) for item in ranking.ranking],
            },
            valuation_results={
                "dcf": asdict(dcf_result) if dcf_result else None,
                "dcf_sensitivity": asdict(dcf_sensitivity) if dcf_sensitivity else None,
                "cash_runway": asdict(runway_result) if runway_result else None,
                "ps": asdict(ps_result),
                "ev_gp": asdict(evgp_result),
                "pb": asdict(pb_result) if pb_result else None,
                "pe": asdict(pe_result) if pe_result else None,
                "sotp": asdict(sotp_guide_result),
                "sotp_valuation": asdict(sotp_val_result) if sotp_val_result else None,
                "wacc_cross_check": asdict(wacc_check) if wacc_check else None,
            },
            pm_instruction=asdict(pm),
        )


# =========================
# CLI / presentation layer
# =========================

def print_human_readable(output: ValuationOutput) -> None:
    data = output.data_recon_packet
    derived = output.derived_metrics
    ranking = output.method_ranking
    results = output.valuation_results
    pm = output.pm_instruction

    print("========== 📊 Valuation Matrix Layered Complete ==========")
    print("【数据侦察层】")
    base = data["base_data_agent"]
    adv = data["advanced_quant_agent"]
    print(f"Base Data Agent: {base['status']} | ticker={base['ticker']} | price={_fmt_num(base['current_price'])} | market_cap={_fmt_num(base['market_cap'])}")
    print(f"Advanced Quant Agent: {adv['status']} | ROIC={_fmt_num(adv['roic'])} | Altman Z={_fmt_num(adv['altman_z_score'])}")
    if data["peer_snapshots"]:
        print(f"Peer snapshots loaded: {len(data['peer_snapshots'])}")
    print()

    print(f"阶段识别: {ranking['stage_label']}")
    if derived.get("cyclical_warning"):
        print(f"⚠️  {derived['cyclical_warning']}")
    print(f"下一年营收: {derived['next_year_revenue']:.2f}")
    print(f"下一年毛利: {derived['next_year_gross_profit']:.2f}")
    print()

    print("【估值方法路由】")
    print(f"主方法: {ranking['primary_method']}")
    print(f"次方法: {ranking['secondary_method']}")
    print(f"降权/弃用: {ranking['deprioritized_method']}")
    for item in ranking["ranking"]:
        print(f"- {item['method']}: score={item['score']}")
        for reason in item["reasons"]:
            print(f"    · {reason}")
    print()

    print("【估值区间】")
    pb = results.get("pb")
    pe = results.get("pe")
    if pb:
        print(f"P/B: bear={pb['bear']:.2f}, base={pb['base']:.2f}, bull={pb['bull']:.2f}")
    if pe:
        print(f"P/E: bear={pe['bear']:.2f}, base={pe['base']:.2f}, bull={pe['bull']:.2f}")
    ps = results["ps"]
    evgp = results["ev_gp"]
    print(f"P/S: bear={ps['bear']:.2f}, base={ps['base']:.2f}, bull={ps['bull']:.2f}")
    print(f"EV/GP: bear={evgp['bear']:.2f}, base={evgp['base']:.2f}, bull={evgp['bull']:.2f}")

    dcf = results["dcf"]
    if dcf:
        share_scale = output.inputs.get("share_scale", 1.0)
        if dcf["implied_share_price"] is not None:
            scale_note = f"  [unit-adjusted via share_scale={share_scale:.0f}x]" if share_scale != 1.0 else ""
            print(f"DCF EV={dcf['enterprise_value']:.2f}, Equity={dcf['implied_equity_value']:.2f}, Price=${dcf['implied_share_price']:.2f}{scale_note}")
        else:
            fail_note = "  [UNIT_INFERENCE_FAILED — see stderr]" if share_scale <= 0 else ""
            print(f"DCF EV={dcf['enterprise_value']:.2f}, Equity={dcf['implied_equity_value']:.2f}, Price=suppressed{fail_note}")
        if dcf.get("tv_pct_of_ev") is not None:
            tv_icon = "[!]" if dcf["tv_confidence"] != "ok" else "[ ]"
            print(f"  TV% of EV = {dcf['tv_pct_of_ev']:.1f}%  {tv_icon} {dcf['tv_confidence'].upper()}")
        if dcf.get("exit_multiple_ev"):
            em = dcf["exit_multiple_ev"]
            emp = dcf.get("exit_multiple_price")
            print(f"  EXIT MULTIPLE (TV too high → FCF×exit mult):")
            print(f"    Bear(6x)={em['bear_6x']:.0f}  Base(8x)={em['base_8x']:.0f}  Bull(10x)={em['bull_10x']:.0f}")
            if emp:
                print(f"    Base exit-multiple price: ${emp:.2f}")

    sens = results["dcf_sensitivity"]
    if sens:
        print("DCF Sensitivity EV Matrix:")
        for dr, row in zip(sens["discount_rates"], sens["enterprise_value_matrix"]):
            print(f"  WACC {dr*100:.1f}% -> {row}")
        print(f"  Note: {sens['note']}")

    runway = results["cash_runway"]
    if runway:
        years_text = f"{runway['runway_years']:.2f} 年" if runway['runway_years'] is not None else "N/A"
        months_text = f"{runway['runway_months']:.1f} 月" if runway['runway_months'] is not None else "N/A"
        print(f"Cash Runway: burn={runway['annual_burn']:.2f}, runway={years_text} / {months_text}")
        print(f"Runway Status: {runway['status']}")

    sotp = results["sotp"]
    print(f"SOTP: {'建议启用' if sotp['recommended'] else '暂不主用'} | {sotp['note']}")

    # SOTP Valuation
    sotp_val = results.get("sotp_valuation")
    if sotp_val:
        print(f"\n【SOTP 分部估值】")
        for seg in sotp_val["segments"]:
            print(f"  {seg['name']}: Rev={seg['revenue']:.0f}, GM={seg['gross_margin']:.1%}, "
                  f"GP={seg['gross_profit']:.0f}, {seg['method']}×{seg['multiple']:.1f} = {seg['segment_value']:.0f}")
        print(f"  Segment total = {sotp_val['total_segment_value']:.0f}")
        print(f"  + Net cash = {sotp_val['net_cash_adjustment']:.0f}")
        if sotp_val["minority_adjustment"] != 0:
            print(f"  - Minority = {sotp_val['minority_adjustment']:.0f}")
        print(f"  SOTP equity value = {sotp_val['sotp_equity_value']:.0f}")
        if sotp_val["sotp_per_share"] is not None:
            print(f"  SOTP per share = {sotp_val['sotp_per_share']:.2f}")
        if sotp_val["vs_market_cap_pct"] is not None:
            sign = "+" if sotp_val["vs_market_cap_pct"] > 0 else ""
            print(f"  vs Market Cap: {sign}{sotp_val['vs_market_cap_pct']:.1f}%")
        if sotp_val["margin_sanity_notes"]:
            print(f"  [!] Margin sanity issues:")
            for note in sotp_val["margin_sanity_notes"]:
                print(f"    - {note}")

    # WACC Cross-Check
    wacc_ck = results.get("wacc_cross_check")
    if wacc_ck:
        print(f"\n【WACC Cross-Check】")
        icon = "[!]" if wacc_ck["warning"] else "[ ]"
        print(f"  {icon} {wacc_ck['note']}")

    print()
    print("【PM 裁决指令】")
    print(f"主锚: {pm['primary_method']} | 辅锚: {pm['secondary_method']} | 弃用: {pm['rejected_method']}")
    print(pm['rationale'])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Layered valuation engine with data agents and embedded DCF.")
    parser.add_argument("revenue", type=float, help="当前营收")
    parser.add_argument("gross_margin", type=float, help="毛利率，小数")
    parser.add_argument("fcf", type=float, help="自由现金流")
    parser.add_argument("growth_rate", type=float, help="预期增速，小数")
    parser.add_argument("--ticker", type=str, default=None)
    parser.add_argument("--cash-balance", type=float, default=0.0)
    parser.add_argument("--net-debt", type=float, default=0.0)
    parser.add_argument("--shares-outstanding", type=float, default=0.0)
    parser.add_argument("--current-price", type=float, default=0.0)
    parser.add_argument("--segment-count", type=int, default=1)
    parser.add_argument("--compute-cost-ratio", type=float, default=None)
    parser.add_argument("--rule-of-40", type=float, default=None)
    parser.add_argument("--fcf-growth-rate", type=float, default=0.15)
    parser.add_argument("--discount-rate", type=float, default=0.12)
    parser.add_argument("--terminal-growth-rate", type=float, default=0.03)
    parser.add_argument("--projection-years", type=int, default=5)
    parser.add_argument("--ps-bear", type=float, default=6.0)
    parser.add_argument("--ps-base", type=float, default=10.0)
    parser.add_argument("--ps-bull", type=float, default=16.0)
    parser.add_argument("--evgp-bear", type=float, default=12.0)
    parser.add_argument("--evgp-base", type=float, default=18.0)
    parser.add_argument("--evgp-bull", type=float, default=24.0)
    parser.add_argument("--sector", type=str, default="",
                        help="行业（e.g. 'Real Estate', 'Financials'）— 启用P/B+P/E路由")
    parser.add_argument("--bps", type=float, default=0.0,
                        help="每股净资产（P/B估值用）")
    parser.add_argument("--eps", type=float, default=0.0,
                        help="每股收益（P/E估值用）")
    parser.add_argument("--pb-bear", type=float, default=0.3)
    parser.add_argument("--pb-base", type=float, default=0.5)
    parser.add_argument("--pb-bull", type=float, default=0.8)
    parser.add_argument("--pe-bear", type=float, default=5.0)
    parser.add_argument("--pe-base", type=float, default=8.0)
    parser.add_argument("--pe-bull", type=float, default=12.0)
    parser.add_argument("--peer-tickers", type=str, default="")
    parser.add_argument("--use-live-peers", action="store_true")
    parser.add_argument("--explicit-fcf", type=str, default=None,
                        help="Comma-separated multi-year FCF values (e.g. '100,120,140,160,180')")
    parser.add_argument("--sell-side-wacc", type=float, default=None,
                        help="Sell-side WACC for cross-check (decimal, e.g. 0.1186)")
    parser.add_argument("--segments", type=str, default=None,
                        help='JSON array of segments: [{"name":"X","rev":N,"gm":0.3,"method":"PS","multiple":2.5}, ...]')
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    peer_tickers = [x.strip().upper() for x in args.peer_tickers.split(",") if x.strip()]

    args.growth_rate = normalize_pct_input("growth_rate", args.growth_rate)
    args.gross_margin = normalize_pct_input("gross_margin", args.gross_margin)

    # Parse explicit FCF
    explicit_fcf = None
    if args.explicit_fcf:
        try:
            explicit_fcf = [float(x.strip()) for x in args.explicit_fcf.split(",")]
        except ValueError:
            print("[ERROR] --explicit-fcf must be comma-separated numbers", file=sys.stderr)
            sys.exit(1)

    # Parse segments JSON
    segments = None
    if args.segments:
        try:
            segments = json.loads(args.segments)
        except json.JSONDecodeError as e:
            print(f"[ERROR] --segments JSON parse failed: {e}", file=sys.stderr)
            sys.exit(1)

    inputs = ValuationInputs(
        revenue=args.revenue,
        gross_margin=args.gross_margin,
        fcf=args.fcf,
        growth_rate=args.growth_rate,
        ticker=args.ticker,
        cash_balance=args.cash_balance,
        net_debt=args.net_debt,
        shares_outstanding=args.shares_outstanding,
        current_price=args.current_price,
        segment_count=args.segment_count,
        compute_cost_ratio=args.compute_cost_ratio,
        rule_of_40=args.rule_of_40,
        fcf_growth_rate=args.fcf_growth_rate,
        discount_rate=args.discount_rate,
        terminal_growth_rate=args.terminal_growth_rate,
        projection_years=args.projection_years,
        ps_bear=args.ps_bear,
        ps_base=args.ps_base,
        ps_bull=args.ps_bull,
        evgp_bear=args.evgp_bear,
        evgp_base=args.evgp_base,
        evgp_bull=args.evgp_bull,
        peer_tickers=peer_tickers,
        use_live_peers=args.use_live_peers,
        sector=args.sector,
        bps=args.bps,
        eps=args.eps,
        pb_bear=args.pb_bear,
        pb_base=args.pb_base,
        pb_bull=args.pb_bull,
        pe_bear=args.pe_bear,
        pe_base=args.pe_base,
        pe_bull=args.pe_bull,
        explicit_fcf=explicit_fcf,
        sell_side_wacc=args.sell_side_wacc,
        segments=segments,
    )

    recon = DataReconLayer().collect(ticker=args.ticker, peer_tickers=peer_tickers)
    engine = ValuationEngine(inputs, recon)
    output = engine.build_output()

    if args.json:
        print(json.dumps(asdict(output), ensure_ascii=False, indent=2))
    else:
        print_human_readable(output)


# =========================
# Helpers
# =========================

def normalize_pct_input(name: str, value: float) -> float:
    import sys
    if 1.0 < value <= 100.0:
        print(f"[WARNING] {name}={value} looks like a percentage — auto-converting to {value / 100:.4f}", file=sys.stderr)
        return value / 100.0
    if value > 100.0:
        raise ValueError(f"{name}={value} is implausibly high. Pass decimals (e.g. 0.08) or percentages up to 100.")
    return value


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        f = float(value)
        if math.isnan(f):
            return None
        return f
    except Exception:
        return None


def _coalesce(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def _extract_metric(obj: Any, candidate_names: List[str]) -> Optional[float]:
    # Best effort for pandas DataFrame / Series / dict / scalar
    try:
        import pandas as pd  # local import for optional dependency patterns
        if isinstance(obj, pd.DataFrame):
            for name in candidate_names:
                if name in obj.index:
                    series = obj.loc[name]
                    return _safe_float(series.iloc[-1] if hasattr(series, "iloc") else series)
                if name in obj.columns:
                    series = obj[name]
                    return _safe_float(series.iloc[-1] if hasattr(series, "iloc") else series)
            flat = obj.stack().dropna()
            if not flat.empty:
                return _safe_float(flat.iloc[-1])
        if isinstance(obj, pd.Series):
            for name in candidate_names:
                if name in obj.index:
                    return _safe_float(obj[name])
            return _safe_float(obj.iloc[-1]) if not obj.empty else None
    except Exception:
        pass

    if isinstance(obj, dict):
        for name in candidate_names:
            if name in obj:
                return _safe_float(obj[name])
        for v in obj.values():
            candidate = _extract_metric(v, candidate_names)
            if candidate is not None:
                return candidate
    return _safe_float(obj)


def _fmt_num(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:.2f}"


if __name__ == "__main__":
    main()
