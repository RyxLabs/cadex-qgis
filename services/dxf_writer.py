# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""DXF output of Conversie CGXML: a closed polyline per ring and labels inside each polygon.
write() goes through GDAL's DXF driver."""

import heapq
import math
import os
import re
from contextlib import ExitStack, contextmanager

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
# the most cells the initial grid has along the polygon's longer side
POLYLABEL_GRID = 500


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


def _clearance(x, y, rings, obstacles):
    """Distance to the nearest edge of the polygon or of an obstacle, positive inside the
    polygon and outside every obstacle, negative elsewhere."""
    distance = _signed_distance(x, y, rings)
    for polygon in obstacles:
        distance = min(distance, -_signed_distance(x, y, polygon))
    return distance


def pole_of_inaccessibility(rings, precision=POLYLABEL_PRECISION, obstacles=()):
    """The inner point farthest from the edges (polylabel), within the precision or as near
    as POLYLABEL_MAX_CELLS allows. Unlike the centroid, it is always inside the polygon: the
    search starts from a point on a scan line inside it and only moves to a point farther
    from the edges. The point also stays out of the obstacles, polygons given as lists of
    rings."""
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
        d = _clearance(cx, cy, rings, obstacles)
        counter += 1
        # best possible distance within the cell, negated for the min-heap
        return (-(d + h * math.sqrt(2)), counter, cx, cy, h, d)

    # square cells as wide as the shorter side, but at least 1/POLYLABEL_GRID of the longer
    # side, so a very thin polygon starts from at most POLYLABEL_GRID cells along it
    side = max(size, max(max_x - min_x, max_y - min_y) / POLYLABEL_GRID)
    half = side / 2.0
    queue = []
    x = min_x
    while x < max_x:
        y = min_y
        while y < max_y:
            heapq.heappush(queue, cell(x + half, y + half, half))
            y += side
        x += side

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


def label_layout(lines, rings, obstacles=(), max_height=MAX_TEXT_HEIGHT):
    """(x, y, text height, rotation) of a label whose lines fit inside the polygon. The label
    goes to the part of the polygon the obstacles leave free, when they leave any; its height
    comes from the whole polygon, at most max_height."""
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
    height = max(min(by_length, by_width, max_height), MIN_TEXT_HEIGHT)
    if obstacles:
        x, y = pole_of_inaccessibility(rings, obstacles=obstacles)
        if _clearance(x, y, rings, obstacles) > 0:
            return x, y, height, angle
    x, y = pole_of_inaccessibility(rings)
    return x, y, height, angle


def cad_layer_name(text: str) -> str:
    """ASCII upper case layer name without the characters DXF forbids."""
    from .roles import fold
    cleaned = "".join(c if (c.isascii() and c.isalnum()) or c in "-_" else "_"
                      for c in fold(text).upper())
    return cleaned.strip("_") or "LAYER"


def _never():
    return False


# a TrueType font of every Windows, with the Romanian letters; a CAD program without it
# draws the labels in its own default font
LABEL_FONT = "Arial"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
# GDAL's writer escapes neither brace, and MTEXT reads a pair of them as a group, not as text
_BRACES = str.maketrans("{}", "()")


def label_text(text) -> str:
    """text as one line of a label: line breaks and tabs become a space, braces become
    parentheses, and a character outside the 16-bit range, which GDAL's writer splits in
    two, becomes '?'."""
    text = _CONTROL.sub(" ", str(text)).translate(_BRACES)
    return "".join(c if ord(c) <= 0xFFFF else "?" for c in text).strip()


def _label_style(text, height, angle) -> str:
    """The OGR style string of a label centred on its point (anchor 5), its height in ground
    units. text is a label_text()."""
    quoted = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'LABEL(f:"{LABEL_FONT}",t:"{quoted}",s:{height:.4f}g,a:{angle:.4f},p:5)'


def _pairs(path) -> list:
    """The (group code, value) pairs of a DXF text file. Raises ValueError for a file that is
    not a list of such pairs."""
    with open(path, encoding="latin-1") as fp:
        lines = fp.read().splitlines()
    if len(lines) % 2:
        raise ValueError(path)
    return [(str(int(lines[i])), lines[i + 1]) for i in range(0, len(lines), 2)]


