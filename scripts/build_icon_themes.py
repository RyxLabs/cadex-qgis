# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate icons/dark/ from the light icons by swapping the palette colors.

A light icon with a color outside the palette is an error, since it would not be converted.
The output is committed: QGIS installs plugins from the zip without a build step.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import icon_tokens  # noqa: E402

ICONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")

# the brand mark keeps its colors in both themes
SKIP = frozenset(("cadex.svg",))


class IconError(Exception):
    """A light icon uses a color outside the palette."""


def to_dark(source):
    result = source
    for light, dark in icon_tokens.SUBSTITUTIONS:
        result = result.replace(light, dark)
    return result


def check(name, source):
    for notation in icon_tokens.FORBIDDEN_NOTATION:
        if notation in source:
            raise IconError(f"{name}: uses {notation}) notation - QtSvg does not "
                            "parse it and falls back to solid black.")
    extra = icon_tokens.svg_colors(source) - icon_tokens.LIGHT_TOKENS
    if extra:
        raise IconError(f"{name}: color(s) outside the palette: {', '.join(sorted(extra))}")


def build(icons_dir=ICONS):
    dark_dir = os.path.join(icons_dir, "dark")
    os.makedirs(dark_dir, exist_ok=True)
    written = []
    for name in sorted(os.listdir(icons_dir)):
        if not name.endswith(".svg") or name in SKIP:
            continue
        with open(os.path.join(icons_dir, name), encoding="utf-8") as fp:
            source = fp.read()
        check(name, source)
        with open(os.path.join(dark_dir, name), "w", encoding="utf-8", newline="\n") as fp:
            fp.write(to_dark(source))
        written.append(name)
    # remove dark icons whose light source is gone
    for name in sorted(os.listdir(dark_dir)):
        if name.endswith(".svg") and name not in written:
            os.remove(os.path.join(dark_dir, name))
    return written


if __name__ == "__main__":
    names = build()
    print(f"{len(names)} dark icons written to icons/dark/")
