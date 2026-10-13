# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""DXF output of Conversie CGXML: a closed polyline per ring and labels inside each polygon.
Only write() needs ezdxf."""

import heapq
import math

# label text heights in drawing units (metres in Stereo 70)
MAX_TEXT_HEIGHT = 2.5
MIN_TEXT_HEIGHT = 0.3
LINE_SPACING = 1.5  # times the text height
# polygons at least this much longer than wide get the label along their main axis
ROTATE_ASPECT = 2.0
# polylabel precision as a share of the polygon's shorter side, so it scales with the size
POLYLABEL_PRECISION = 0.01
# polylabel refines at most this many cells after the initial grid. Along a long strip a
# whole line of points lies equally far from the edges, and refining all of them to the
# precision takes up to 25,000 cells on real parcels.
POLYLABEL_MAX_CELLS = 2000


def _signed_distance(x, y, rings):
    """Distance to the nearest edge, positive inside the polygon, negative outside."""
    inside = False
    best = math.inf
    # written out in one loop: polylabel calls this thousands of times per polygon
    for ring in rings:
        ax, ay = ring[-1]
        for bx, by in ring:
            if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
                inside = not inside
            dx, dy = bx - ax, by - ay
            px, py = x - ax, y - ay
            length = dx * dx + dy * dy
            if length:
                t = (px * dx + py * dy) / length
                if t >= 1:
                    px, py = x - bx, y - by
                elif t > 0:
                    px -= t * dx
                    py -= t * dy
            squared = px * px + py * py
            if squared < best:
                best = squared
            ax, ay = bx, by
    distance = math.sqrt(best)
    return distance if inside else -distance


def _scanline_point(rings, min_y, max_y):
    """A point inside the polygon, as GEOS's pointOnSurface finds it: the middle of the
    widest stretch inside the polygon along a horizontal line that passes near the middle of
    its height and through no vertex. None when the line crosses no edge."""
    middle = (min_y + max_y) / 2.0
    low, high = min_y, max_y
    for ring in rings:
        for _x, y in ring:
            if low < y <= middle:
                low = y
            elif middle < y < high:
                high = y
    y = (low + high) / 2.0
    crossings = []
    for ring in rings:
        ax, ay = ring[-1]
        for bx, by in ring:
            if (ay > y) != (by > y):
                crossings.append(ax + (y - ay) * (bx - ax) / (by - ay))
            ax, ay = bx, by
    crossings.sort()
    stretches = list(zip(crossings[0::2], crossings[1::2]))
    if not stretches:
        return None
    left, right = max(stretches, key=lambda s: s[1] - s[0])
    return ((left + right) / 2.0, y)


def pole_of_inaccessibility(rings, precision=POLYLABEL_PRECISION):
    """The inner point farthest from the edges (polylabel), within the precision or as near
    as POLYLABEL_MAX_CELLS allows. Unlike the centroid, it is always inside the polygon: the
    search starts from a point on a scan line inside it and only moves to a point farther
    from the edges."""
    exterior = rings[0]
    xs = [p[0] for p in exterior]
    ys = [p[1] for p in exterior]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    size = min(max_x - min_x, max_y - min_y)
    if size <= 0:
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    tolerance = size * precision

    counter = 0

    def cell(cx, cy, h):
        nonlocal counter
        d = _signed_distance(cx, cy, rings)
        counter += 1
        # best possible distance within the cell, negated for the min-heap
        return (-(d + h * math.sqrt(2)), counter, cx, cy, h, d)

    half = size / 2.0
    queue = []
    x = min_x
    while x < max_x:
        y = min_y
        while y < max_y:
            heapq.heappush(queue, cell(x + half, y + half, half))
            y += size
        x += size

    starts = [cell(sum(xs) / len(xs), sum(ys) / len(ys), 0)]
    inside = _scanline_point(rings, min_y, max_y)
    if inside is not None:
        starts.append(cell(inside[0], inside[1], 0))
    best = max(starts + queue, key=lambda c: c[5])
    limit = counter + POLYLABEL_MAX_CELLS
    while queue and counter < limit:
        item = heapq.heappop(queue)
        if item[5] > best[5]:
            best = item
        if -item[0] - best[5] <= tolerance:
            continue
        h = item[4] / 2.0
        for dx in (-h, h):
            for dy in (-h, h):
                heapq.heappush(queue, cell(item[2] + dx, item[3] + dy, h))
    return (best[2], best[3])


def main_axis_angle(ring):
    """Angle of the ring's main axis, -90 to 90 degrees (principal component)."""
    n = len(ring)
    if n < 3:
        return 0.0
    cx = sum(p[0] for p in ring) / n
    cy = sum(p[1] for p in ring) / n
    xx = sum((p[0] - cx) ** 2 for p in ring) / n
    yy = sum((p[1] - cy) ** 2 for p in ring) / n
    xy = sum((p[0] - cx) * (p[1] - cy) for p in ring) / n
    if xx == yy and xy == 0:
        return 0.0
    return math.degrees(0.5 * math.atan2(2 * xy, xx - yy))


