# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Intersection, difference, symmetric difference and union of polygons, computed on a
fixed grid."""

# GEOS's floating-point overlay can return nothing for the intersection, and the whole first
# polygon for the difference, when that polygon lies inside the other and the other's boundary
# runs a fraction of a millimetre beside one of its edges. This happens when a neighbour's
# corner, rounded to the millimetre, lies next to an edge with no vertex there. GEOS reports
# no error. On a grid the same call gives the true result.

from qgis.core import QgsGeometry, QgsGeometryParameters  # type: ignore

# metres; CGXML coordinates are rounded to the millimetre, and this is a thousandth of that
GRID = 1e-6


def _parameters() -> QgsGeometryParameters:
    parameters = QgsGeometryParameters()
    parameters.setGridSize(GRID)
    return parameters


def _computed(on_grid: QgsGeometry, floating) -> QgsGeometry:
    # GEOS returns a null geometry when it refuses the operation, which on a grid happens for
    # some invalid inputs; an empty geometry is a real empty result
    return floating() if on_grid.isNull() else on_grid


def intersection(a: QgsGeometry, b: QgsGeometry) -> QgsGeometry:
    return _computed(a.intersection(b, _parameters()), lambda: a.intersection(b))


def difference(a: QgsGeometry, b: QgsGeometry) -> QgsGeometry:
    """The part of a outside b."""
    return _computed(a.difference(b, _parameters()), lambda: a.difference(b))


def sym_difference(a: QgsGeometry, b: QgsGeometry) -> QgsGeometry:
    """The parts of a and b that the other does not cover."""
    return _computed(a.symDifference(b, _parameters()), lambda: a.symDifference(b))


def union(geometries) -> QgsGeometry:
    """One geometry covering all of geometries; a null geometry for none."""
    geometries = list(geometries)
    if not geometries:
        return QgsGeometry()
    return _computed(QgsGeometry.unaryUnion(geometries, _parameters()),
                     lambda: QgsGeometry.unaryUnion(geometries))
