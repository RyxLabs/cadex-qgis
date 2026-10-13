# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plugin icons for the light or dark QGIS theme. QtGui only, so qgis_process can load it."""

import os

from qgis.PyQt.QtGui import QGuiApplication, QIcon, QPalette  # type: ignore
from qgis.core import QgsApplication  # type: ignore

ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

# QGIS's dark themes are matched by name as well, for a theme that sets only a style sheet;
# the palette check covers OS dark mode and custom themes
DARK_THEMES = frozenset(("night mapping", "blend of gray"))


def dark_ui() -> bool:
    if (QgsApplication.themeName() or "").strip().lower() in DARK_THEMES:
        return True
    if QGuiApplication.instance() is None:
        return False
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def path(name: str) -> str:
    # icons without a dark variant (the brand mark) fall back to the light file
    if dark_ui():
        dark = os.path.join(ICONS_DIR, "dark", name)
        if os.path.exists(dark):
            return dark
    return os.path.join(ICONS_DIR, name)


def icon(name: str) -> QIcon:
    return QIcon(path(name))
