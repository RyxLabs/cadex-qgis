# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Styles of the result layers."""

import sys

from qgis.PyQt.QtGui import QColor, QFont, QFontDatabase  # type: ignore
from qgis.core import (  # type: ignore
    Qgis, QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsMapLayer, QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsRendererCategory, QgsSingleSymbolRenderer, QgsTextBufferSettings, QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
)

# outline of the Conversie CGXML layers, also the color of their DXF layers; chosen to stay
# visible over orthophotos (vegetation, tiled roofs, forest, asphalt) and on a white canvas
CADASTRAL_COLORS = {"imobile": "#FF00FF", "constructii": "#00B8E6"}
LABEL_COLOR = QColor("#161A22")
# labels are drawn from this scale in; over a whole UAT they cover the map and take ten times
# longer to draw than the outlines
LABEL_MIN_SCALE = {"imobile": 10000, "constructii": 2500}


def _font_families():
    try:
        return QFontDatabase.families()
    except TypeError:
        # Qt 5: an instance method
        return QFontDatabase().families()


def _label_text_format():
    """A text format on the application's font. Without a screen Qt has no font database and
    names Helvetica, which Windows lacks; there Arial, which every Windows has."""
    text_format = QgsTextFormat()
    if sys.platform == "win32" and not _font_families():
        text_format.setFont(QFont("Arial"))
    return text_format


def _outline_symbol(color, width=0.5):
    return QgsFillSymbol.createSimple({
        "color": "0,0,0,0",
        "outline_color": color,
        "outline_width": str(width),
        "outline_width_unit": "MM",
    })


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def label_expression(parts):
    """An expression putting each non-empty field of parts [(field, prefix)] on a line."""
    pieces = []
    for field, prefix in parts:
        ref = _quote(field)
        literal = prefix.replace("'", "''")
        pieces.append(f"if({ref} IS NULL OR to_string({ref}) = '', NULL, "
                      f"'{literal}' || to_string({ref}))")
    if not pieces:
        return ""
    # array_filter drops the NULLs, so an empty value leaves no blank line
    return ("array_to_string(array_filter(array(" + ", ".join(pieces)
            + "), @element IS NOT NULL), '\\n')")


def _apply_labels(layer, expression, size=8.0, min_scale=0):
    if not expression:
        layer.setLabelsEnabled(False)
        return
    settings = QgsPalLayerSettings()
    settings.fieldName = expression
    settings.isExpression = True
    settings.placement = Qgis.LabelPlacement.OverPoint
    # the centroid of an L-shaped parcel lies outside it
    settings.centroidInside = True
    if min_scale:
        settings.scaleVisibility = True
        settings.minimumScale = min_scale
        settings.maximumScale = 0
    text_format = _label_text_format()
    text_format.setSize(size)
    text_format.setColor(LABEL_COLOR)
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer)
    settings.setFormat(text_format)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def cadastral_label_parts(which: str, names):
    """[(field, prefix)] of a converted layer's label: ID and IE for imobile, number and
    destination for construcții. names is [(column key, output name)]."""
    names = dict(names)
    if which == "imobile":
        return [(names[k], p) for k, p in (("id", "ID "), ("ie", "IE ")) if k in names]
    return [(names[k], "") for k in ("nr_constr", "destinatie") if k in names]


def style_cadastral(layer, which: str, label_parts):
    """Outline without fill and label for a Conversie CGXML layer."""
    width = 0.5 if which == "imobile" else 0.4
    layer.setRenderer(QgsSingleSymbolRenderer(_outline_symbol(CADASTRAL_COLORS[which], width)))
    _apply_labels(layer, label_expression(label_parts), min_scale=LABEL_MIN_SCALE[which])
    layer.triggerRepaint()


def store_with_file(layer):
    """Save the layer's style, its CADex custom properties included, with its file: in a
    GeoPackage's style table, as a .qml next to a Shapefile. QGIS applies it whenever the file
    is opened, in any project, so the other tools can still read a converted layer's columns
    and recognise a report. Other providers are left alone."""
    if layer.providerType() != "ogr":
        return
    path = layer.source().split("|")[0].lower()
    categories = QgsMapLayer.StyleCategory.AllStyleCategories
    if path.endswith(".gpkg"):
        # saveStyleToDatabase is deprecated where its V2 exists
        save = getattr(layer, "saveStyleToDatabaseV2", None) or layer.saveStyleToDatabase
        save("CADex", "", True, "", categories)
    elif path.endswith(".shp"):
        layer.saveDefaultStyle(categories)


def style_categories(layer, field: str, categories):
    """Categorized renderer from [(value, label, "#rrggbb")], with only the values the layer
    holds, so the legend has no empty rows: translucent fill for polygons, 3 mm circles for
    points."""
    points = layer.geometryType() == Qgis.GeometryType.Point
    present = layer.uniqueValues(layer.fields().indexOf(field))
    items = []
    for value, label, hex_color in categories:
        if value not in present:
            continue
        outline = QColor(hex_color).darker(140).name()
        if points:
            symbol = QgsMarkerSymbol.createSimple({
                "name": "circle", "color": hex_color, "outline_color": outline,
                "outline_width": "0.3", "size": "3", "size_unit": "MM",
            })
        else:
            fill = QColor(hex_color)
            fill.setAlpha(110)
            symbol = QgsFillSymbol.createSimple({
                "color": f"{fill.red()},{fill.green()},{fill.blue()},{fill.alpha()}",
                "outline_color": outline,
                "outline_width": "0.4",
                "outline_width_unit": "MM",
            })
        items.append(QgsRendererCategory(value, symbol, label))
    layer.setRenderer(QgsCategorizedSymbolRenderer(field, items))
    layer.triggerRepaint()


def style_fill(layer, hex_color):
    """Translucent single-color fill; points and lines only get the color."""
    color = QColor(hex_color)
    if layer.geometryType() == Qgis.GeometryType.Polygon:
        fill = QColor(color)
        fill.setAlpha(110)
        symbol = QgsFillSymbol.createSimple({
            "color": f"{fill.red()},{fill.green()},{fill.blue()},{fill.alpha()}",
            "outline_color": color.darker(140).name(),
            "outline_width": "0.5",
            "outline_width_unit": "MM",
        })
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    elif layer.renderer() is not None and hasattr(layer.renderer(), "symbol"):
        layer.renderer().symbol().setColor(color)
    layer.triggerRepaint()
