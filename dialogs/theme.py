# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dialog colors and style sheet. Neutrals are mixed from the running QGIS palette, so light
and dark themes both work; only the brand blue is fixed."""

from dataclasses import dataclass
from typing import Optional

from qgis.PyQt.QtGui import QColor, QPalette  # type: ignore
from qgis.PyQt.QtWidgets import QApplication  # type: ignore

from . import _ui_helpers

# ACCENT is the text blue (6.6:1 on white), ACCENT_MARK and ACCENT_CYAN the logo gradient
ACCENT = "#0052D8"
ACCENT_MARK = "#0971E5"
ACCENT_CYAN = "#2CC8FD"
ON_ACCENT = "#FFFFFF"


@dataclass(frozen=True)
class Tokens:
    dark: bool
    accent: str        # section titles and links
    fill: str          # primary button
    fill_hover: str
    fill_pressed: str
    fill_disabled: str
    card: str          # footer note and segmented control
    border: str
    hairline: str
    text: str


def _mix(first: QColor, second: QColor, share: float) -> str:
    return QColor(round(first.red() + (second.red() - first.red()) * share),
                  round(first.green() + (second.green() - first.green()) * share),
                  round(first.blue() + (second.blue() - first.blue()) * share)).name()


def tokens() -> Tokens:
    app = QApplication.instance()
    palette = app.palette() if app is not None else QPalette()
    window = palette.color(QPalette.ColorRole.Window)
    base = palette.color(QPalette.ColorRole.Base)
    text = palette.color(QPalette.ColorRole.WindowText)
    dark = _ui_helpers.dark_ui()
    return Tokens(
        dark=dark,
        # the deep blue has too little contrast on a dark window
        accent=ACCENT_CYAN if dark else ACCENT,
        fill=ACCENT,
        fill_hover=ACCENT_MARK,
        fill_pressed="#0044B3",
        fill_disabled=_mix(QColor(ACCENT), window, 0.55),
        card=_mix(window, base, 0.65),
        border=_mix(window, text, 0.2),
        hairline=_mix(window, text, 0.16),
        text=text.name(),
    )


def colored_links(html_text: str, t: Optional[Tokens] = None) -> str:
    # dark themes often keep the palette's deep blue link color, unreadable on a dark window
    t = t or tokens()
    return html_text.replace("<a href=", f"<a style='color:{t.accent}' href=")


def dialog_stylesheet(t: Tokens) -> str:
    return f"""
        QGroupBox {{
            font-weight: 600;
            border: none;
            border-top: 1px solid {t.hairline};
            margin-top: 1.3em;
            padding-top: 0.55em;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 0px;
            padding: 0px 8px 0px 0px;
            color: {t.accent};
        }}
        QFrame#cadexBrandBar {{
            border: none;
            min-height: 3px;
            max-height: 3px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                        stop:0 {ACCENT_MARK}, stop:1 {ACCENT_CYAN});
        }}
        QFrame#cadexHairline {{
            border: none;
            min-height: 1px;
            max-height: 1px;
            background: {t.hairline};
        }}
        QFrame#cadexCard {{
            background: {t.card};
            border: 1px solid {t.border};
            border-radius: 6px;
        }}
        QFrame#cadexCard QLabel {{
            background: transparent;
            border: none;
        }}
        QPushButton#cadexPrimary {{
            background: {t.fill};
            color: {ON_ACCENT};
            border: 1px solid {t.fill};
            border-radius: 4px;
            padding: 5px 18px;
            font-weight: 600;
        }}
        QPushButton#cadexPrimary:hover {{
            background: {t.fill_hover};
            border-color: {t.fill_hover};
        }}
        QPushButton#cadexPrimary:pressed {{
            background: {t.fill_pressed};
            border-color: {t.fill_pressed};
        }}
        QPushButton#cadexPrimary:disabled {{
            background: {t.fill_disabled};
            border-color: {t.fill_disabled};
            color: {ON_ACCENT};
        }}
        QToolButton#cadexSegment {{
            background: {t.card};
            color: {t.text};
            border: 1px solid {t.border};
            padding: 4px 16px;
            margin: 0px;
        }}
        QToolButton#cadexSegment[position="first"] {{
            border-top-left-radius: 4px;
            border-bottom-left-radius: 4px;
        }}
        QToolButton#cadexSegment[position="last"] {{
            border-top-right-radius: 4px;
            border-bottom-right-radius: 4px;
        }}
        QToolButton#cadexSegment[position="middle"], QToolButton#cadexSegment[position="last"] {{
            border-left: none;
        }}
        QToolButton#cadexSegment:hover:!checked {{
            color: {t.accent};
        }}
        QToolButton#cadexSegment:checked {{
            background: {t.fill};
            color: {ON_ACCENT};
            border-color: {t.fill};
        }}
        QToolButton#cadexSegment:disabled {{
            color: {t.border};
        }}
        QToolButton#cadexSegment:checked:disabled {{
            background: {t.fill_disabled};
            color: {ON_ACCENT};
            border-color: {t.fill_disabled};
        }}
    """