def header_template(layers, bounds):
    """The text of GDAL's own DXF header with a LAYER record for each of layers, [(name,
    color index, "#rrggbb" or None)], metres as the unit of the drawing and the opening view
    on bounds, (min x, min y, max x, max y) or None. In a drawing with this header the
    entities take the color of their layer. None when GDAL's header is not found or is not
    laid out as expected."""
    from osgeo import gdal
    try:
        header = _pairs(gdal.FindFile("gdal", "header.dxf"))
        trailer = _pairs(gdal.FindFile("gdal", "trailer.dxf"))
        used = {int(value, 16) for code, value in header + trailer if code in ("5", "105")}
    except (OSError, TypeError, ValueError):
        return None
    result = []
    table = record = None
    # the first record of the LAYER table, layer 0, copied for each new layer
    model = None
    copying = added = False
    handle = 0x1000
    for index, (code, value) in enumerate(header):
        if code == "0":
            record = value
            if value == "TABLE" and index + 1 < len(header):
                table = header[index + 1][1]
            copying = table == "LAYER" and value == "LAYER" and model is None
            if copying:
                model = []
            if value == "ENDTAB":
                if table == "LAYER" and model:
                    for name, color, rgb in layers:
                        while handle in used:
                            handle += 1
                        used.add(handle)
                        result += _layer_record(model, f"{handle:X}", name, color, rgb)
                    added = True
                table = None
        if copying:
            model.append((code, value))
        if table == "VPORT" and record == "VPORT" and bounds is not None:
            min_x, min_y, max_x, max_y = bounds
            # the drawing opens on the data instead of the origin
            value = {"12": repr((min_x + max_x) / 2.0), "22": repr((min_y + max_y) / 2.0),
                     "40": repr(max(max_x - min_x, max_y - min_y, 1.0) * 1.05)}.get(code, value)
        elif code == "70" and index and header[index - 1] == ("9", "$INSUNITS"):
            # 6 is the metre
            value = "     6"
        result.append((code, value))
    if not added:
        return None
    return "".join(f"{code:>3}\n{value}\n" for code, value in result)


def _layer_record(model, handle, name, color, rgb) -> list:
    """The pairs of a LAYER record like model, with its own handle, name, color index (group
    62) and, with rgb, true color (group 420)."""
    record = []
    for code, value in model:
        value = {"5": handle, "2": name, "62": f"{color:6d}"}.get(code, value)
        record.append((code, value))
        if code == "62" and rgb:
            record.append(("420", str(int(rgb.lstrip("#"), 16))))
    return record


def _bounds(groups):
    """(min x, min y, max x, max y) of every ring of the groups' records, None without one."""
    points = [point for _layer, _color, records, *_label in groups for record in records
              for part in record.get("rings") or [] for ring in part for point in ring]
    if not points:
        return None
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    return min(xs), min(ys), max(xs), max(ys)


@contextmanager
def _exceptions(*modules):
    """GDAL errors as exceptions on this thread, whatever the rest of QGIS has chosen; gdal
    and ogr each keep their own switch. A GDAL older than 3.7 has none: there a failed call
    returns None or an error code, unless exceptions are on for the whole process."""
    with ExitStack() as stack:
        for module in modules:
            manager = getattr(module, "ExceptionMgr", None)
            if manager is not None:
                stack.enter_context(manager(useExceptions=True))
        yield