def extent_along(ring, angle_deg):
    """(length, width) of the ring along and across the axis."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    along = [p[0] * ca + p[1] * sa for p in ring]
    across = [-p[0] * sa + p[1] * ca for p in ring]
    return max(along) - min(along), max(across) - min(across)


def label_layout(lines, rings):
    """(x, y, text height, rotation) of a label whose lines fit inside the polygon."""
    exterior = rings[0]
    angle = main_axis_angle(exterior)
    length, width = extent_along(exterior, angle)
    if width <= 0 or length / width < ROTATE_ASPECT:
        angle = 0.0
        length, width = extent_along(exterior, 0.0)
    longest = max((len(line) for line in lines), default=1) or 1
    # 0.6: typical character width to height ratio of the default DXF font
    by_length = (length * 0.8) / (longest * 0.6)
    by_width = (width * 0.75) / (len(lines) * LINE_SPACING)
    height = max(min(by_length, by_width, MAX_TEXT_HEIGHT), MIN_TEXT_HEIGHT)
    x, y = pole_of_inaccessibility(rings)
    return x, y, height, angle


def cad_layer_name(text: str) -> str:
    """ASCII upper case layer name without the characters DXF forbids."""
    from .roles import fold
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_"
                      for c in fold(text).upper())
    return cleaned.strip("_") or "LAYER"


def _never():
    return False


def write(path, groups, stop=_never, progress=None) -> bool:
    """Write the DXF. groups: [(cad layer, (color index, "#rrggbb"), records, label_fn)], each
    record with its "rings" parts; label_fn(record) returns the label lines. progress
    receives 0 to 1 while the records are drawn. Returns False, having written nothing, when
    stop() turns true."""
    import ezdxf
    from ezdxf.colors import rgb2int
    from ezdxf.enums import TextEntityAlignment

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    bounds = [math.inf, math.inf, -math.inf, -math.inf]
    total = max(sum(len(records) for _layer, _color, records, _fn in groups), 1)
    done = 0

    for cad_layer, (color, rgb), records, label_fn in groups:
        text_layer = f"TEXT_{cad_layer}"
        if cad_layer not in doc.layers:
            red, green, blue = (int(rgb[i:i + 2], 16) for i in (1, 3, 5))
            doc.layers.add(cad_layer, color=color, true_color=rgb2int((red, green, blue)))
        if text_layer not in doc.layers:
            doc.layers.add(text_layer, color=7)
        for record in records:
            if stop():
                return False
            done += 1
            if progress is not None:
                progress(done / total)
            parts = record.get("rings") or []
            if not parts:
                continue
            for part in parts:
                for ring in part:
                    msp.add_lwpolyline(ring, close=True,
                                       dxfattribs={"layer": cad_layer})
                    for x, y in ring:
                        bounds[0] = min(bounds[0], x)
                        bounds[1] = min(bounds[1], y)
                        bounds[2] = max(bounds[2], x)
                        bounds[3] = max(bounds[3], y)
            lines = [line for line in label_fn(record) if line]
            if not lines:
                continue
            # the label goes in the largest part, which organize_rings puts first
            x, y, height, angle = label_layout(lines, parts[0])
            spacing = height * LINE_SPACING
            rad = math.radians(angle + 90)
            nx, ny = math.cos(rad), math.sin(rad)
            top = (len(lines) - 1) * spacing / 2.0
            for i, line in enumerate(lines):
                offset = top - i * spacing
                text = msp.add_text(line, height=height, dxfattribs={
                    "layer": text_layer, "rotation": angle})
                text.set_placement((x + nx * offset, y + ny * offset),
                                   align=TextEntityAlignment.MIDDLE_CENTER)

    if stop():
        return False
    if bounds[0] < math.inf:
        doc.header["$EXTMIN"] = (bounds[0], bounds[1], 0)
        doc.header["$EXTMAX"] = (bounds[2], bounds[3], 0)
        # the drawing opens zoomed to the data instead of the origin
        doc.set_modelspace_vport(
            height=max(bounds[3] - bounds[1], bounds[2] - bounds[0]) * 1.05,
            center=((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2))
    doc.saveas(path)
    return True
