"""
analysis_profiles.py — Cognitive analysis profiles for pipeline_runner.

Each profile defines HOW to analyze a company based on WHAT it is.
This replaces the Platonic "same 8 steps for everything" with
industry-native analytical paths.

Usage:
    from analysis_profiles import get_profile
    profile = get_profile(sector, industry)
    # profile.steps, profile.valuation_primary, profile.skip_forensic, etc.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class AnalysisProfile:
    """Defines the analytical path for a company based on its nature."""

    name: str
    description: str

    # Pipeline step control
    run_forensic: bool = True           # Beneish/accruals — skip for financials/REIT
    run_filing: bool = True
    run_insider: bool = True
    run_valuation: bool = True
    run_peers: bool = True

    # Valuation routing override
    valuation_primary: str = "auto"     # auto / dcf / ps / evgp / pb / pev / pffo / pe
    valuation_notes: str = ""

    # Health checker context
    health_skip_margin: bool = False    # Skip margin check entirely (financials)
    health_skip_leverage: bool = False  # Skip D/E check (captive finance, REITs)

    # Extra Tavily search queries (appended to default recon)
    extra_recon_queries: List[str] = field(default_factory=list)

    # Key metrics to extract from filing (beyond defaults)
    key_metrics: List[str] = field(default_factory=list)

    # PM briefing: what to focus on in Bear/Bull
    bear_focus: List[str] = field(default_factory=list)
    bull_focus: List[str] = field(default_factory=list)

    # Sector addenda reference
    claude_md_section: str = ""


# ── Profile Definitions ──────────────────────────────────────────────────────

PROFILES: Dict[str, AnalysisProfile] = {}


def _register(key: str, profile: AnalysisProfile):
    PROFILES[key] = profile


# 1. Default / General
_register("default", AnalysisProfile(
    name="General",
    description="Standard pipeline for most companies",
    bear_focus=["revenue quality", "margin sustainability", "capital efficiency"],
    bull_focus=["growth durability", "competitive position", "FCF generation"],
))

# 2. SaaS / High-Growth Software
_register("saas", AnalysisProfile(
    name="SaaS / High-Growth Software",
    description="ARR-driven, typically loss-making, high GM. Focus on unit economics not GAAP profit.",
    valuation_primary="ps",
    valuation_notes="P/S primary if GM>70% + growth>20%. Check Rule of 40. SBC-adjusted FCF mandatory.",
    extra_recon_queries=[
        "{company} ARR net retention rate NRR churn",
        "{company} stock-based compensation SBC dilution",
    ],
    key_metrics=["ARR", "NRR/NDR", "Rule of 40", "SBC as % of revenue", "non-GAAP vs GAAP margin gap"],
    bear_focus=["SBC-adjusted FCF (may be zero or negative)", "customer concentration",
                "NDR trend (declining = churn)", "path to GAAP profitability timeline"],
    bull_focus=["ARR growth + expansion rate", "GM >70% quality", "platform stickiness / switching cost"],
    claude_md_section="§8.1 or §8.2",
))

# 3. Insurance / Life
_register("insurance", AnalysisProfile(
    name="Insurance / Financials — Balance Sheet Driven",
    description="P/EV or P/B anchor. Revenue meaningless (premium vs total income). Skip forensic.",
    run_forensic=False,
    health_skip_margin=True,
    health_skip_leverage=True,
    valuation_primary="pb",
    valuation_notes="P/EV primary, P/B secondary. P/E tertiary. DCF not applicable. Need EV assumptions from filing.",
    extra_recon_queries=[
        "{company} embedded value NBV new business value solvency",
        "{company} investment portfolio yield asset allocation interest rate sensitivity",
    ],
    key_metrics=["Embedded Value", "NBV + NBV margin", "COR (P&C)", "Solvency ratio (C-ROSS/RBC)",
                 "Investment yield", "VNB growth", "Surrender rate"],
    bear_focus=["interest rate impact on investment portfolio", "reserve adequacy",
                "NBV quality (bancassurance vs agent channel)", "solvency margin erosion"],
    bull_focus=["NBV growth sustainability", "underwriting discipline (COR trend)",
                "P/EV discount to historical mean", "dividend yield as floor"],
    claude_md_section="§8.4",
))

# 4. REIT
_register("reit", AnalysisProfile(
    name="REIT — Income/Asset Driven",
    description="P/FFO primary. GAAP NI distorted by D&A. Focus on occupancy, cap rate, lease structure.",
    run_forensic=False,
    health_skip_leverage=True,  # REITs are structurally leveraged
    valuation_primary="pffo",
    valuation_notes="P/FFO or P/AFFO primary. NAV secondary. P/E NOT applicable (D&A distortion). "
                    "Manually compute FFO = NI + D&A - gains on sale.",
    extra_recon_queries=[
        "{company} occupancy rate lease expiry tenant concentration",
        "{company} cap rate NAV interest rate sensitivity refinancing",
    ],
    key_metrics=["FFO/AFFO per share", "Occupancy rate", "Weighted avg lease term",
                 "Top tenant concentration", "Cap rate / implied cap rate", "Debt maturity schedule"],
    bear_focus=["interest rate → cap rate expansion → NAV decline",
                "refinancing risk (debt maturity schedule)", "tenant credit quality",
                "occupancy trend in key markets"],
    bull_focus=["FFO growth trajectory", "rent escalators built into leases",
                "acquisition pipeline at accretive cap rates", "dividend coverage ratio"],
    claude_md_section="§8.3 (capital-intensive)",
))

# 5. Consumer / Brand / Restaurant
_register("consumer", AnalysisProfile(
    name="Consumer / Brand / Restaurant",
    description="ASP, SSS, channel mix are the core variables. Brand premium durability is thesis.",
    valuation_primary="auto",
    valuation_notes="P/S only if GM>50%. Else EV/EBITDA or EV/GP. DCF when multi-year FCF visible.",
    extra_recon_queries=[
        "{company} same store sales ASP average selling price trend",
        "{company} store openings closures franchise expansion",
        "{company} competitor market share price war",
    ],
    key_metrics=["SSS (same-store sales)", "ASP trend", "Store count (open/close/net)",
                 "Franchise vs direct ratio", "Customer traffic vs ticket", "Inventory turnover"],
    bear_focus=["ASP decline = brand erosion (§8.5 single-condition trigger)",
                "SSS positive + total rev negative = survivorship bias (P7)",
                "channel concentration risk", "private label / competitor displacement"],
    bull_focus=["pricing power durability", "franchise model scalability",
                "store-level unit economics improving", "brand differentiation vs commoditized peers"],
    claude_md_section="§8.5",
))

# 6. Pharma / Biotech
_register("pharma", AnalysisProfile(
    name="Pharma / Biotech / Healthcare",
    description="Pipeline risk is #1. Patent cliff timeline. Capex norms differ (R&D heavy, WC long).",
    valuation_primary="pe",
    valuation_notes="P/E for profitable diversified. rNPV DCF for pre-revenue biotech. "
                    "EV/Rev for high-growth commercial-stage. Capex/DA >3x normal during mfg scale-up.",
    extra_recon_queries=[
        "{company} FDA approval pipeline clinical trial results patent expiry",
        "{company} generic biosimilar competition pricing reimbursement",
    ],
    key_metrics=["Revenue by drug/product", "Patent expiry dates for top products",
                 "Pipeline stage distribution", "R&D as % of revenue", "Regulatory pipeline"],
    bear_focus=["patent cliff revenue-at-risk", "FDA rejection/CRL risk",
                "generic/biosimilar competition timeline", "pricing/reimbursement policy changes"],
    bull_focus=["pipeline value (# of Phase 3 candidates)", "revenue diversification",
                "blockbuster drug growth runway", "M&A optionality"],
    claude_md_section="§8.6",
))

# 7. Cyclical / Commodity / Oil & Gas
_register("cyclical", AnalysisProfile(
    name="Cyclical / Commodity / Mining / Oil & Gas",
    description="Don't use current revenue growth for valuation (P10). Forward indicators matter.",
    valuation_primary="auto",
    valuation_notes="EV/EBITDA primary for E&P/mining. Normalize earnings through cycle. "
                    "Current revenue growth lags commodity price 6-12 months.",
    extra_recon_queries=[
        "{company} order backlog production guidance commodity exposure hedging",
        "{company} AISC all-in sustaining cost reserve replacement capex cycle",
    ],
    key_metrics=["Order backlog / book-to-bill", "Production volume + guidance",
                 "AISC or lifting cost", "Reserve/resource base", "Hedging position",
                 "Commodity price sensitivity (per $10 change)"],
    bear_focus=["commodity price reversal scenario", "cost inflation (AISC/lifting cost)",
                "capex cycle: value-accretive or obligatory?", "E&P capex cut cascade risk"],
    bull_focus=["forward order book / backlog growth", "cost discipline vs peers",
                "structural supply tightness duration", "shareholder return at current commodity"],
    claude_md_section="§8.3 (capital-intensive)",
))

# 8. Auto / EV
_register("auto", AnalysisProfile(
    name="Auto / EV / Mobility",
    description="Unit economics per vehicle. Production vs delivery gap. No SaaS multiples on hardware.",
    valuation_primary="pe",
    valuation_notes="P/E for profitable OEMs. EV/EBITDA capital-intensive. "
                    "P/S only if >40% rev growth + path to profitability. No SaaS multiples on hardware.",
    extra_recon_queries=[
        "{company} delivery production volume quarterly margin per vehicle",
        "{company} order backlog cancellation rate autonomous driving regulatory",
    ],
    key_metrics=["Deliveries vs production (gap = stuffing risk)", "GM per vehicle",
                 "ASP trend", "Order backlog / cancellation rate", "Battery cost trajectory"],
    bear_focus=["delivery miss vs guidance >10%", "GM compression >300bps QoQ",
                "inventory build / channel stuffing", "competition: no moat in auto"],
    bull_focus=["volume ramp + margin expansion simultaneously", "technology differentiation",
                "order book growth", "regulatory tailwinds (EV mandates)"],
    claude_md_section="§8.7",
))

# 9. AI / Compute-Heavy
_register("ai_compute", AnalysisProfile(
    name="AI / Compute-Heavy / Model-Driven",
    description="Cost-of-inference scrutiny mandatory. Weak GM → EV/GP over P/S.",
    valuation_primary="evgp",
    valuation_notes="Weak/unstable GM → EV/GP primary. P/S only with documented GM improvement. "
                    "Open-source metrics = falsification only.",
    extra_recon_queries=[
        "{company} inference cost compute cost gross margin trend GPU spend",
        "{company} AI model benchmark open source competition moat",
    ],
    key_metrics=["Inference cost per query/token", "GM trend (improving or compressing)",
                 "Compute capex as % of revenue", "Customer concentration (hyperscaler dependency)"],
    bear_focus=["inference cost outpacing revenue = GP destruction",
                "demo ≠ proof of moat (insufficient-evidence standard)",
                "open-source substitution risk", "customer concentration"],
    bull_focus=["unit cost decline trajectory", "commercial traction (paying customers, not stars)",
                "platform lock-in / switching cost", "margin expansion path"],
    claude_md_section="§8.1",
))

# 10. Banks
_register("bank", AnalysisProfile(
    name="Bank / Lending",
    description="NIM decomposition. Credit quality > growth. P/B + ROE/ROTCE primary.",
    run_forensic=False,
    health_skip_margin=True,
    health_skip_leverage=True,
    valuation_primary="pb",
    valuation_notes="P/B primary. ROTCE/ROE vs CoE. Earnings multiples secondary.",
    extra_recon_queries=[
        "{company} NIM net interest margin credit quality NPL provision",
        "{company} capital adequacy CET1 stress test deposit growth",
    ],
    key_metrics=["NIM", "NPL ratio", "Provision coverage", "CET1 ratio",
                 "ROE / ROTCE", "Loan growth", "Deposit mix (low-cost vs wholesale)"],
    bear_focus=["credit cycle deterioration", "NIM compression from rate changes",
                "commercial real estate exposure", "deposit flight risk"],
    bull_focus=["NIM expansion", "credit quality outperformance vs cycle",
                "capital return (buyback + dividend)", "efficiency ratio improvement"],
    claude_md_section="§8.4",
))


# ── Profile Matching ─────────────────────────────────────────────────────────

# Industry keyword → profile key mapping (checked first, most specific)
INDUSTRY_PROFILE_MAP = {
    "insurance": "insurance",
    "reit": "reit",
    "real estate investment": "reit",
    "banks": "bank",
    "credit services": "bank",
    "mortgage": "bank",
    "restaurants": "consumer",
    "beverages": "consumer",
    "apparel": "consumer",
    "luxury": "consumer",
    "footwear": "consumer",
    "retail": "consumer",
    "grocery": "consumer",
    "discount stores": "consumer",
    "drug manufacturers": "pharma",
    "biotechnology": "pharma",
    "medical devices": "pharma",
    "diagnostics": "pharma",
    "oil & gas": "cyclical",
    "gold": "cyclical",
    "silver": "cyclical",
    "copper": "cyclical",
    "mining": "cyclical",
    "steel": "cyclical",
    "aluminum": "cyclical",
    "coal": "cyclical",
    "auto manufacturers": "auto",
    "farm & heavy construction": "cyclical",
    "trucking": "cyclical",
    "airlines": "cyclical",
    "shipping": "cyclical",
    "semiconductors": "ai_compute",
    "software - infrastructure": "saas",
    "software - application": "saas",
    "information technology": "saas",
    "internet content": "saas",
    "electronic gaming": "saas",
}

# Sector-level fallback (less specific)
SECTOR_PROFILE_MAP = {
    "financial services": "bank",
    "real estate": "reit",
    "healthcare": "pharma",
    "energy": "cyclical",
    "basic materials": "cyclical",
    "consumer cyclical": "consumer",
    "consumer defensive": "consumer",
    "technology": "saas",
    "communication services": "saas",
    "industrials": "default",
    "utilities": "default",
}


def get_profile(sector: str = "", industry: str = "") -> AnalysisProfile:
    """Match sector/industry to the best analysis profile.
    Industry match takes priority over sector match.
    """
    ind_lower = industry.lower() if industry else ""
    sec_lower = sector.lower() if sector else ""

    # Try industry match first (most specific)
    for keyword, profile_key in INDUSTRY_PROFILE_MAP.items():
        if keyword in ind_lower:
            profile = PROFILES[profile_key]
            return profile

    # Fallback to sector match
    for keyword, profile_key in SECTOR_PROFILE_MAP.items():
        if keyword in sec_lower:
            return PROFILES[profile_key]

    return PROFILES["default"]


def print_profile(profile: AnalysisProfile):
    """Human-readable profile summary."""
    print(f"  Profile: {profile.name}")
    print(f"  Description: {profile.description}")
    print(f"  Valuation: {profile.valuation_primary} — {profile.valuation_notes[:100]}")
    print(f"  Forensic: {'skip' if not profile.run_forensic else 'enabled'}")
    print(f"  Key metrics: {', '.join(profile.key_metrics[:5])}")
    print(f"  Bear focus: {profile.bear_focus[0] if profile.bear_focus else 'standard'}")
    if profile.claude_md_section:
        print(f"  CLAUDE.md: {profile.claude_md_section}")
