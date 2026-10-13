# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stereo 70 (EPSG:3844), the CRS of every computation and every result."""

# EPSG:31700 and custom Stereo 70 definitions transform onto EPSG:3844 without a shift, so
# _is_identity() treats them as the same system

import math
from typing import Optional, Tuple

from qgis.core import (  # type: ignore
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCoordinateTransformContext,
    QgsCsException, QgsPointXY, QgsProject,
)

from . import fmt

AUTHID = "EPSG:3844"
NAME = "Stereo 70 (EPSG:3844)"

# metres; a transform moving no probe point further is an identity
IDENTITY_TOLERANCE = 1e-6

# Stereo 70 x and y of anything in Romania lie well inside this range, in metres
ROMANIA_RANGE = (0.0, 1200000.0)

# how a layer was read into Stereo 70
SAME = "same"                # already Stereo 70 or an equivalent definition
REPROJECTED = "reprojected"
ASSUMED = "assumed"          # no CRS, read as Stereo 70


def crs() -> QgsCoordinateReferenceSystem:
    return QgsCoordinateReferenceSystem(AUTHID)


def _probes(extent):
    """The corners and centre of the extent, else a point in central Romania."""
    if extent is not None and not extent.isEmpty() and all(
            math.isfinite(v) for v in (extent.xMinimum(), extent.yMinimum(),
                                       extent.xMaximum(), extent.yMaximum())):
        return [QgsPointXY(extent.xMinimum(), extent.yMinimum()),
                QgsPointXY(extent.xMaximum(), extent.yMaximum()),
                QgsPointXY(extent.xMinimum(), extent.yMaximum()),
                QgsPointXY(extent.xMaximum(), extent.yMinimum()),
                extent.center()]
    return [QgsPointXY(500000, 500000)]


def _is_identity(transform, extent) -> bool:
    try:
        for point in _probes(extent):
            moved = transform.transform(point)
            if math.hypot(moved.x() - point.x(), moved.y() - point.y()) > IDENTITY_TOLERANCE:
                return False
    except QgsCsException:
        return False
    return True


def transform_for(source_crs, extent=None,
                  context=None) -> Tuple[Optional[QgsCoordinateTransform], str]:
    """(transform to Stereo 70 or None, SAME / REPROJECTED / ASSUMED).

    None when the coordinates would not change. context defaults to the project's transform
    context, which holds the user's datum transformations.
    """
    if source_crs is None or not source_crs.isValid():
        return None, ASSUMED
    target = crs()
    if source_crs == target:
        return None, SAME
    if context is None:
        context = QgsProject.instance().transformContext()
    transform = QgsCoordinateTransform(source_crs, target, context)
    if not transform.isValid() or transform.isShortCircuited() or _is_identity(transform, extent):
        return None, SAME
    return transform, REPROJECTED


def looks_geographic(extent) -> bool:
    """True for an extent within longitude and latitude ranges; Stereo 70 values are
    six-digit metres."""
    if extent is None or extent.isEmpty():
        return False
    return (-180 <= extent.xMinimum() <= extent.xMaximum() <= 180
            and -90 <= extent.yMinimum() <= extent.yMaximum() <= 90)


def _measurable(extent) -> bool:
    return (extent is not None and not extent.isNull() and all(
        math.isfinite(v) for v in (extent.xMinimum(), extent.yMinimum(),
                                   extent.xMaximum(), extent.yMaximum())))


def _in_romania(source_crs, extent) -> bool:
    """Whether the extent's corners and centre, in Stereo 70, lie in ROMANIA_RANGE. source_crs
    None reads the extent as Stereo 70."""
    points = [QgsPointXY(x, y) for x in (extent.xMinimum(), extent.xMaximum())
              for y in (extent.yMinimum(), extent.yMaximum())] + [extent.center()]
    if source_crs is not None and source_crs != crs():
        # PROJ's default operation is close enough for a range this wide; with an empty
        # context a worker thread reads nothing from the project
        transform = QgsCoordinateTransform(source_crs, crs(), QgsCoordinateTransformContext())
        try:
            points = [transform.transform(point) for point in points]
        except QgsCsException:
            return False
    low, high = ROMANIA_RANGE
    return all(math.isfinite(p.x()) and math.isfinite(p.y())
               and low <= p.x() <= high and low <= p.y() <= high for p in points)


def _span(extent, decimals) -> str:
    """The extent's lower-left and upper-right corners, "de la (x; y) până la (x; y)"."""
    def corner(x, y):
        return f"({fmt.number(x, decimals)}; {fmt.number(y, decimals)})"
    return (f"de la {corner(extent.xMinimum(), extent.yMinimum())} până la "
            f"{corner(extent.xMaximum(), extent.yMaximum())}")


def unusable_reason(layer_name, source_crs, extent) -> str:
    """A Romanian sentence when the layer cannot be read as Stereo 70, else "": coordinates
    that do not fit the layer's CRS (degrees without a CRS or in a projected CRS, metres in a
    geographic CRS) or that do not lie in Romania once in Stereo 70. The sentence for
    coordinates outside Romania gives the extent, since a single feature far away is enough
    to cause it."""
    if not _measurable(extent):
        return ""
    advice = "Alegeți-i sistemul de coordonate din proprietățile layerului, apoi rulați din nou."
    if source_crs is None or not source_crs.isValid():
        if looks_geographic(extent):
            return (f"Layerul '{layer_name}' nu are sistem de coordonate, iar coordonatele lui "
                    f"par a fi grade geografice. {advice}")
        if not _in_romania(None, extent):
            return (f"Layerul '{layer_name}' nu are sistem de coordonate și a fost citit ca "
                    f"Stereo 70, dar coordonatele lui, {_span(extent, 2)}, nu se află toate în "
                    "România. Dacă datele sunt în alt sistem, alegeți-l din proprietățile "
                    "layerului, apoi rulați din nou.")
        return ""
    name = source_crs.authid() or source_crs.description()
    if source_crs.isGeographic() and not looks_geographic(extent):
        return (f"Layerul '{layer_name}' are sistemul de coordonate geografice {name}, dar "
                f"coordonatele lui nu sunt grade. {advice}")
    if not source_crs.isGeographic() and looks_geographic(extent):
        return (f"Layerul '{layer_name}' are sistemul de coordonate proiectat {name}, dar "
                f"coordonatele lui par a fi grade geografice. {advice}")
    if not _in_romania(source_crs, extent):
        decimals = 6 if source_crs.isGeographic() else 2
        return (f"Layerul '{layer_name}' are sistemul de coordonate {name}, dar coordonatele "
                f"lui, {_span(extent, decimals)}, nu se află toate în România în acest "
                "sistem.")
    return ""


def notes(snapshots) -> list:
    """End-of-run notes on the layers that were reprojected or had no CRS."""
    reprojected = [f"'{s.name}' ({s.crs.authid() or s.crs.description()})"
                   for s in snapshots if s.crs_state == REPROJECTED]
    assumed = [f"'{s.name}'" for s in snapshots if s.crs_state == ASSUMED]
    result = []
    if reprojected:
        result.append(("Layerul {0} a fost reproiectat în Stereo 70 (EPSG:3844)."
                       if len(reprojected) == 1 else
                       "Layerele {0} au fost reproiectate în Stereo 70 (EPSG:3844).")
                      .format(", ".join(reprojected)))
    if assumed:
        result.append(("Layerul {0} nu are sistem de coordonate și a fost citit ca Stereo 70."
                       if len(assumed) == 1 else
                       "Layerele {0} nu au sistem de coordonate și au fost citite ca Stereo 70.")
                      .format(", ".join(assumed)))
    return result
