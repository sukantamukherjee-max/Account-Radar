"""Account Radar theme engine.

Presentation only -- no research, evidence, opportunity or People logic
lives here. app.py calls three functions:

    init_theme_state(st)       # once, before any widget or CSS
    inject_theme(st)           # once, right after init (paints the page)
    render_theme_controls(st)  # draws the sidebar "Appearance" section

The selected palette and mode live in st.session_state["ar_palette"] and
st.session_state["ar_mode"]. Every colour the GUI uses is a CSS custom
property (--ar-*) generated from THEMES; all Account Radar components and the
Streamlit widgets we restyle read those variables, so switching palette or
mode repaints the whole page on the next rerun. No Streamlit config is
changed at runtime.

Token roles (every palette defines all of them for both modes):

    bg              application background
    surface         cards, sidebar, inputs
    surface_2       subtle fills (table headers, hovers, alert fills)
    border          hairlines and card borders
    text            headings and primary body text
    text_secondary  supporting body text
    muted           captions / labels (still >= 4.5:1 on bg and surfaces)
    primary         filled primary buttons, active tab underline, accents
    primary_hover   primary button hover fill
    on_primary      text on a primary fill
    primary_text    primary colour used AS TEXT (links, active tab label)
    primary_soft    tinted background for highlighted cards
    danger / success / warning / info   status text colours
    shadow          card shadow
"""

from __future__ import annotations

from typing import Any

PALETTE_KEY = "ar_palette"
MODE_KEY = "ar_mode"
MODES = ("Light", "Dark")
DEFAULT_PALETTE = "Executive Blue"
DEFAULT_MODE = "Light"

_LIGHT_STATUS = {
    "danger": "#B91C1C",
    "success": "#047857",
    "warning": "#92400E",
    "info": "#0369A1",
    # Semantic accents (KPI cards, category cards, badges): colour + soft fill.
    "blue": "#1D4ED8", "blue_soft": "#EFF5FF",
    "green": "#047857", "green_soft": "#ECFDF5",
    "amber": "#B45309", "amber_soft": "#FFF7EB",
    "red": "#BE123C", "red_soft": "#FFF1F2",
    "purple": "#6D28D9", "purple_soft": "#F5F3FF",
    "slate": "#334155", "slate_soft": "#F1F5F9",
}
_DARK_STATUS = {
    "danger": "#FCA5A5",
    "success": "#6EE7B7",
    "warning": "#FCD34D",
    "info": "#7DD3FC",
    "blue": "#8AB8FF", "blue_soft": "#132443",
    "green": "#6EE7B7", "green_soft": "#0E2A22",
    "amber": "#FCD34D", "amber_soft": "#2A2111",
    "red": "#FF8A94", "red_soft": "#2C1519",
    "purple": "#C4B5FD", "purple_soft": "#221A3D",
    "slate": "#CBD5E1", "slate_soft": "#1C2533",
}


def _light(**tokens: str) -> dict[str, str]:
    base = {
        "bg": "#F4F6FA",
        "surface": "#FFFFFF",
        "surface_2": "#F1F4F9",
        "border": "#E3E8EF",
        "text": "#0F172A",
        "text_secondary": "#334155",
        "muted": "#4B5A70",
        "on_primary": "#FFFFFF",
        "shadow": "0 1px 2px rgba(15, 23, 42, 0.06), 0 4px 14px rgba(15, 23, 42, 0.05)",
        **_LIGHT_STATUS,
    }
    base.update(tokens)
    return base


def _dark(**tokens: str) -> dict[str, str]:
    base = {
        "bg": "#0B1120",
        "surface": "#131B2C",
        "surface_2": "#1A2438",
        "border": "#2B3850",
        "text": "#F1F5F9",
        "text_secondary": "#D3DCE8",
        "muted": "#A9B6C9",
        "on_primary": "#FFFFFF",
        "shadow": "0 1px 2px rgba(0, 0, 0, 0.40), 0 6px 18px rgba(0, 0, 0, 0.28)",
        **_DARK_STATUS,
    }
    base.update(tokens)
    return base


THEMES: dict[str, dict[str, dict[str, str]]] = {
    "Executive Blue": {
        "light": _light(
            primary="#1D4ED8", primary_hover="#1E40AF", primary_text="#1D4ED8", primary_soft="#EAF1FE",
        ),
        "dark": _dark(
            primary="#2563EB", primary_hover="#1D4ED8", primary_text="#8AB8FF", primary_soft="#16264A",
        ),
    },
    "Otech Red": {
        "light": _light(
            bg="#F6F5F5", surface_2="#F4F1F1", border="#E8E2E3",
            primary="#C8102E", primary_hover="#A50D26", primary_text="#B30E29", primary_soft="#FDECEE",
            danger="#9F1239",
        ),
        "dark": _dark(
            bg="#0F0F12", surface="#18181D", surface_2="#202027", border="#33333D",
            primary="#C8102E", primary_hover="#A50D26", primary_text="#FF8A94", primary_soft="#2A1519",
        ),
    },
    "Enterprise Emerald": {
        "light": _light(
            bg="#F3F7F5", surface_2="#EEF4F1", border="#DCE8E2",
            primary="#047857", primary_hover="#065F46", primary_text="#047857", primary_soft="#E6F4EE",
            success="#065F46",
        ),
        "dark": _dark(
            bg="#08110E", surface="#101C18", surface_2="#162621", border="#26403A",
            primary="#047857", primary_hover="#065F46", primary_text="#5EE0AE", primary_soft="#0F2A22",
        ),
    },
    "Technology Indigo": {
        "light": _light(
            bg="#F5F5FB", surface_2="#F0F0F9", border="#E4E4F2",
            primary="#4338CA", primary_hover="#3730A3", primary_text="#4338CA", primary_soft="#EEEDFD",
        ),
        "dark": _dark(
            bg="#0C0B18", surface="#151426", surface_2="#1D1B33", border="#323052",
            primary="#4F46E5", primary_hover="#4338CA", primary_text="#B4B9FF", primary_soft="#1F1D44",
        ),
    },
    "Neutral Slate": {
        "light": _light(
            bg="#F5F6F7", surface_2="#F1F3F5", border="#E2E5E9",
            primary="#334155", primary_hover="#1E293B", primary_text="#1E3A5F", primary_soft="#EDF0F4",
        ),
        "dark": _dark(
            bg="#0E1013", surface="#171A1F", surface_2="#1F2329", border="#323842",
            primary="#475569", primary_hover="#334155", primary_text="#BFD3EA", primary_soft="#1F2733",
        ),
    },
}

# Premium header band (dark navy / charcoal in BOTH modes) per palette:
# (background, gradient end, accent text). Header text is always white.
_HEADER_BANDS = {
    "Executive Blue": ("#0A1A36", "#173A7A", "#9CC3FF"),
    "Otech Red": ("#131317", "#3A1219", "#FF8A94"),
    "Enterprise Emerald": ("#07211B", "#0F4637", "#7BE8BF"),
    "Technology Indigo": ("#141236", "#2B2575", "#C3C7FF"),
    "Neutral Slate": ("#141920", "#2D3846", "#C5D6EA"),
}
for _name, (_bg, _bg2, _accent) in _HEADER_BANDS.items():
    for _mode in ("light", "dark"):
        THEMES[_name][_mode].update(
            header_bg=_bg, header_bg_2=_bg2, header_text="#FFFFFF",
            header_muted="#C7D0DE", header_accent=_accent,
        )

PALETTES = tuple(THEMES.keys())


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def init_theme_state(st: Any) -> None:
    """Seed / repair the palette and mode in session state. A value left
    over from an older build (e.g. a palette name that no longer exists) is
    reset to the default instead of raising a KeyError."""
    if st.session_state.get(PALETTE_KEY) not in THEMES:
        st.session_state[PALETTE_KEY] = DEFAULT_PALETTE
    if st.session_state.get(MODE_KEY) not in MODES:
        st.session_state[MODE_KEY] = DEFAULT_MODE


def current_selection(st: Any) -> tuple[str, str]:
    palette = st.session_state.get(PALETTE_KEY, DEFAULT_PALETTE)
    mode = st.session_state.get(MODE_KEY, DEFAULT_MODE)
    if palette not in THEMES:
        palette = DEFAULT_PALETTE
    if mode not in MODES:
        mode = DEFAULT_MODE
    return palette, mode


def get_theme_tokens(palette: str, mode: str) -> dict[str, str]:
    palette = palette if palette in THEMES else DEFAULT_PALETTE
    mode_key = "dark" if str(mode).lower() == "dark" else "light"
    return dict(THEMES[palette][mode_key])


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

def render_theme_controls(st: Any) -> None:
    """Sidebar "Appearance" section. The widgets are keyed to the session
    state entries, so Streamlit updates the selection before the next rerun
    and inject_theme() paints the new theme straight away."""
    init_theme_state(st)
    st.sidebar.divider()
    st.sidebar.subheader("Appearance")
    st.sidebar.selectbox("Colour palette", list(PALETTES), key=PALETTE_KEY)
    st.sidebar.radio("Mode", list(MODES), key=MODE_KEY, horizontal=True)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

def _css_variables(tokens: dict[str, str], mode: str) -> str:
    lines = [f"    --ar-{name.replace('_', '-')}: {value};" for name, value in tokens.items()]
    scheme = "dark" if mode == "dark" else "light"
    return ":root {\n" + "\n".join(lines) + f"\n    color-scheme: {scheme};\n}}\n"