def write(path, groups, stop=_never, progress=None) -> bool:
    """Write the DXF. groups: [(cad layer, (color index, "#rrggbb"), records, label_fn)], each
    record with its "rings" parts; label_fn(record) returns the label lines. A group may end
    with label options: {"obstacles": function of a record returning the polygons its label
    keeps off, "max_height": the label's text height ceiling}. progress
    receives 0 to 1 while the records are drawn. Returns False, leaving no file at path, when
    stop() turns true. Raises OSError, with path as its file name, when the drawing cannot be
    written."""
    from osgeo import gdal, ogr

    # the operating system's own reason when the file cannot be created
    with open(path, "wb"):
        pass
    # one definition per layer name: two groups given the same name share the layer
    layers = {}
    for layer, (color, rgb), *_rest in groups:
        layers.setdefault(layer, (layer, color, rgb))
    for layer in list(layers):
        layers.setdefault(f"TEXT_{layer}", (f"TEXT_{layer}", 7, None))
    layers = list(layers.values())
    header_path = f"{path}.header"
    complete = False
    try:
        with _exceptions(gdal, ogr):
            source = None
            header = header_template(layers, _bounds(groups))
            if header is not None:
                with open(header_path, "w", encoding="latin-1", newline="\n") as fp:
                    fp.write(header)
                source = _create(ogr, path, [f"HEADER={header_path}"])
            # without the header's layers, each ring carries its color
            colored = source is None
            if source is None:
                source = _create(ogr, path, [])
            if source is None:
                raise OSError(None, gdal.GetLastErrorMsg() or "DXF", path)
            drawn = _draw(ogr, source, groups, colored, stop, progress)
            # closing writes the file: GDAL joins the header, the entities and its trailer
            gdal.ErrorReset()
            source = None
            if gdal.GetLastErrorType() >= gdal.CE_Failure:
                raise OSError(None, gdal.GetLastErrorMsg(), path)
            if drawn:
                _count_before_flags(path)
            complete = drawn
    except RuntimeError as error:
        raise OSError(None, str(error), path) from error
    finally:
        for leftover in (header_path, f"{path}.tmp") + (() if complete else (path,)):
            if os.path.exists(leftover):
                os.remove(leftover)
    return complete


# GDAL writes the flags of an LWPOLYLINE (group 70) before its vertex count (group 90), and
# AutoCAD reads the closed flag only after the count: it draws such a polyline open
_FLAGS_THEN_COUNT = re.compile(
    rb"(\n *100\r?\nAcDbPolyline\r?\n)( *70\r?\n[^\r\n]*\r?\n)( *90\r?\n[^\r\n]*\r?\n)")


def _count_before_flags(path):
    """Put the vertex count of each polyline of the DXF at path before its flags."""
    with open(path, "rb") as fp:
        data = fp.read()
    data, swapped = _FLAGS_THEN_COUNT.subn(rb"\1\3\2", data)
    if swapped:
        with open(path, "wb") as fp:
            fp.write(data)


def _create(ogr, path, options):
    """The data source of a new DXF at path; None when GDAL refuses it with these options."""
    try:
        return ogr.GetDriverByName("DXF").CreateDataSource(path, options=options)
    except RuntimeError:
        if not options:
            raise
        return None


def _draw(ogr, source, groups, colored, stop, progress) -> bool:
    """Add the rings and labels of the groups to the data source; colored gives each ring its
    own color. False when stop() turned true."""
    layer = source.CreateLayer("entities")
    definition = layer.GetLayerDefn()

    def add(name, geometry, style):
        feature = ogr.Feature(definition)
        feature.SetField("Layer", name)
        feature.SetGeometryDirectly(geometry)
        if style:
            feature.SetStyleString(style)
        if layer.CreateFeature(feature) != 0:
            raise RuntimeError(f"CreateFeature: {name}")

    total = max(sum(len(records) for _layer, _color, records, *_label in groups), 1)
    done = 0
    for cad_layer, (_color, rgb), records, label_fn, *options in groups:
        options = options[0] if options else {}
        obstacles_of = options.get("obstacles")
        max_height = options.get("max_height", MAX_TEXT_HEIGHT)
        text_layer = f"TEXT_{cad_layer}"
        pen = f"PEN(c:{rgb})" if colored else ""
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
                    # a linear ring becomes a closed polyline of these points; a polygon
                    # would become a hatch
                    outline = ogr.Geometry(ogr.wkbLinearRing)
                    for x, y in ring:
                        outline.AddPoint_2D(x, y)
                    add(cad_layer, outline, pen)
            lines = [line for line in map(label_text, label_fn(record)) if line]
            if not lines:
                continue
            # the label goes in the largest part, which organize_rings puts first
            obstacles = obstacles_of(record) if obstacles_of is not None else ()
            x, y, height, angle = label_layout(lines, parts[0], obstacles, max_height)
            spacing = height * LINE_SPACING
            rad = math.radians(angle + 90)
            nx, ny = math.cos(rad), math.sin(rad)
            top = (len(lines) - 1) * spacing / 2.0
            for i, line in enumerate(lines):
                offset = top - i * spacing
                point = ogr.Geometry(ogr.wkbPoint)
                point.AddPoint_2D(x + nx * offset, y + ny * offset)
                add(text_layer, point, _label_style(line, height, angle))
    return not stop()
