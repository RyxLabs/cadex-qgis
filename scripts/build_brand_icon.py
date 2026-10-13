# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build icons/cadex.svg from the cadex.ro logo, on a square canvas, in the SVG subset Qt renders.

Usage: python scripts/build_brand_icon.py <cadex.ro logo, cadex.svg>

QtSvg (SVG Tiny 1.2) draws CSS rgba() stops solid black and ignores fill: url(#id) in style
attributes, so both become plain attributes.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "icons", "cadex.svg")

_STOP = re.compile(r'style="stop-color:rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\);"')
_FILL_URL = re.compile(r'style="fill: url\(#([\w-]+)\)"')
_FILL_RGB = re.compile(r'style="fill: rgb\((\d+),\s*(\d+),\s*(\d+)\)"')
_VIEWBOX = re.compile(r'viewBox="([\d.\-]+) ([\d.\-]+) ([\d.]+) ([\d.]+)"')


def _hex(r, g, b):
    return "#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b))


def convert(source: str, padding: float = 0.04) -> str:
    def stop(m):
        r, g, b, a = m.groups()
        extra = f' stop-opacity="{float(a):g}"' if float(a) < 1 else ""
        return f'stop-color="{_hex(r, g, b)}"{extra}'

    text = _STOP.sub(stop, source)
    text = _FILL_URL.sub(r'fill="url(#\1)"', text)
    text = _FILL_RGB.sub(lambda m: f'fill="{_hex(*m.groups())}"', text)
    text = re.sub(r"<\?xml[^>]*\?>\s*", "", text)
    text = re.sub(r"<!--.*?-->\s*", "", text, flags=re.S)
    text = re.sub(r"<desc>.*?</desc>\s*", "", text, flags=re.S)
    text = re.sub(r"<defs>\s*</defs>\s*", "", text)
    text = text.replace(' data-editor-group="true"', "").replace(' style=""', "")
    text = text.replace(' stroke-linecap="round"', "")

    # square canvas, so the mark is not stretched in a square button
    m = _VIEWBOX.search(text)
    if m is None:
        raise ValueError("the logo has no viewBox")
    x, y, w, h = (float(v) for v in m.groups())
    side = max(w, h) * (1 + 2 * padding)
    nx = x - (side - w) / 2
    ny = y - (side - h) / 2
    text = _VIEWBOX.sub(f'viewBox="{nx:.2f} {ny:.2f} {side:.2f} {side:.2f}"', text, count=1)
    text = re.sub(r'<svg([^>]*?) width="[\d.]+" height="[\d.]+"', r'<svg\1 width="128" height="128"', text, count=1)
    text = re.sub(r' xmlns:xlink="[^"]+"', "", text)
    text = re.sub(r' version="1.1"', "", text)
    # drop the namespace declarations repeated on inner elements
    head, sep, body = text.partition(">")
    body = body.replace(' xmlns="http://www.w3.org/2000/svg"', "")
    return head + sep + body.strip() + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1], encoding="utf-8") as fp:
        result = convert(fp.read())
    with open(TARGET, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(result)
    print(f"{TARGET}: {len(result)} bytes")