# Static rules -- every colour comes from a --ar-* variable. Plain string
# (not an f-string) so CSS braces never need escaping.
_BASE_CSS = """
/* ---------- application shell ---------- */
html, body, .stApp,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
.main {
    background: var(--ar-bg) !important;
    color: var(--ar-text) !important;
    font-family: "Source Sans", "Source Sans Pro", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
}
[data-testid="stHeader"] {
    background: var(--ar-bg) !important;
    border-bottom: 1px solid var(--ar-border);
}
[data-testid="stToolbar"] button, [data-testid="stToolbar"] svg,
[data-testid="stHeader"] button, [data-testid="stHeader"] svg {
    color: var(--ar-text-secondary) !important;
    fill: currentColor;
}

/* ---------- typography ---------- */
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
[data-testid="stHeading"], [data-testid="stHeading"] * {
    color: var(--ar-text) !important;
    letter-spacing: -0.01em;
}
.stApp h1 { font-weight: 750; }
.stApp h2, .stApp h3 { font-weight: 700; }
.stApp h4, .stApp h5, .stApp h6 { font-weight: 650; }
[data-testid="stMarkdownContainer"],
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] ol,
[data-testid="stMarkdownContainer"] ul,
[data-testid="stText"] {
    color: var(--ar-text-secondary);
}
[data-testid="stMarkdownContainer"] strong,
[data-testid="stMarkdownContainer"] b {
    color: var(--ar-text);
}
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p,
.stCaption {
    color: var(--ar-muted) !important;
    opacity: 1 !important;   /* Streamlit fades captions to 0.6 -- the "hazy text" */
}
.stApp a, .stApp a:visited,
[data-testid="stMarkdownContainer"] a {
    color: var(--ar-primary-text) !important;
    text-decoration: underline;
    text-underline-offset: 2px;
}
.stApp a:hover { text-decoration-thickness: 2px; }
.stApp hr, [data-testid="stDivider"] hr {
    border: none !important;
    border-top: 1px solid var(--ar-border) !important;
    background: transparent !important;
}
.stApp code {
    color: var(--ar-text) !important;
    background: var(--ar-surface-2) !important;
    border: 1px solid var(--ar-border);
    border-radius: 4px;
}
.stApp pre, [data-testid="stCode"], [data-testid="stCode"] pre {
    background: var(--ar-surface-2) !important;
    color: var(--ar-text) !important;
    border: 1px solid var(--ar-border);
    border-radius: 8px;
}

/* ---------- sidebar ---------- */
[data-testid="stSidebar"],
[data-testid="stSidebar"] > div,
[data-testid="stSidebarContent"] {
    background: var(--ar-surface) !important;
}
[data-testid="stSidebar"] {
    border-right: 1px solid var(--ar-border) !important;
}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3, [data-testid="stSidebar"] h4 {
    color: var(--ar-text) !important;
    font-size: 0.95rem !important;
    font-weight: 700 !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color: var(--ar-text-secondary); }
[data-testid="stSidebarCollapseButton"] button, [data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapsedControl"] button, [data-testid="stSidebarCollapsedControl"] svg,
[data-testid="stExpandSidebarButton"], [data-testid="stExpandSidebarButton"] svg {
    color: var(--ar-text-secondary) !important;
    fill: currentColor;
}

/* Material-icon glyphs (Streamlit >= 1.4x render icons as text) */
[data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"],
[data-testid="stSidebarCollapsedControl"] [data-testid="stIconMaterial"],
[data-testid="stHeader"] [data-testid="stIconMaterial"],
[data-testid="stExpander"] summary [data-testid="stIconMaterial"],
[data-testid="stFileUploaderDropzone"] [data-testid="stIconMaterial"] {
    color: var(--ar-text-secondary) !important;
}
[data-testid="stAlertContentInfo"] [data-testid="stIconMaterial"] { color: var(--ar-info) !important; }
[data-testid="stAlertContentWarning"] [data-testid="stIconMaterial"] { color: var(--ar-warning) !important; }
[data-testid="stAlertContentError"] [data-testid="stIconMaterial"] { color: var(--ar-danger) !important; }
[data-testid="stAlertContentSuccess"] [data-testid="stIconMaterial"] { color: var(--ar-success) !important; }

/* ---------- widget labels ---------- */
[data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] label,
.stApp label {
    color: var(--ar-text) !important;
    font-weight: 600;
}
[data-testid="stRadioOption"] > div > div > div:first-child { background: var(--ar-muted) !important; }
[data-testid="stRadioOption"] > div > div > div:first-child > div { background: var(--ar-surface) !important; }
[data-testid="stRadioOption"][data-selected="true"] > div > div > div:first-child { background: var(--ar-primary) !important; }
[data-testid="stRadioOption"][data-selected="true"] > div > div > div:first-child > div { background: var(--ar-on-primary) !important; }
[data-testid="stRadio"] label p, [data-testid="stCheckbox"] label p,
[data-testid="stRadio"] [role="radiogroup"] label, [data-testid="stCheckbox"] label span {
    color: var(--ar-text-secondary) !important;
    font-weight: 500;
}

/* ---------- inputs / selects ---------- */
[data-baseweb="input"], [data-baseweb="textarea"],
[data-baseweb="select"] > div,
[data-testid="stTextInputRootElement"], [data-testid="stTextAreaRootElement"],
[data-testid="stSelectbox"] [role="group"], [data-testid="stMultiSelect"] [role="group"],
[data-testid="stNumberInputContainer"] {
    background: var(--ar-surface) !important;
    border: 1px solid var(--ar-border) !important;
    border-radius: 8px !important;
    color: var(--ar-text) !important;
}
[data-baseweb="base-input"] { background: transparent !important; border: none !important; }
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within,
[data-baseweb="select"] > div:focus-within,
[data-testid="stTextInputRootElement"]:focus-within, [data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stSelectbox"] [role="group"]:focus-within {
    border-color: var(--ar-primary) !important;
    box-shadow: 0 0 0 1px var(--ar-primary) !important;
}
.stApp input, .stApp textarea, [data-baseweb="select"] input,
[data-baseweb="select"] [data-testid="stSelectboxVirtualDropdown"],
[data-baseweb="select"] div[value], [data-baseweb="select"] span {
    color: var(--ar-text) !important;
    background: transparent !important;
    -webkit-text-fill-color: var(--ar-text) !important;
}
.stApp input::placeholder, .stApp textarea::placeholder {
    color: var(--ar-muted) !important;
    -webkit-text-fill-color: var(--ar-muted) !important;
    opacity: 1;
}
[data-testid="stSelectbox"] [role="group"] button { background: transparent !important; }
[data-baseweb="select"] svg, [data-baseweb="input"] svg, [data-testid="stSelectbox"] svg { color: var(--ar-text-secondary) !important; fill: currentColor; }
[data-testid="stNumberInputContainer"] button { background: var(--ar-surface-2) !important; color: var(--ar-text) !important; }

/* dropdown menus render in a portal outside the app container */
[data-baseweb="popover"] > div, [data-baseweb="popover"] ul,
[data-baseweb="menu"], [role="listbox"],
[data-testid="stSelectboxVirtualDropdown"] {
    background: var(--ar-surface) !important;
    border-color: var(--ar-border) !important;
}
[data-baseweb="popover"] li, [role="option"], [role="option"] * {
    color: var(--ar-text) !important;
    background: transparent;
}
[data-baseweb="popover"] li:hover, [role="option"]:hover,
[role="option"][aria-selected="true"], [role="option"][data-focused="true"] {
    background: var(--ar-primary-soft) !important;
}

/* file uploader */
[data-testid="stFileUploaderDropzone"] {
    background: var(--ar-surface-2) !important;
    border: 1px dashed var(--ar-border) !important;
    color: var(--ar-text-secondary) !important;
}
[data-testid="stFileUploaderDropzone"] span, [data-testid="stFileUploaderDropzone"] small,
[data-testid="stFileUploaderDropzoneInstructions"] * {
    color: var(--ar-text-secondary) !important;
}
[data-testid="stFileUploaderFile"], [data-testid="stFileUploaderFileName"] { color: var(--ar-text) !important; }

/* ---------- buttons ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
[data-testid="stFileUploaderDropzone"] button,
[data-testid="stBaseButton-secondary"], [data-testid="baseButton-secondary"],
[data-testid="stBaseLinkButton-secondary"] {
    background: var(--ar-surface) !important;
    color: var(--ar-text) !important;
    border: 1px solid var(--ar-border) !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    box-shadow: none !important;
}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover,
[data-testid="stFileUploaderDropzone"] button:hover,
[data-testid="stBaseButton-secondary"]:hover, [data-testid="baseButton-secondary"]:hover,
[data-testid="stBaseLinkButton-secondary"]:hover {
    border-color: var(--ar-primary) !important;
    color: var(--ar-primary-text) !important;
    background: var(--ar-primary-soft) !important;
}
[data-testid="stBaseLinkButton-secondary"] p, [data-testid="stBaseLinkButton-secondary"] * { color: inherit !important; text-decoration: none !important; }
.stButton > button p, .stDownloadButton > button p, .stFormSubmitButton > button p,
.stButton > button [data-testid="stMarkdownContainer"] p { color: inherit !important; }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"],
.stButton > button[data-testid="stBaseButton-primary"],
.stFormSubmitButton > button[data-testid="stBaseButton-primary"],
.stDownloadButton > button[data-testid="stBaseButton-primary"],
.stButton > button[data-testid="baseButton-primary"],
.stFormSubmitButton > button[data-testid="stBaseButton-primaryFormSubmit"],
[data-testid="stBaseButton-primaryFormSubmit"],
[data-testid="stBaseButton-primary"], [data-testid="baseButton-primary"] {
    background: var(--ar-primary) !important;
    border-color: var(--ar-primary) !important;
    color: var(--ar-on-primary) !important;
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover,
.stButton > button[data-testid="stBaseButton-primary"]:hover,
.stFormSubmitButton > button[data-testid="stBaseButton-primary"]:hover,
.stButton > button[data-testid="baseButton-primary"]:hover,
[data-testid="stBaseButton-primaryFormSubmit"]:hover,
[data-testid="stBaseButton-primary"]:hover, [data-testid="baseButton-primary"]:hover {
    background: var(--ar-primary-hover) !important;
    border-color: var(--ar-primary-hover) !important;
    color: var(--ar-on-primary) !important;
}
.stButton > button:focus-visible { outline: 2px solid var(--ar-primary) !important; outline-offset: 2px; }

/* ---------- tabs ---------- */
[data-baseweb="tab-list"], [data-testid="stTabs"] [role="tablist"] {
    gap: 4px;
    border-bottom: 1px solid var(--ar-border);
}
[data-baseweb="tab-border"] { background: var(--ar-border) !important; }
[data-baseweb="tab"], [data-testid="stTabs"] button[role="tab"], [data-testid="stTab"] {
    padding: 8px 14px !important;
    background: transparent !important;
    color: var(--ar-text-secondary) !important;
    font-weight: 650 !important;
    letter-spacing: 0.04em;
}
[data-baseweb="tab"] p, [data-testid="stTabs"] button[role="tab"] p,
[data-testid="stTab"] p, [data-testid="stTab"] [data-testid="stMarkdownContainer"] p {
    color: inherit !important;
    font-weight: inherit !important;
}
[data-baseweb="tab"]:hover, [data-testid="stTab"]:hover { color: var(--ar-text) !important; }
[data-baseweb="tab"][aria-selected="true"], [data-testid="stTabs"] button[aria-selected="true"],
[data-testid="stTab"][aria-selected="true"] {
    color: var(--ar-primary-text) !important;
}
[data-testid="stTab"][aria-selected="true"] > div[data-rac] { background: var(--ar-primary) !important; }
[data-baseweb="tab-highlight"] { background: var(--ar-primary) !important; height: 3px !important; }

/* ---------- metrics ---------- */
[data-testid="stMetric"] {
    background: var(--ar-surface) !important;
    border: 1px solid var(--ar-border) !important;
    border-radius: 12px !important;
    padding: 14px 16px !important;
    box-shadow: var(--ar-shadow);
}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p, [data-testid="stMetricLabel"] * {
    color: var(--ar-muted) !important;
    font-weight: 650 !important;
    font-size: 0.82rem !important;
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: clip !important;
}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * {
    color: var(--ar-text) !important;
    font-weight: 750 !important;
}
[data-testid="stMetricDelta"] { color: var(--ar-text-secondary) !important; }
/* ---------- expanders ---------- */
[data-testid="stExpander"] details, [data-testid="stExpander"] > details {
    background: var(--ar-surface) !important;
    border: 1px solid var(--ar-border) !important;
    border-radius: 10px !important;
}
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary *,
[data-testid="stExpander"] summary p {
    color: var(--ar-text) !important;
    font-weight: 600;
}
[data-testid="stExpander"] summary:hover { background: var(--ar-surface-2) !important; }
[data-testid="stExpander"] summary svg { fill: var(--ar-text-secondary) !important; color: var(--ar-text-secondary) !important; }
[data-testid="stExpanderDetails"] { background: var(--ar-surface) !important; }

/* ---------- alerts ---------- */
[data-testid="stAlert"], [data-testid="stAlertContainer"] {
    background: var(--ar-surface) !important;
    border: 1px solid var(--ar-border) !important;
    border-left: 4px solid var(--ar-info) !important;
    border-radius: 10px !important;
    color: var(--ar-text) !important;
}
[data-testid="stAlert"] *, [data-testid="stAlertContainer"] * { color: var(--ar-text) !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) { border-left-color: var(--ar-warning) !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) { border-left-color: var(--ar-danger) !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) { border-left-color: var(--ar-success) !important; }
[data-testid="stAlertContentWarning"] svg { color: var(--ar-warning) !important; fill: currentColor; }
[data-testid="stAlertContentError"] svg { color: var(--ar-danger) !important; fill: currentColor; }
[data-testid="stAlertContentSuccess"] svg { color: var(--ar-success) !important; fill: currentColor; }
[data-testid="stAlertContentInfo"] svg { color: var(--ar-info) !important; fill: currentColor; }

/* ---------- tables / dataframes ---------- */
[data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 1px solid var(--ar-border) !important;
    border-radius: 10px !important;
    overflow: hidden;
    background: var(--ar-surface);
}
[data-testid="stTable"] table { color: var(--ar-text) !important; background: var(--ar-surface) !important; }
[data-testid="stTable"] th {
    background: var(--ar-surface-2) !important;
    color: var(--ar-text) !important;
    border-color: var(--ar-border) !important;
}
[data-testid="stTable"] td { color: var(--ar-text-secondary) !important; border-color: var(--ar-border) !important; }
[data-testid="stJson"] { background: var(--ar-surface) !important; border-radius: 8px; }

/* ---------- tooltips ---------- */
[data-testid="stTooltipContent"], [data-baseweb="tooltip"] > div {
    background: var(--ar-text) !important;
    color: var(--ar-bg) !important;
}

/* ---------- spinner / progress ---------- */
[data-testid="stSpinner"], [data-testid="stSpinner"] * { color: var(--ar-text-secondary) !important; }

/* =========================================================
   Account Radar components
   ========================================================= */
.ar-product-header {
    padding: 2px 0 20px 0;
    border-bottom: 1px solid var(--ar-border);
    margin-bottom: 22px;
}
.ar-product-eyebrow {
    color: var(--ar-primary-text) !important;
    font-size: 0.74rem;
    font-weight: 750;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    margin-bottom: 6px;
}
.ar-product-title {
    color: var(--ar-text) !important;
    font-size: 2.35rem;
    line-height: 1.08;
    font-weight: 780;
    letter-spacing: -0.03em;
}
.ar-product-flow {
    margin-top: 12px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    font-size: 0.74rem;
    font-weight: 700;
    letter-spacing: 0.1em;
}
.ar-product-flow span {
    color: var(--ar-text) !important;
    background: var(--ar-surface);
    border: 1px solid var(--ar-border);
    border-radius: 999px;
    padding: 4px 11px;
}
.ar-product-flow span.ar-flow-arrow {
    color: var(--ar-primary-text) !important;
    background: transparent;
    border: none;
    padding: 0 2px;
}

.ar-signal-card {
    background: var(--ar-surface);
    border: 1px solid var(--ar-border);
    border-radius: 12px;
    padding: 15px 17px;
    min-height: 88px;
    margin-bottom: 10px;
    box-shadow: var(--ar-shadow);
}
.ar-signal-card:hover { border-color: var(--ar-primary); }
.ar-signal-label {
    color: var(--ar-muted) !important;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 8px;
}
.ar-signal-count {
    color: var(--ar-text) !important;
    font-size: 1.7rem;
    font-weight: 780;
    line-height: 1;
}

.ar-evidence-card {
    background: var(--ar-surface);
    border: 1px solid var(--ar-border);
    border-left: 4px solid var(--ar-primary);
    border-radius: 10px;
    padding: 16px 18px;
    margin: 0 0 10px 0;
    box-shadow: var(--ar-shadow);
}
.ar-evidence-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 20px;
}
.ar-evidence-type {
    color: var(--ar-primary-text) !important;
    font-size: 0.7rem;
    font-weight: 750;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    white-space: nowrap;
}
.ar-evidence-title {
    color: var(--ar-text) !important;
    font-size: 0.95rem;
    font-weight: 680;
    line-height: 1.35;
    margin-bottom: 7px;
}
.ar-evidence-text {
    color: var(--ar-text-secondary) !important;
    font-size: 0.86rem;
    line-height: 1.55;
    margin-bottom: 8px;
}
.ar-evidence-match { color: var(--ar-muted) !important; font-size: 0.76rem; }
.ar-evidence-match strong { color: var(--ar-text-secondary) !important; }
.ar-section-label {
    color: var(--ar-text) !important;
    font-size: 1.05rem;
    font-weight: 700;
    margin: 18px 0 8px 0;
}

.ar-decision-context {
    background: var(--ar-surface);
    border: 1px solid var(--ar-border);
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 8px;
}
.ar-decision-capability { color: var(--ar-text) !important; font-weight: 700; margin-bottom: 4px; }
.ar-decision-evidence { color: var(--ar-text-secondary) !important; font-size: 0.86rem; }
.ar-decision-gap {
    display: flex;
    gap: 14px;
    align-items: flex-start;
    background: var(--ar-surface);
    border: 1px solid var(--ar-border);
    border-left: 4px solid var(--ar-warning);
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 8px;
    color: var(--ar-text-secondary) !important;
}
.ar-decision-gap strong { color: var(--ar-text) !important; }
.ar-decision-gap div { color: var(--ar-text-secondary) !important; }
.ar-decision-gap-number {
    color: var(--ar-text) !important;
    font-size: 1.5rem;
    font-weight: 780;
    min-width: 2ch;
    line-height: 1.1;
}
.ar-decision-hypothesis {
    background: var(--ar-primary-soft);
    border: 1px solid var(--ar-border);
    border-left: 4px solid var(--ar-primary);
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 8px;
    color: var(--ar-text) !important;
    line-height: 1.55;
}

.ar-people-card {
    background: var(--ar-surface);
    border: 1px solid var(--ar-border);
    border-radius: 12px;
    padding: 18px;
    margin-bottom: 12px;
    box-shadow: var(--ar-shadow);
}
.ar-people-card h4, .ar-people-card strong { color: var(--ar-text) !important; }
.ar-people-card p { color: var(--ar-text-secondary) !important; }

/* =========================================================
   Executive design system (reference-driven layout)
   ========================================================= */
[data-testid="stMainBlockContainer"], .block-container {
    padding: 0 2rem 3rem 2rem !important;
    max-width: none !important;
}
/* Streamlit's top bar floats over the navy header band; its controls sit
   on a small navy pill so they stay readable when the page scrolls. */
[data-testid="stHeader"] {
    background: transparent !important;
    border-bottom: none !important;
    height: 3rem !important;
}
[data-testid="stToolbar"] { background: transparent !important; }
[data-testid="stToolbarActions"], [data-testid="stAppDeployButton"],
[data-testid="stAppDeployButton"] button, [data-testid="stMainMenu"] {
    background: var(--ar-header-bg) !important;
    border-radius: 8px;
}
[data-testid="stSidebarUserContent"] { padding-top: 0.25rem !important; }
[data-testid="stSidebarHeader"] { height: 2.6rem !important; min-height: 0 !important; padding-top: 0.6rem !important; }
[data-testid="stHeader"] button, [data-testid="stHeader"] svg, [data-testid="stHeader"] span,
[data-testid="stHeader"] [data-testid="stIconMaterial"], [data-testid="stToolbar"] *,
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {
    color: var(--ar-header-muted) !important;
}
.ar-num { font-variant-numeric: tabular-nums; }
.ar-ico { width: 18px; height: 18px; flex: none; }

/* bordered st.container() -> card (used across every tab) */
[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stVerticalBlock"][data-test-border="true"] {
    background: var(--ar-surface) !important;
    border: 1px solid var(--ar-border) !important;
    border-radius: 12px !important;
    box-shadow: var(--ar-shadow);
}

/* ---------- premium header band ---------- */
.ar-topbar {
    position: relative;
    overflow: hidden;
    margin: -1rem -2rem 18px -2rem;
    padding: 18px 2rem 16px 2rem;
    min-height: 108px;
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 24px;
    background-color: var(--ar-header-bg);
    background-image: linear-gradient(100deg, var(--ar-header-bg) 0%, var(--ar-header-bg) 52%, var(--ar-header-bg-2) 100%);
}
.ar-topbar-brand { position: relative; z-index: 1; }
.ar-topbar-eyebrow {
    color: var(--ar-header-muted) !important;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
}
.ar-topbar-eyebrow b { color: var(--ar-header-accent) !important; font-weight: 800; }
.ar-topbar-title {
    color: var(--ar-header-text) !important;
    font-size: 1.85rem;
    font-weight: 750;
    letter-spacing: -0.02em;
    line-height: 1.15;
    margin: 2px 0 6px;
}
.ar-topbar-flow { display: flex; gap: 10px; align-items: center; }
.ar-topbar-flow span {
    color: var(--ar-header-muted) !important;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.12em;
}
.ar-topbar-flow i { color: var(--ar-header-accent) !important; font-style: normal; font-size: 0.6rem; }
.ar-topbar-account { position: relative; z-index: 1; text-align: right; padding-right: 4px; }
.ar-identity { display: inline-flex; align-items: center; gap: 10px; text-align: left; max-width: 360px; }
.ar-identity-text { min-width: 0; }
.ar-identity .ar-topbar-account-name { font-size: 0.95rem; line-height: 1.2; }
.ar-identity .ar-topbar-account-sub { overflow-wrap: anywhere; }
.ar-identity-logo {
    flex: 0 0 36px;
    width: 36px;
    height: 36px;
    border-radius: 9px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    border: 1px solid color-mix(in srgb, var(--ar-header-text) 22%, transparent);
    background: color-mix(in srgb, var(--ar-header-text) 12%, transparent);
    color: var(--ar-header-text) !important;
    font-size: 0.8rem;
    font-weight: 800;
    letter-spacing: 0.04em;
}
.ar-identity-logo.has-image { background: var(--ar-surface); }
.ar-identity-logo img { width: 100%; height: 100%; object-fit: contain; padding: 3px; display: block; }
.ar-identity-more { cursor: help; text-decoration: underline dotted; text-underline-offset: 2px; }
.ar-topbar-account-name { color: var(--ar-header-text) !important; font-size: 1rem; font-weight: 750; }
.ar-topbar-account-sub { color: var(--ar-header-muted) !important; font-size: 0.78rem; margin-top: 2px; }
.ar-topbar-art {
    position: absolute;
    right: -40px;
    top: -70px;
    width: 420px;
    height: 260px;
    color: var(--ar-header-accent);
    pointer-events: none;
    z-index: 0;
}

/* ---------- page / section headers ---------- */
.ar-page { margin: 6px 0 14px; }
.ar-page-title {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--ar-text) !important;
    font-size: 1.35rem;
    font-weight: 800;
    letter-spacing: 0.01em;
}
.ar-page-title .ar-ico { width: 20px; height: 20px; color: var(--ar-primary-text); }
.ar-page-question { color: var(--ar-primary-text) !important; font-size: 0.86rem; font-weight: 700; margin-top: 2px; }
.ar-page-sub { color: var(--ar-text-secondary) !important; font-size: 0.86rem; margin-top: 2px; }
.ar-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin: 4px 0 10px; }
.ar-section-title { color: var(--ar-text) !important; font-size: 1.05rem; font-weight: 800; }
.ar-section-sub { color: var(--ar-muted) !important; font-size: 0.82rem; }
.stApp h3 { font-size: 1.15rem !important; }
.stApp h4 { font-size: 0.98rem !important; }

/* ---------- panels ---------- */
.ar-panel {
    background: var(--ar-surface);
    border: 1px solid var(--ar-border);
    border-radius: 12px;
    padding: 14px 16px;
    box-shadow: var(--ar-shadow);
    margin-bottom: 12px;
}
.ar-panel-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.ar-panel-title {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--ar-text) !important;
    font-size: 0.92rem;
    font-weight: 800;
}
.ar-panel-title .ar-ico { color: var(--ar-primary-text); width: 16px; height: 16px; }
.ar-panel-meta { color: var(--ar-muted) !important; font-size: 0.78rem; }
.ar-panel-lead { color: var(--ar-text-secondary) !important; font-size: 0.86rem; line-height: 1.5; margin: -2px 0 6px; }

/* ---------- account identity ---------- */
.ar-acct { margin: 0 0 14px; }
.ar-acct-name { color: var(--ar-text) !important; font-size: 1.55rem; font-weight: 800; letter-spacing: -0.015em; line-height: 1.2; }
.ar-acct-sub {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 10px;
    margin-top: 4px;
    color: var(--ar-text-secondary) !important;
    font-size: 0.86rem;
}
.ar-acct-sub > span { color: var(--ar-text-secondary) !important; }
.ar-acct-sub a { color: var(--ar-primary-text) !important; }
.ar-dot { color: var(--ar-muted) !important; }
.ar-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 9px;
    border-radius: 6px;
    border: 1px solid var(--ar-border);
    background: var(--ar-surface-2);
    color: var(--ar-text-secondary) !important;
    font-size: 0.74rem;
    font-weight: 700;
    white-space: nowrap;
}
.ar-chip::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--ar-muted); }
.ar-chip.is-ok { color: var(--ar-success) !important; }
.ar-chip.is-ok::before { background: var(--ar-success); }
.ar-chip.is-warn { color: var(--ar-warning) !important; }
.ar-chip.is-warn::before { background: var(--ar-warning); }
.ar-chip.is-accent { color: var(--ar-primary-text) !important; background: var(--ar-primary-soft); border-color: var(--ar-primary); }
.ar-chip.is-accent::before { background: var(--ar-primary); }
.ar-chip.is-plain::before { display: none; }

/* ---------- semantic accents ---------- */
.k-blue { --k: var(--ar-blue); --k-soft: var(--ar-blue-soft); }
.k-green { --k: var(--ar-green); --k-soft: var(--ar-green-soft); }
.k-amber { --k: var(--ar-amber); --k-soft: var(--ar-amber-soft); }
.k-red { --k: var(--ar-red); --k-soft: var(--ar-red-soft); }
.k-purple { --k: var(--ar-purple); --k-soft: var(--ar-purple-soft); }
.k-slate { --k: var(--ar-slate); --k-soft: var(--ar-slate-soft); }

/* ---------- KPI cards (six across) ---------- */
.ar-kpis { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.ar-kpi {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    background: var(--k-soft, var(--ar-surface));
    border: 1px solid var(--ar-border);
    border-radius: 10px;
    padding: 12px 14px;
    box-shadow: var(--ar-shadow);
}
.ar-kpi.is-emph { border-color: var(--k); }
.ar-kpi-icon {
    flex: none;
    width: 38px;
    height: 38px;
    border-radius: 9px;
    display: grid;
    place-items: center;
    color: var(--k, var(--ar-primary-text));
    background: var(--ar-surface);
    border: 1px solid var(--ar-border);
}
.ar-kpi-icon .ar-ico { width: 20px; height: 20px; }
.ar-kpi-body { min-width: 0; }
.ar-kpi-label { color: var(--ar-text-secondary) !important; font-size: 0.78rem; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ar-kpi-value {
    color: var(--ar-text) !important;
    font-size: 1.5rem;
    font-weight: 800;
    line-height: 1.15;
    font-variant-numeric: tabular-nums;
}
.ar-kpi.is-empty .ar-kpi-value { color: var(--ar-muted) !important; }
.ar-kpi-sub { color: var(--ar-muted) !important; font-size: 0.72rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ar-priority {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 6px;
    border: 1px solid var(--ar-border);
    font-size: 0.74rem;
    font-weight: 800;
    letter-spacing: 0.06em;
    color: var(--ar-text-secondary) !important;
    background: var(--ar-surface-2);
}
.ar-priority.is-high { color: var(--ar-red) !important; background: var(--ar-red-soft); border-color: var(--ar-red); }
.ar-priority.is-medium { color: var(--ar-amber) !important; background: var(--ar-amber-soft); border-color: var(--ar-amber); }

/* ---------- category cards (People) ---------- */
.ar-cats { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; margin: 4px 0 14px; }
.ar-cat {
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--k-soft);
    border: 1px solid var(--ar-border);
    border-radius: 10px;
    padding: 12px 14px;
}
.ar-cat .ar-kpi-icon { width: 34px; height: 34px; }
.ar-cat-label { color: var(--ar-text-secondary) !important; font-size: 0.78rem; font-weight: 700; }
.ar-cat-value { color: var(--ar-text) !important; font-size: 1.35rem; font-weight: 800; line-height: 1.15; }

/* ---------- DECIDE v2 ---------- */
.ar-decision { border-left: 4px solid var(--ar-primary); }
.ar-decision-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin: 2px 0 8px; }
.ar-decision-state {
    display: inline-flex;
    align-items: center;
    padding: 5px 14px;
    border-radius: 999px;
    border: 1.5px solid var(--ar-border);
    font-size: 0.82rem;
    font-weight: 900;
    letter-spacing: 0.1em;
    white-space: nowrap;
    color: var(--ar-text) !important;
}
.ar-decision-state.is-pursue { color: var(--ar-success) !important; border-color: var(--ar-success); }
.ar-decision-state.is-validate { color: var(--ar-primary-text) !important; border-color: var(--ar-primary); background: var(--ar-primary-soft); }
.ar-decision-state.is-monitor { color: var(--ar-warning) !important; border-color: var(--ar-warning); }
.ar-decision-state.is-none { color: var(--ar-muted) !important; }
.ar-decision-headline { flex: 1 1 320px; color: var(--ar-text) !important; font-size: 0.98rem; font-weight: 700; line-height: 1.5; }
.ar-decision-confidence { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ar-u-tag.is-high { color: var(--ar-success) !important; border-color: var(--ar-success); }
.ar-u-tag.is-medium { color: var(--ar-primary-text) !important; border-color: var(--ar-primary); }
.ar-u-tag.is-low { color: var(--ar-warning) !important; border-color: var(--ar-warning); }
.ar-d-list { list-style: none; margin: 0 !important; padding: 0 !important; }
.ar-d-list li { margin-left: 0 !important; padding-left: 0 !important; }
.ar-d-fact { padding: 7px 0; border-bottom: 1px solid var(--ar-border); }
.ar-d-fact:last-child { border-bottom: none; }
.ar-d-fact-text { color: var(--ar-text) !important; font-size: 0.86rem; line-height: 1.45; }
.ar-d-fact .ar-u-src a { color: var(--ar-primary-text) !important; }
.ar-d-validate { border-color: var(--ar-primary); background: color-mix(in srgb, var(--ar-primary-soft) 55%, var(--ar-surface)); }
.ar-d-questions { margin: 0; padding-left: 20px; }
.ar-d-questions li { color: var(--ar-text) !important; font-size: 0.92rem; font-weight: 600; line-height: 1.5; margin-bottom: 6px; }
.ar-d-action .ar-decision-headline { font-size: 0.92rem; }
/* ---------- ACT v2 ---------- */
.ar-act-status { color: var(--ar-text) !important; font-size: 0.86rem; font-weight: 800; letter-spacing: 0.02em; }
.st-key-ar_act_controls { gap: 8px !important; flex-wrap: wrap; margin: -4px 0 12px; }

/* ---------- Live Demo Mode (ui/demo_mode.py; demo/demo_app.py) ---------- */
.ar-demo-banner { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 0 0 10px; padding: 7px 14px;
    border: 1px solid var(--ar-border); border-radius: 10px; background: var(--ar-surface); }
.ar-demo-live { display: inline-flex; align-items: center; gap: 7px; color: var(--ar-danger) !important; font-size: 0.72rem;
    font-weight: 800; letter-spacing: 0.12em; }
.ar-demo-live::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--ar-danger);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--ar-danger) 22%, transparent); }
.ar-demo-banner-text { color: var(--ar-text-secondary) !important; font-size: 0.8rem; }
.ar-demo-status { margin: 0 0 12px; padding: 10px 14px; border: 1px solid var(--ar-border); border-radius: 12px; background: var(--ar-surface); }
.ar-demo-status-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
.ar-demo-facts { display: grid; grid-template-columns: 1.3fr 1fr 1.6fr 1fr 1.3fr; gap: 14px; }
.ar-demo-fact-label { color: var(--ar-muted) !important; font-size: 0.68rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
.ar-demo-fact-value { color: var(--ar-text) !important; font-size: 0.92rem; font-weight: 800; font-variant-numeric: tabular-nums; }
.ar-demo-fail { border-color: var(--ar-danger); }
.ar-demo-fail-title { color: var(--ar-text) !important; font-size: 1rem; font-weight: 800; margin-bottom: 6px; }
.ar-demo-fail-text { color: var(--ar-text-secondary) !important; font-size: 0.88rem; line-height: 1.5; }
.ar-demo-fail-meta { color: var(--ar-muted) !important; font-size: 0.78rem; margin-top: 8px; }
@media (max-width: 900px) { .ar-demo-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

/* ---------- interactive cards (top KPIs, PEOPLE groups) ----------
   Each card is a keyed Streamlit container holding the card markup plus a
   real button stretched transparently over it, so the whole card is the
   control while it looks exactly like the static card. */
.st-key-ar_kpi_grid, .st-key-ar_people_buckets {
    display: grid !important;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 12px !important;
    row-gap: 12px !important;
    column-gap: 12px !important;
}
.st-key-ar_kpi_grid { margin-bottom: 16px; }
.st-key-ar_people_buckets { margin: 4px 0 14px; }
[class*="st-key-ar_kpi_nav_"], [class*="st-key-ar_people_bucket_"] { position: relative; gap: 0 !important; min-width: 0; }
[class*="st-key-ar_kpi_nav_"] .ar-kpi, [class*="st-key-ar_people_bucket_"] .ar-cat { height: 100%; transition: border-color 0.15s ease, transform 0.15s ease; }
[class*="st-key-ar_kpi_nav_"]:hover .ar-kpi, [class*="st-key-ar_people_bucket_"]:hover .ar-cat {
    border-color: var(--k, var(--ar-primary));
    transform: translateY(-1px);
}
/* Streamlit gives markdown a -1rem bottom margin; inside a grid card that
   makes the card overflow its row, so it is neutralised here. */
[class*="st-key-ar_kpi_nav_"] [data-testid="stMarkdownContainer"],
[class*="st-key-ar_people_bucket_"] [data-testid="stMarkdownContainer"] { height: 100%; margin-bottom: 0 !important; }
[class*="st-key-kpi_nav_"], [class*="st-key-people_bucket_"] {
    position: absolute !important;
    inset: 0;
    z-index: 2;
    margin: 0 !important;
    width: 100% !important;
    height: 100% !important;
}
[class*="st-key-kpi_nav_"] [data-testid="stButton"], [class*="st-key-people_bucket_"] [data-testid="stButton"] {
    width: 100% !important;
    height: 100% !important;
}
[class*="st-key-kpi_nav_"] button, [class*="st-key-people_bucket_"] button {
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
    opacity: 0;
    cursor: pointer;
    border: none !important;
    background: transparent !important;
}
[class*="st-key-kpi_nav_"] button:focus-visible, [class*="st-key-people_bucket_"] button:focus-visible {
    opacity: 1;
    color: transparent !important;
    outline: 2px solid var(--ar-primary) !important;
    outline-offset: 2px;
    border-radius: 10px;
}
.ar-cat.is-active {
    border-color: var(--k, var(--ar-primary));
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--k, var(--ar-primary)) 35%, transparent);
}
.ar-cat.is-active .ar-cat-label { color: var(--ar-text) !important; }

/* PEOPLE: active filters, filter chips, selected card */
.st-key-ar_people_filterbar { gap: 10px !important; flex-wrap: wrap; margin: 2px 0 8px; }
.ar-active-filters { display: flex; flex-wrap: wrap; gap: 6px; }
[class*="st-key-ar_person_chips_"] { flex-wrap: wrap; gap: 6px !important; margin: 2px 0 4px; }
[class*="st-key-ar_person_chips_"] button {
    min-height: 0 !important;
    padding: 1px 10px !important;
    border-radius: 999px !important;
    font-size: 0.74rem !important;
    line-height: 1.6 !important;
}
[class*="st-key-ar_person_selected_"] {
    border-color: var(--ar-primary) !important;
    box-shadow: 0 0 0 1px var(--ar-primary), var(--ar-shadow);
}

/* ---------- tabs = primary navigation ---------- */
[data-testid="stTabs"] [role="tablist"], [data-baseweb="tab-list"] { gap: 2px !important; margin-bottom: 6px; }
[data-testid="stTab"], [data-baseweb="tab"] {
    padding: 9px 16px !important;
    border-radius: 8px 8px 0 0;
    font-size: 0.8rem !important;
    letter-spacing: 0.05em !important;
}
[data-testid="stTab"][aria-selected="true"], [data-baseweb="tab"][aria-selected="true"] {
    background: var(--ar-primary-soft) !important;
    font-weight: 800 !important;
}

/* ---------- compact st.metric (DECIDE / RESEARCH) ---------- */
[data-testid="stMetric"] { min-height: 0 !important; padding: 10px 14px !important; border-radius: 10px !important; }
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * { font-size: 1.45rem !important; }

/* ---------- sidebar ---------- */
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3, [data-testid="stSidebar"] h4 {
    text-transform: none !important;
    letter-spacing: -0.005em !important;
    font-size: 1.02rem !important;
    font-weight: 800 !important;
}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
    color: var(--ar-muted) !important;
    font-size: 0.78rem !important;
    font-weight: 650 !important;
}
[data-testid="stSidebar"] hr { margin: 12px 0 !important; }
/* navigation list (sidebar radio keyed ar_nav) */
.st-key-ar_nav [data-testid="stRadioGroup"] { display: flex; flex-direction: column; gap: 2px; width: 100%; }
.st-key-ar_nav [data-testid="stRadioOption"] {
    width: 100%;
    padding: 8px 12px !important;
    border-radius: 8px;
    margin: 0 !important;
}
.st-key-ar_nav [data-testid="stRadioOption"] > div > div > div:first-child { display: none !important; }
.st-key-ar_nav [data-testid="stRadioOption"] p {
    color: var(--ar-text-secondary) !important;
    font-size: 0.9rem !important;
    font-weight: 600 !important;
}
.st-key-ar_nav [data-testid="stRadioOption"] [data-testid="stIconMaterial"] {
    color: var(--ar-muted) !important;
    margin-right: 10px;
    font-size: 1.15rem !important;
    vertical-align: -3px;
}
.st-key-ar_nav [data-testid="stRadioOption"]:hover { background: var(--ar-surface-2); }
.st-key-ar_nav [data-testid="stRadioOption"][data-selected="true"] { background: var(--ar-primary-soft) !important; }
.st-key-ar_nav [data-testid="stRadioOption"][data-selected="true"] p,
.st-key-ar_nav [data-testid="stRadioOption"][data-selected="true"] [data-testid="stIconMaterial"] {
    color: var(--ar-primary-text) !important;
    font-weight: 800 !important;
}

/* ---------- signal landscape ---------- */
.ar-dist-row {
    display: grid;
    grid-template-columns: minmax(130px, 180px) 1fr 44px;
    align-items: center;
    gap: 12px;
    padding: 5px 0;
}
.ar-dist-label { display: flex; align-items: center; gap: 8px; color: var(--ar-text-secondary) !important; font-size: 0.84rem; font-weight: 650; }
.ar-dist-label::before { content: ""; width: 8px; height: 8px; border-radius: 2px; background: var(--k, var(--ar-primary)); flex: none; }
.ar-dist-track { height: 8px; border-radius: 999px; background: var(--ar-surface-2); overflow: hidden; }
.ar-dist-fill { height: 100%; border-radius: 999px; background: var(--k, var(--ar-primary)); }
.ar-dist-count { color: var(--ar-text) !important; font-weight: 800; text-align: right; font-variant-numeric: tabular-nums; }

/* ---------- type badge ---------- */
.ar-badge {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 5px;
    font-size: 0.68rem;
    font-weight: 800;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    white-space: nowrap;
    color: var(--k, var(--ar-primary-text)) !important;
    background: var(--k-soft, var(--ar-primary-soft));
}

/* ---------- event list ---------- */
.ar-event { display: grid; grid-template-columns: 150px 1fr auto; gap: 12px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--ar-border); }
.ar-event:last-child { border-bottom: none; }
.ar-event-text { color: var(--ar-text) !important; font-size: 0.86rem; font-weight: 600; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ar-event-src { color: var(--ar-muted) !important; font-size: 0.74rem; margin-top: 1px; }

/* ---------- strongest opportunity ---------- */
.ar-highlight-name { color: var(--ar-text) !important; font-size: 1.05rem; font-weight: 800; line-height: 1.3; margin: 0 0 8px; }
.ar-highlight-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.ar-highlight-text {
    color: var(--ar-text-secondary) !important;
    font-size: 0.85rem;
    line-height: 1.55;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
}
.ar-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
.ar-fact { background: var(--ar-surface-2); border: 1px solid var(--ar-border); border-radius: 8px; padding: 7px 10px; }
.ar-fact-label { color: var(--ar-muted) !important; font-size: 0.68rem; font-weight: 800; letter-spacing: 0.06em; text-transform: uppercase; }
.ar-fact-value { color: var(--ar-text) !important; font-size: 0.98rem; font-weight: 800; font-variant-numeric: tabular-nums; margin-top: 1px; }
.ar-next { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.ar-next-item { border: 1px solid var(--ar-border); border-radius: 8px; padding: 9px 12px; background: var(--ar-surface-2); }
.ar-next-item.is-primary { border-color: var(--ar-primary); background: var(--ar-primary-soft); }
.ar-next-step { color: var(--ar-primary-text) !important; font-size: 0.68rem; font-weight: 800; letter-spacing: 0.08em; }
.ar-next-title { color: var(--ar-text) !important; font-size: 0.88rem; font-weight: 800; margin-top: 2px; }
.ar-next-text { color: var(--ar-text-secondary) !important; font-size: 0.78rem; margin-top: 2px; }

/* ---------- evidence grid ---------- */
.ar-evidence-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.ar-evidence-grid .ar-evidence-card { margin: 0; display: flex; flex-direction: column; border-left-width: 1px; border-radius: 10px; }
.ar-evidence-grid .ar-evidence-text { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.ar-evidence-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: auto; padding-top: 8px; }
.ar-evidence-link { color: var(--ar-primary-text) !important; font-size: 0.78rem; font-weight: 800; text-decoration: none !important; white-space: nowrap; }
.ar-evidence-link:hover { text-decoration: underline !important; }

/* ---------- evidence-strength legend (UNDERSTAND) ---------- */
.ar-legend { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 0 0 14px; }
.ar-legend-item { display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; align-items: center; border: 1px solid var(--ar-border); border-radius: 10px; padding: 10px 12px; background: var(--ar-surface); }
.ar-legend-text { grid-column: 1 / -1; color: var(--ar-text-secondary) !important; font-size: 0.8rem; line-height: 1.45; }
.ar-legend-status { color: var(--ar-muted) !important; font-size: 0.74rem; font-weight: 700; }

/* ---------- People cards ---------- */
.ar-person-head { display: grid; grid-template-columns: 52px 1fr; gap: 14px; align-items: start; }
.ar-avatar {
    width: 52px;
    height: 52px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    background: var(--ar-primary-soft);
    color: var(--ar-primary-text) !important;
    font-weight: 800;
    font-size: 1.05rem;
    border: 1px solid var(--ar-border);
}
.ar-person-name { color: var(--ar-text) !important; font-size: 1.1rem; font-weight: 800; line-height: 1.25; }
.ar-person-title { color: var(--ar-text-secondary) !important; font-size: 0.86rem; margin-top: 1px; }
.ar-tags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
.ar-tag {
    font-family: ui-monospace, "SFMono-Regular", "Cascadia Mono", Consolas, monospace;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 2px 7px;
    border-radius: 4px;
    color: var(--k, var(--ar-text-secondary)) !important;
    background: var(--k-soft, var(--ar-surface-2));
    border: 1px solid var(--ar-border);
}
.ar-block-title { display: flex; align-items: center; gap: 8px; color: var(--ar-text) !important; font-size: 0.88rem; font-weight: 800; margin: 14px 0 4px; }
.ar-block-title .ar-ico { width: 16px; height: 16px; color: var(--ar-primary-text); }
.ar-block-text { color: var(--ar-text-secondary) !important; font-size: 0.85rem; line-height: 1.5; }
.ar-person-scores { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; margin-top: 12px; }
.ar-score-box { border: 1px solid var(--ar-border); border-left: 3px solid var(--ar-primary); background: var(--ar-primary-soft); border-radius: 8px; padding: 8px 12px; }
.ar-score-box.is-plain { border-left-color: var(--ar-border); background: var(--ar-surface); }
.ar-score-label { color: var(--ar-text-secondary) !important; font-size: 0.76rem; font-weight: 700; }
.ar-score-value { color: var(--ar-primary-text) !important; font-size: 1.35rem; font-weight: 800; line-height: 1.2; font-variant-numeric: tabular-nums; }
.ar-score-value.is-ok { color: var(--ar-success) !important; }
.ar-score-value.is-warn { color: var(--ar-amber) !important; }
.ar-score-value.is-muted { color: var(--ar-text-secondary) !important; }
.ar-factors { color: var(--ar-muted) !important; font-size: 0.74rem; margin-top: 6px; }
.ar-dl { display: grid; grid-template-columns: auto 1fr; gap: 3px 10px; font-size: 0.84rem; }
.ar-dl dt { color: var(--ar-text) !important; font-weight: 700; }
.ar-dl dd { color: var(--ar-text-secondary) !important; margin: 0; }
.ar-links { margin: 2px 0 0; padding-left: 18px; }
.ar-links li { color: var(--ar-text-secondary) !important; font-size: 0.82rem; line-height: 1.5; }
.ar-side-box { background: var(--ar-surface-2); border: 1px solid var(--ar-border); border-radius: 10px; padding: 12px 14px; height: 100%; }
.ar-side-box ul { margin: 4px 0 0; padding-left: 18px; }
.ar-side-box li { color: var(--ar-text-secondary) !important; font-size: 0.84rem; line-height: 1.5; }
[class*="st-key-ar_outreach_"] {
    background: var(--ar-primary-soft) !important;
    border: 1px solid var(--ar-border) !important;
    border-radius: 10px !important;
    padding: 10px 14px !important;
    margin-top: 8px;
}
.ar-outreach-title { display: flex; align-items: center; gap: 8px; color: var(--ar-primary-text) !important; font-size: 0.95rem; font-weight: 800; min-height: 40px; }
.ar-outreach-title .ar-ico { width: 18px; height: 18px; }

/* ---------- empty states ---------- */
.ar-empty { display: grid; grid-template-columns: 40px 1fr; gap: 12px; align-items: start; }
.ar-empty-icon {
    width: 40px;
    height: 40px;
    border-radius: 10px;
    display: grid;
    place-items: center;
    background: var(--ar-primary-soft);
    border: 1px solid var(--ar-border);
    color: var(--ar-primary-text) !important;
}
.ar-empty-title { color: var(--ar-text) !important; font-size: 0.98rem; font-weight: 800; }
.ar-empty-text { color: var(--ar-text-secondary) !important; font-size: 0.85rem; line-height: 1.5; margin-top: 2px; }
.ar-howto { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
.ar-howto-item { border: 1px solid var(--ar-border); border-radius: 8px; padding: 9px 12px; background: var(--ar-surface-2); }
.ar-howto-num { color: var(--ar-primary-text) !important; font-size: 0.68rem; font-weight: 800; letter-spacing: 0.08em; }
.ar-howto-text { color: var(--ar-text-secondary) !important; font-size: 0.8rem; margin-top: 2px; line-height: 1.45; }

/* start card: the main-area prompt when no account is loaded */
.ar-panel.ar-start {
    display: flex;
    align-items: flex-start;
    gap: 16px;
    padding: 18px 20px;
    margin-bottom: 14px;
    background-color: var(--ar-surface);
    background-image: linear-gradient(110deg, color-mix(in srgb, var(--ar-primary-soft) 85%, transparent) 0%, transparent 62%);
    border-left: 3px solid var(--ar-primary);
}
.ar-start-icon {
    flex: 0 0 auto;
    width: 46px;
    height: 46px;
    border-radius: 12px;
    display: grid;
    place-items: center;
    background: var(--ar-surface);
    border: 1px solid var(--ar-border);
    box-shadow: var(--ar-shadow);
    color: var(--ar-primary-text) !important;
}
.ar-start-icon .ar-ico { width: 22px; height: 22px; }
.ar-start-body { flex: 1 1 auto; min-width: 0; }
.ar-start-title { color: var(--ar-text) !important; font-size: 1.08rem; font-weight: 800; letter-spacing: -0.01em; }
.ar-start-text { color: var(--ar-text-secondary) !important; font-size: 0.88rem; line-height: 1.5; margin-top: 2px; }
.ar-start-flow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.ar-start-step {
    display: inline-flex;
    align-items: center;
    padding: 3px 11px;
    border-radius: 999px;
    border: 1px solid var(--ar-border);
    background: color-mix(in srgb, var(--ar-surface) 70%, transparent);
    color: var(--ar-text-secondary) !important;
    font-size: 0.74rem;
    font-weight: 700;
    white-space: nowrap;
}
.ar-start-sep { color: var(--ar-muted) !important; font-size: 0.8rem; }
.ar-empty.ar-empty-center { padding: 18px 4px 14px; }

/* ---------- executive account summary ---------- */
.ar-exec {
    margin: 0 0 18px;
    padding: 16px 16px 4px;
    border: 1px solid var(--ar-border);
    border-radius: 14px;
    background-color: var(--ar-surface);
    background-image: linear-gradient(135deg, color-mix(in srgb, var(--ar-primary-soft) 70%, transparent) 0%, transparent 55%);
    box-shadow: var(--ar-shadow);
}
.ar-exec-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.ar-exec-title { display: flex; align-items: center; gap: 8px; color: var(--ar-text) !important; font-size: 1.05rem; font-weight: 800; }
.ar-exec-title .ar-ico { width: 18px; height: 18px; color: var(--ar-primary-text); }
.ar-exec-meta { color: var(--ar-muted) !important; font-size: 0.76rem; }
.ar-exec-label {
    color: var(--ar-primary-text) !important;
    font-size: 0.68rem;
    font-weight: 800;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    margin: 2px 0 8px;
}
.ar-pulse { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.ar-pulse-item {
    border: 1px solid var(--ar-border);
    border-radius: 12px;
    padding: 10px 14px;
    background: color-mix(in srgb, var(--ar-surface) 72%, transparent);
    backdrop-filter: blur(10px);
}
.ar-pulse-value { color: var(--ar-text) !important; font-size: 1.45rem; font-weight: 800; line-height: 1.1; font-variant-numeric: tabular-nums; }
.ar-pulse-label { color: var(--ar-text) !important; font-size: 0.8rem; font-weight: 800; margin-top: 2px; }
.ar-pulse-sub { color: var(--ar-muted) !important; font-size: 0.72rem; }
.ar-exec-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 12px; }
.ar-exec-grid.is-wide { grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); }
.ar-exec-panel { background: color-mix(in srgb, var(--ar-surface) 82%, transparent); }
.ar-exec-summary p { color: var(--ar-text) !important; font-size: 0.9rem; line-height: 1.55; margin: 0 0 6px; }
.ar-exec-summary p:last-child { margin-bottom: 0; }
.ar-convo-quote {
    color: var(--ar-text) !important;
    font-size: 0.95rem;
    font-weight: 700;
    line-height: 1.5;
    border-left: 3px solid var(--ar-primary);
    padding: 4px 0 4px 12px;
}
.ar-convo-basis { color: var(--ar-text-secondary) !important; font-size: 0.78rem; margin-top: 8px; }
.ar-exec-empty { color: var(--ar-text-secondary) !important; font-size: 0.84rem; padding: 6px 0; }
.ar-engage { display: grid; grid-template-columns: 40px 1fr auto; gap: 12px; align-items: start; padding: 9px 0; border-bottom: 1px solid var(--ar-border); }
.ar-engage:last-child { border-bottom: none; }
.ar-avatar.ar-avatar-sm { width: 40px; height: 40px; font-size: 0.86rem; }
.ar-engage-name { color: var(--ar-text) !important; font-size: 0.92rem; font-weight: 800; }
.ar-engage-title { color: var(--ar-text-secondary) !important; font-size: 0.8rem; }
.ar-engage-why { color: var(--ar-text-secondary) !important; font-size: 0.78rem; line-height: 1.45; margin-top: 4px; }
.ar-opp-cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.ar-opp {
    border: 1px solid var(--ar-border);
    border-radius: 10px;
    padding: 10px 12px;
    margin-bottom: 10px;
    background: var(--ar-surface-2);
}
.ar-opp.is-confirmed { border-color: var(--ar-success); }
.ar-opp-head { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
.ar-opp-name { color: var(--ar-text) !important; font-size: 0.95rem; font-weight: 800; }
.ar-opp-conf { color: var(--ar-muted) !important; font-size: 0.72rem; white-space: nowrap; }
.ar-opp-conf span { color: var(--ar-text) !important; font-size: 1rem; font-weight: 800; margin-right: 4px; font-variant-numeric: tabular-nums; }
.ar-opp-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.ar-opp-why { color: var(--ar-text-secondary) !important; font-size: 0.8rem; line-height: 1.5; }
.ar-opp-why strong { color: var(--ar-text) !important; }
.ar-opp-evidence { color: var(--ar-text) !important; font-size: 0.8rem; line-height: 1.5; font-style: italic; margin-top: 6px; }
.ar-opp-note { color: var(--ar-warning) !important; font-size: 0.76rem; font-weight: 700; margin-top: 6px; }
.ar-opp-src { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 8px; color: var(--ar-muted) !important; font-size: 0.74rem; }
@media (max-width: 1180px) {
    .ar-exec-grid, .ar-exec-grid.is-wide, .ar-opp-cols { grid-template-columns: 1fr; }
    .ar-pulse { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

/* ---------- UNDERSTAND v2 ---------- */
.ar-u-section { padding-bottom: 10px; }
.ar-u-cols { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.ar-u-col { min-width: 0; }
.ar-u-block { margin-top: 12px; }
.ar-u-ai { border-top: 1px dashed var(--ar-border); padding-top: 10px; }
.ar-u-item {
    border: 1px solid var(--ar-border);
    border-radius: 10px;
    padding: 8px 10px;
    margin-bottom: 8px;
    background: var(--ar-surface-2);
}
.ar-u-item-head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin-bottom: 4px; }
.ar-u-strength { color: var(--ar-muted) !important; font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }
.ar-u-dims { display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 6px; }
.ar-u-dim {
    display: inline-flex;
    align-items: center;
    padding: 1px 8px;
    border-radius: 6px;
    border: 1px solid var(--ar-border);
    font-size: 0.66rem;
    font-weight: 800;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ar-text-secondary) !important;
}
.ar-u-dim.is-direct_evidence { color: var(--ar-success) !important; border-color: var(--ar-success); }
.ar-u-dim.is-strong_inference { color: var(--ar-primary-text) !important; border-color: var(--ar-primary); }
.ar-u-dim.is-weak_relevance, .ar-u-dim.is-no_capability_link { color: var(--ar-muted) !important; }
.ar-u-entity { color: var(--ar-text) !important; font-size: 0.9rem; font-weight: 800; }
.ar-u-excerpt { color: var(--ar-text) !important; font-size: 0.8rem; line-height: 1.5; font-style: italic; }
.ar-u-src { color: var(--ar-muted) !important; font-size: 0.72rem; margin-top: 4px; overflow-wrap: anywhere; }
.ar-u-src a { color: var(--ar-primary-text) !important; }
.ar-u-note { color: var(--ar-text-secondary) !important; font-size: 0.78rem; line-height: 1.45; }
.ar-u-posture { padding: 9px 0; border-bottom: 1px solid var(--ar-border); }
.ar-u-posture:last-child { border-bottom: none; }
.ar-u-posture-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.ar-u-cap { color: var(--ar-text) !important; font-size: 0.9rem; font-weight: 800; }
.ar-u-status, .ar-u-tag {
    display: inline-flex;
    align-items: center;
    padding: 2px 9px;
    border-radius: 999px;
    border: 1px solid var(--ar-border);
    font-size: 0.7rem;
    font-weight: 800;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    white-space: nowrap;
    color: var(--ar-text-secondary) !important;
    background: color-mix(in srgb, var(--ar-surface) 70%, transparent);
}
.ar-u-status.is-evidence, .ar-u-tag.is-observed { color: var(--ar-success) !important; border-color: var(--ar-success); }
.ar-u-status.is-monitor, .ar-u-tag.is-inferred { color: var(--ar-warning) !important; border-color: var(--ar-warning); }
.ar-u-status.is-context { color: var(--ar-primary-text) !important; border-color: var(--ar-primary); }
.ar-u-status.is-none, .ar-u-tag.is-unknown { color: var(--ar-muted) !important; }
.ar-u-counts { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 5px 0 3px; }
.ar-u-count { color: var(--ar-text-secondary) !important; font-size: 0.76rem; }
.ar-u-count b { color: var(--ar-text) !important; font-variant-numeric: tabular-nums; margin-right: 3px; }
.ar-u-person { padding: 7px 0; border-bottom: 1px solid var(--ar-border); }
.ar-u-person:last-child { border-bottom: none; }
.ar-u-person-name { color: var(--ar-text) !important; font-size: 0.88rem; font-weight: 800; }
.ar-u-person-name a { color: var(--ar-text) !important; text-decoration: none; }
.ar-u-person-title { color: var(--ar-text-secondary) !important; font-size: 0.8rem; }
.ar-u-person-meta { color: var(--ar-muted) !important; font-size: 0.72rem; margin: 2px 0; }
.ar-u-buy { display: grid; grid-template-columns: 190px 92px 1fr; gap: 10px; align-items: start; padding: 7px 0; border-bottom: 1px solid var(--ar-border); }
.ar-u-buy:last-child { border-bottom: none; }
.ar-u-buy-label { color: var(--ar-text) !important; font-size: 0.84rem; font-weight: 800; }
.ar-u-buy-detail { color: var(--ar-text-secondary) !important; font-size: 0.8rem; line-height: 1.45; }
.ar-u-buy-detail a { color: var(--ar-primary-text) !important; }
.ar-u-list { margin: 0; padding-left: 18px; }
.ar-u-list li { color: var(--ar-text) !important; font-size: 0.84rem; line-height: 1.5; margin-bottom: 4px; }
@media (max-width: 1180px) {
    .ar-u-cols { grid-template-columns: 1fr; }
    .ar-u-buy { grid-template-columns: 1fr; gap: 4px; }
}

/* ---------- responsive ---------- */
@media (max-width: 1500px) {
    .ar-kpis, .ar-cats { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .st-key-ar_kpi_grid, .st-key-ar_people_buckets { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 1180px) {
    .ar-evidence-grid, .ar-legend { grid-template-columns: 1fr; }
    .ar-topbar-art { opacity: 0.5; }
    .ar-event { grid-template-columns: 1fr; gap: 4px; }
}
@media (max-width: 760px) {
    .ar-kpis, .ar-cats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .st-key-ar_kpi_grid, .st-key-ar_people_buckets { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .ar-topbar { flex-direction: column; align-items: flex-start; }
    .ar-topbar-account { text-align: left; }
    .ar-next, .ar-howto, .ar-facts, .ar-person-scores { grid-template-columns: 1fr; }
}

/* =========================================================
   Glass pill buttons -- floating, translucent, blurred
   (placed after the base button rules so it refines them)
   ========================================================= */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-primaryFormSubmit"], [data-testid="stBaseButton-secondaryFormSubmit"],
[data-testid="stBaseLinkButton-secondary"], [data-testid="stBaseLinkButton-primary"] {
    border-radius: 999px !important;
    padding-left: 18px !important;
    padding-right: 18px !important;
    -webkit-backdrop-filter: blur(12px) saturate(160%);
    backdrop-filter: blur(12px) saturate(160%);
    transition: transform 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease;
}
/* secondary: frosted glass */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-secondaryFormSubmit"],
[data-testid="stBaseLinkButton-secondary"] {
    background: color-mix(in srgb, var(--ar-surface) 62%, transparent) !important;
    border: 1px solid color-mix(in srgb, var(--ar-border) 85%, transparent) !important;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.45), 0 4px 14px rgba(15, 23, 42, 0.08) !important;
}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover,
[data-testid="stBaseButton-secondary"]:hover, [data-testid="stBaseLinkButton-secondary"]:hover {
    background: color-mix(in srgb, var(--ar-primary-soft) 78%, transparent) !important;
    transform: translateY(-1px);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.5), 0 8px 22px rgba(15, 23, 42, 0.12) !important;
}
/* primary: tinted glass -- kept ~90% opaque so white text stays >= 4.5:1 */
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"],
.stButton > button[data-testid="stBaseButton-primary"],
.stFormSubmitButton > button[data-testid="stBaseButton-primaryFormSubmit"],
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"],
[data-testid="stBaseLinkButton-primary"] {
    background-color: var(--ar-primary) !important;
    background-image: linear-gradient(135deg,
        color-mix(in srgb, var(--ar-primary) 94%, transparent) 0%,
        color-mix(in srgb, var(--ar-primary-hover) 90%, transparent) 100%) !important;
    border: 1px solid color-mix(in srgb, var(--ar-primary) 60%, rgba(255, 255, 255, 0.5)) !important;
    color: var(--ar-on-primary) !important;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.28),
                0 6px 18px color-mix(in srgb, var(--ar-primary) 32%, transparent) !important;
}
.stButton > button[kind="primary"]:hover,
.stButton > button[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {
    transform: translateY(-1px);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.32),
                0 10px 26px color-mix(in srgb, var(--ar-primary) 40%, transparent) !important;
}
.stButton > button:active, [data-testid="stBaseButton-primary"]:active { transform: translateY(0); }
@media (prefers-reduced-motion: reduce) {
    .stButton > button, [data-testid="stBaseButton-primary"] { transition: none; }
}

/* ---------- markdown inside controls inherits the control's colour ----------
   Streamlit renders button / tab / link-button labels through a markdown
   container; without this the body-text colour above would override the
   control's own (e.g. white-on-primary) text colour. */
button [data-testid="stMarkdownContainer"], button [data-testid="stMarkdownContainer"] p,
[role="tab"] [data-testid="stMarkdownContainer"], [role="tab"] [data-testid="stMarkdownContainer"] p,
a [data-testid="stMarkdownContainer"], a [data-testid="stMarkdownContainer"] p {
    color: inherit !important;
}

/* ---------- scrollbars ---------- */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: var(--ar-bg); }
::-webkit-scrollbar-thumb { background: var(--ar-border); border-radius: 8px; border: 2px solid var(--ar-bg); }
"""

def build_theme_css(palette: str, mode: str) -> str:
    tokens = get_theme_tokens(palette, mode)
    mode_key = "dark" if str(mode).lower() == "dark" else "light"
    return _css_variables(tokens, mode_key) + _BASE_CSS


def inject_theme(st: Any) -> None:
    """Paint the current palette/mode. Call once per run, early."""
    init_theme_state(st)
    palette, mode = current_selection(st)
    st.markdown(f"<style>\n{build_theme_css(palette, mode)}\n</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Contrast helpers (used by tests to guard readability)
# ---------------------------------------------------------------------------

def _relative_luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(foreground: str, background: str) -> float:
    l1, l2 = _relative_luminance(foreground), _relative_luminance(background)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)
