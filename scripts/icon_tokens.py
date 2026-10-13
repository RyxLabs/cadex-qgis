# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Icon palette: light to dark color pairs and WCAG contrast. Used by build_icon_themes.py
and test_conventions.py."""

import re

FG_LIGHT = "#161A22"
FG_DARK = "#C9D1DC"
AC_LIGHT = "#0971E5"    # logo blue
AC_DARK = "#2CC8FD"     # logo cyan

# applied in this order
SUBSTITUTIONS = (
    (FG_LIGHT, FG_DARK),
    (AC_LIGHT, AC_DARK),
)

LIGHT_TOKENS = frozenset((FG_LIGHT, AC_LIGHT, "none"))
DARK_TOKENS = frozenset((FG_DARK, AC_DARK, "none"))

# QtSvg (SVG Tiny 1.2) draws these color notations solid black
FORBIDDEN_NOTATION = ("rgba(", "rgb(", "hsl(")

# toolbar backgrounds of the QGIS Default and Night Mapping themes
BG_DEFAULT = "#F0F0F0"
BG_NIGHT = "#333333"


def _channel(value):
    if value <= 0.03928:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def luminance(hex_color):
    """WCAG relative luminance of a #rrggbb string."""
    channels = (int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    red, green, blue = (_channel(c) for c in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first, second):
    """WCAG contrast ratio, 1.0 to 21.0; icons need at least 3:1."""
    values = (luminance(first), luminance(second))
    return (max(values) + 0.05) / (min(values) + 0.05)


_COLOR = re.compile(r'(?<![-\w])(?:fill|stroke)="([^"]+)"')


def svg_colors(text):
    """Every fill and stroke value, "none" included."""
    return set(_COLOR.findall(text))
