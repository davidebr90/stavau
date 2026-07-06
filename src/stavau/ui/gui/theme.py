"""Visual theme for the stavau GUI: brand palette + a Qt stylesheet builder.

Kept Qt-free on purpose — `build_stylesheet()` only assembles a string, so it
is trivially unit-testable and the widget code (app.py) just applies the
result. Two palettes (light/dark) share the stavau brand blue/green taken from
the project logo; app.py picks one from the OS colour scheme.
"""

from __future__ import annotations

from dataclasses import dataclass

# stavau brand accents (from the logo): a royal blue and a fresh green.
BRAND_BLUE = "#2f56d9"
BRAND_BLUE_DARK = "#2241ad"
BRAND_BLUE_BRIGHT = "#4f74ff"
BRAND_GREEN = "#35a94a"


@dataclass(frozen=True)
class Palette:
    name: str
    primary: str
    primary_hover: str
    window: str
    sidebar: str
    card: str
    surface: str
    surface_hover: str
    border: str
    text: str
    muted: str
    input_bg: str
    on_primary: str = "#ffffff"
    danger: str = "#cc3e34"
    danger_hover: str = "#b5342b"
    success: str = "#2e9e46"
    chosen_row: str = "rgba(53, 169, 74, 55)"


LIGHT = Palette(
    name="light",
    primary=BRAND_BLUE,
    primary_hover=BRAND_BLUE_DARK,
    window="#f4f6fb",
    sidebar="#ffffff",
    card="#ffffff",
    surface="#eef1f8",
    surface_hover="#e4e9f5",
    border="#dde3ee",
    text="#1a2233",
    muted="#6b7488",
    input_bg="#ffffff",
)

DARK = Palette(
    name="dark",
    primary=BRAND_BLUE_BRIGHT,
    primary_hover="#3a5ae0",
    window="#0f1420",
    sidebar="#151b2b",
    card="#1a2233",
    surface="#222c42",
    surface_hover="#2b3651",
    border="#2b3651",
    text="#e6eaf2",
    muted="#9aa4bd",
    input_bg="#141b29",
    danger="#e05a4f",
    danger_hover="#c94a40",
    success="#4fce6a",
    chosen_row="rgba(53, 169, 74, 70)",
)


def build_stylesheet(p: Palette) -> str:
    """Assemble the full application stylesheet for a palette."""
    return f"""
    QWidget {{
        font-family: 'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif;
        font-size: 13px;
        color: {p.text};
    }}
    QMainWindow, #Root {{ background: {p.window}; }}

    /* --- sidebar navigation --- */
    #Sidebar {{ background: {p.sidebar}; border-right: 1px solid {p.border}; }}
    #Wordmark {{ font-size: 22px; font-weight: 700; color: {p.primary}; padding: 4px 2px; }}
    #WordmarkTag {{ color: {p.muted}; font-size: 11px; padding: 0 2px 8px 2px; }}
    #NavButton {{
        text-align: left; padding: 11px 14px; border: none; border-radius: 10px;
        background: transparent; color: {p.muted}; font-weight: 600;
    }}
    #NavButton:hover {{ background: {p.surface}; color: {p.text}; }}
    #NavButton:checked {{ background: {p.primary}; color: {p.on_primary}; }}

    /* --- content + cards --- */
    #Content {{ background: {p.window}; }}
    #Card {{
        background: {p.card}; border: 1px solid {p.border}; border-radius: 14px;
    }}
    #PageTitle {{ font-size: 18px; font-weight: 700; color: {p.text}; }}
    #Muted, QLabel#Muted {{ color: {p.muted}; }}
    #Success, QLabel#Success {{ color: {p.success}; font-weight: 600; }}
    #Error, QLabel#Error {{ color: {p.danger}; font-weight: 600; }}
    #StatusHero {{ font-size: 15px; font-weight: 600; color: {p.text}; }}

    /* --- busy indicator (scan spinner) --- */
    QProgressBar {{
        background: {p.surface}; border: none; border-radius: 4px;
        max-height: 6px; min-height: 6px;
    }}
    QProgressBar::chunk {{ background: {p.primary}; border-radius: 4px; }}

    /* --- buttons --- */
    QPushButton {{
        background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px;
        padding: 8px 16px; color: {p.text}; font-weight: 600;
    }}
    QPushButton:hover {{ background: {p.surface_hover}; }}
    QPushButton:disabled {{ color: {p.muted}; background: {p.surface}; }}
    QPushButton#Primary {{ background: {p.primary}; color: {p.on_primary}; border: none; }}
    QPushButton#Primary:hover {{ background: {p.primary_hover}; }}
    QPushButton#Primary:disabled {{ background: {p.border}; color: {p.muted}; }}
    QPushButton#Danger {{ background: {p.danger}; color: {p.on_primary}; border: none; }}
    QPushButton#Danger:hover {{ background: {p.danger_hover}; }}

    /* --- inputs --- */
    QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QLineEdit {{
        background: {p.input_bg}; border: 1px solid {p.border}; border-radius: 8px;
        padding: 6px 10px; selection-background-color: {p.primary};
    }}
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QPlainTextEdit:focus, QLineEdit:focus {{ border: 1px solid {p.primary}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {p.card}; border: 1px solid {p.border};
        selection-background-color: {p.primary}; selection-color: {p.on_primary};
        outline: none;
    }}
    QPlainTextEdit {{ font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px; }}

    /* --- slider --- */
    QSlider::groove:horizontal {{
        height: 6px; background: {p.surface_hover}; border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{ background: {p.primary}; border-radius: 3px; }}
    QSlider::handle:horizontal {{
        background: {p.primary}; width: 18px; height: 18px; margin: -7px 0;
        border-radius: 9px; border: 2px solid {p.card};
    }}

    /* --- table --- */
    QTableWidget {{
        background: {p.card}; border: 1px solid {p.border}; border-radius: 10px;
        gridline-color: {p.border}; outline: none;
    }}
    QTableWidget::item {{ padding: 4px 6px; }}
    QTableWidget::item:selected {{ background: {p.primary}; color: {p.on_primary}; }}
    QHeaderView::section {{
        background: {p.surface}; padding: 8px; border: none;
        border-bottom: 1px solid {p.border}; font-weight: 700; color: {p.muted};
    }}
    QTableCornerButton::section {{ background: {p.surface}; border: none; }}

    /* --- scrollbars --- */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """
