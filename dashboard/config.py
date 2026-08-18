"""Central configuration for the dashboard: paths, thresholds, and the visual theme.

Nothing here talks to Streamlit or SQLite directly — it's pure constants so every
other module (database, components, charts, pages) reads the same values.
"""
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

DASHBOARD_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = DASHBOARD_ROOT.parent
DB_PATH = PROJECT_ROOT / "data" / "filings.db"
STYLES_PATH = DASHBOARD_ROOT / "styles" / "main.css"

# ── App metadata ─────────────────────────────────────────────────────────────

APP_TITLE = "OTC Filing Intelligence"
APP_ICON = "◆"

# ── Business thresholds ──────────────────────────────────────────────────────

IMPORTANCE_THRESHOLD = 70  # score >= this is an "important event" for KPI purposes
DEFAULT_PAGE_SIZE = 50
TOP_COMPANIES_LIMIT = 15

# A handful of filings carry multi-megabyte clean_text (an un-stripped exhibit,
# usually). Rendering that much text into one HTML node freezes the browser
# tab, so the Detalle page truncates and points to EDGAR for the rest.
MAX_FULL_TEXT_CHARS = 50_000

# ── Caching ───────────────────────────────────────────────────────────────────

CACHE_TTL_SECONDS = 300  # data is refreshed by the backend at most once a day

# ── Theme: dark financial-terminal palette ───────────────────────────────────
# Values are the dark-mode steps of the dataviz skill's validated reference
# palette (references/palette.md) — a fixed dark theme (not a light/dark
# toggle) to match the Bloomberg/PitchBook-style brief.

THEME = {
    "page_bg": "#0d0d0d",
    "surface": "#1a1a19",
    "surface_alt": "#222221",
    "text_primary": "#ffffff",
    "text_secondary": "#c3c2b7",
    "text_muted": "#898781",
    "gridline": "#2c2c2a",
    "baseline": "#383835",
    "border": "rgba(255,255,255,0.10)",
    # Categorical (fixed order — never cycled or reassigned per filter)
    "cat_blue": "#3987e5",
    "cat_orange": "#d95926",
    "cat_aqua": "#199e70",
    "cat_yellow": "#c98500",
    "cat_magenta": "#d55181",
    "cat_green": "#008300",
    "cat_violet": "#9085e9",
    "cat_red": "#e66767",
    # Status (fixed, reserved meaning — always icon/label + color)
    "status_good": "#0ca30c",
    "status_warning": "#fab219",
    "status_serious": "#ec835a",
    "status_critical": "#d03b3b",
    # Sequential blue ramp (light -> dark), for magnitude encodings
    "seq_blue": [
        "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b",
    ],
}

CATEGORICAL_ORDER = [
    THEME["cat_blue"], THEME["cat_orange"], THEME["cat_aqua"], THEME["cat_yellow"],
    THEME["cat_magenta"], THEME["cat_green"], THEME["cat_violet"], THEME["cat_red"],
]

# market_impact -> status color (fixed mapping, not a themed/categorical choice)
MARKET_IMPACT_COLORS = {
    "LOW": THEME["status_good"],
    "MEDIUM": THEME["status_warning"],
    "HIGH": THEME["status_serious"],
    "VERY_HIGH": THEME["status_critical"],
}
