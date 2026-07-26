"""Design tokens and the QSS theme.

One palette dataclass drives everything: widgets never hard-code colors,
they reference palette fields (directly for dynamic bits like status colors)
or rely on the QSS built from it. Light and dark are the same layout with
different tokens, so the two stay in sync by construction.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    window: str          # app background
    surface: str         # cards, inputs, sidebar
    sunken: str          # log panel, code preview
    border: str
    text: str
    subtext: str
    accent: str          # indigo — matches the README branding
    accent_hover: str
    accent_text: str     # text on accent
    ok: str
    warn: str
    error: str
    running: str
    selection: str       # nav pill / list selection background


LIGHT = Palette(
    window="#f4f5f7",
    surface="#ffffff",
    sunken="#f8f9fb",
    border="#e3e6ea",
    text="#191c22",
    subtext="#69707d",
    accent="#4f46e5",
    accent_hover="#4338ca",
    accent_text="#ffffff",
    ok="#15803d",
    warn="#b45309",
    error="#dc2626",
    running="#2563eb",
    selection="#e8e9fd",
)

DARK = Palette(
    window="#141519",
    surface="#1e2026",
    sunken="#191b20",
    border="#2d3038",
    text="#e7e9ee",
    subtext="#98a0ac",
    accent="#818cf8",
    accent_hover="#93a0fa",
    accent_text="#15162e",
    ok="#4ade80",
    warn="#fbbf24",
    error="#f87171",
    running="#60a5fa",
    selection="#2a2d51",
)


def build_qss(p: Palette) -> str:
    return f"""
* {{
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {p.window};
    color: {p.text};
}}
QWidget {{
    color: {p.text};
}}
QLabel {{
    background: transparent;
}}
QLabel[role="subtext"] {{
    color: {p.subtext};
    font-size: 12px;
}}
QLabel[role="title"] {{
    font-size: 16px;
    font-weight: 600;
}}

/* ---- sidebar -------------------------------------------------------- */
QFrame#Sidebar {{
    background: {p.surface};
    border-right: 1px solid {p.border};
}}
QLabel#AppTitle {{
    font-size: 17px;
    font-weight: 700;
    padding: 2px 8px;
}}
QToolButton[nav="true"] {{
    border: none;
    border-radius: 8px;
    padding: 9px 14px;
    text-align: left;
    font-size: 13px;
    color: {p.subtext};
    background: transparent;
}}
QToolButton[nav="true"]:hover {{
    background: {p.selection};
    color: {p.text};
}}
QToolButton[nav="true"]:checked {{
    background: {p.selection};
    color: {p.accent};
    font-weight: 600;
}}

/* ---- cards & panels -------------------------------------------------- */
QFrame[card="true"] {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 10px;
}}
QFrame#BottomBar {{
    background: {p.surface};
    border-top: 1px solid {p.border};
}}

/* ---- buttons ---------------------------------------------------------- */
QPushButton {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    border-color: {p.accent};
    color: {p.accent};
}}
QPushButton:disabled {{
    color: {p.subtext};
    border-color: {p.border};
    background: transparent;
}}
QPushButton[variant="primary"] {{
    background: {p.accent};
    color: {p.accent_text};
    border: none;
    font-weight: 600;
    padding: 7px 18px;
}}
QPushButton[variant="primary"]:hover {{
    background: {p.accent_hover};
}}
QPushButton[variant="primary"]:disabled {{
    background: {p.border};
    color: {p.subtext};
}}
QPushButton[variant="danger"] {{
    background: {p.error};
    color: white;
    border: none;
    font-weight: 600;
    padding: 7px 18px;
}}
QPushButton[variant="quiet"] {{
    border: none;
    background: transparent;
    color: {p.accent};
    padding: 5px 8px;
}}
QPushButton[variant="quiet"]:hover {{
    background: {p.selection};
    color: {p.accent};
}}
QPushButton[variant="quiet"]:disabled {{
    background: transparent;
}}

/* ---- inputs ----------------------------------------------------------- */
QPlainTextEdit, QLineEdit, QTextEdit {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 5px 8px;
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
}}
QPlainTextEdit:focus, QLineEdit:focus, QTextEdit:focus {{
    border-color: {p.accent};
}}
QPlainTextEdit[role="log"], QPlainTextEdit[role="preview"] {{
    background: {p.sunken};
    font-family: Menlo, Consolas, monospace;
    font-size: 11px;
}}
QTextBrowser[role="preview"] {{
    background: {p.sunken};
    padding: 10px 12px;
}}
QComboBox {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 5px 10px;
    min-height: 20px;
}}
QComboBox:hover {{
    border-color: {p.accent};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 6px;
    selection-background-color: {p.selection};
    selection-color: {p.text};
    outline: none;
}}
/* Checkbox indicators stay native (Fusion + our QPalette): the style
   draws a real check mark, which custom QSS boxes cannot. */
QCheckBox {{
    spacing: 7px;
}}

/* ---- progress --------------------------------------------------------- */
QProgressBar {{
    background: {p.border};
    border: none;
    border-radius: 3px;
    max-height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {p.accent};
    border-radius: 3px;
}}

/* ---- scrollbars ------------------------------------------------------- */
QScrollArea {{
    border: none;
    background: transparent;
}}
/* Viewport and its host widget paint the platform default otherwise. */
QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.border};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p.subtext};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.border};
    border-radius: 4px;
    min-width: 30px;
}}

/* ---- toast ------------------------------------------------------------ */
QLabel#Toast {{
    color: white;
    font-weight: 600;
    padding: 9px 18px;
    border-radius: 9px;
}}
"""


def palette_for(mode: str, system_dark: bool) -> Palette:
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    return DARK if system_dark else LIGHT
