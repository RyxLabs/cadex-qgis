# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verificare Topologică: overlaps, gaps, unsnapped vertices and misplaced construcții in
cadastral sectors. Reports only, never edits."""

# rules:
# - the imobile of a complete sector cover it without overlaps or gaps
# - Generare CG rejects self-intersecting polygons and construcție vertices outside the imobil
# - sector 0 holds the roads between sectors; roads are not snapped like the sectors, so gaps
#   and snapping skip them and an overlap with a road is only a warning
# - ground enclosed by imobile of several sectors belongs to no sector and can be a road that
#   is not among the checked imobile, so a hole there is only a warning; inside the outline of
#   a sector it is that sector's uncovered ground
# defaults:
# - a vertex and the segment it should lie on, both rounded to the millimetre, can end up
#   1.4 mm apart, so 2 mm still counts as snapped
# - a vertex 2 mm to 50 cm short of a neighbour leaves an open sliver no area check finds
# - overlaps under 0.01 mp come from coordinate rounding
# - so do overlaps and gaps narrower than the snapping precision, whatever their area: 1 mm
#   along a 100 m boundary is 0.1 mp
# - a construcție vertex more than 1 cm outside its imobil is an error, not rounding; so is
#   ground of a construcție outside its imobil wider than 1 cm, as where a wall crosses a
#   notch of the limit with no vertex in it

import math
import os
from collections import defaultdict, namedtuple
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from qgis.core import (  # type: ignore
    Qgis, QgsCurvePolygon, QgsFeature, QgsField, QgsFields, QgsGeometry, QgsPointXY,
    QgsPolygon, QgsRectangle, QgsSpatialIndex,
)

from . import cgxml_export, cgxml_sources, fmt, overlay, stereo70, xlsx_report
from .compare_service import normalize_key
from .tasks import CadexTask
from ..qt_compat import FIELD_DOUBLE, FIELD_STRING, is_null

ERROR = "Eroare"
WARNING = "Avertisment"

INVALID = "Geometrie invalidă"
OVERLAP = "Suprapunere între imobile"
GAP = "Gol între imobile"
UNSNAPPED = "Punct neracordat"
UNCOVERED = "Suprafață neacoperită din sector"
OUTSIDE_SECTOR = "Imobil în afara sectorului"
BUILDING_OUTSIDE = "Construcție în afara imobilului"
BUILDING_ELSEWHERE = "Construcție pe alt imobil"
BUILDING_ALONE = "Construcție fără imobil"
BUILDING_OVERLAP = "Suprapunere între construcții"
LINK_MISSING = "Imobil negăsit"

KINDS = (INVALID, OVERLAP, GAP, UNSNAPPED, UNCOVERED, OUTSIDE_SECTOR,
         BUILDING_OUTSIDE, BUILDING_ELSEWHERE, BUILDING_ALONE, BUILDING_OVERLAP,
         LINK_MISSING)
KIND_COLORS = {
    INVALID: "#7B3FB0", OVERLAP: "#D64545", GAP: "#E3A008", UNSNAPPED: "#F76707",
    UNCOVERED: "#0CA678", OUTSIDE_SECTOR: "#1971C2", BUILDING_OUTSIDE: "#D6338A",
    BUILDING_ELSEWHERE: "#AE3EC9", BUILDING_ALONE: "#495057",
    BUILDING_OVERLAP: "#862E9C", LINK_MISSING: "#868E96",
}

# the cadastral sector of the roads between sectors, unless the user names another
ROADS = "0"


def _road(sector, roads) -> bool:
    """True for the roads sector; roads None means there is none."""
    return roads is not None and sector == roads


MIN_AREA = 0.01          # mp, smaller overlaps and gaps are ignored
SNAP_PRECISION = 0.002   # m, a vertex this close to a boundary is on it
SNAP_DISTANCE = 0.50     # m, a vertex this close to a neighbour should be snapped to it
BUILDING_TOLERANCE = 0.01  # m

# Romanian text for the GEOS validity messages
_GEOS_REASONS = {
    "self-intersection": "conturul se intersectează cu el însuși",
    "ring self-intersection": "un inel al conturului se atinge pe sine",
    "too few points in geometry component": "conturul are prea puține puncte",
    "hole lies outside shell": "o gaură iese din contur",
    "holes are nested": "o gaură stă în interiorul altei găuri",
    "interior is disconnected": "găurile taie poligonul în bucăți",
    "nested shells": "o parte a poligonului stă în interiorul alteia",
    "duplicate rings": "același inel apare de două ori",
    "invalid coordinate": "un punct are coordonate care nu sunt numere",
    "ring is not closed": "un inel nu este închis",
}


def _never():
    return False


def normalize_sector(value) -> Optional[str]:
    """5, 5.0, "5" and " 05 " all give "5"; empty and NULL give None."""
    if value is None or is_null(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return text
    if number.is_integer():
        return str(int(number))
    return text


def geos_reason(what: str) -> str:
    return _GEOS_REASONS.get((what or "").strip().lower(), f"geometrie invalidă ({what})")


def _coords(point) -> str:
    x, y = (fmt.number(value, 3, thousands=False) for value in (point.x(), point.y()))
    return f"{x} {y}"


def validity_problem(geometry):
    """(Romanian reason, with the place when GEOS gives one, and the place as a point) for a
    polygon that is not GEOS-valid; None for a valid one."""
    errors = geometry.validateGeometry(Qgis.GeometryValidationEngine.Geos)
    if not errors:
        return None
    error = errors[0]
    if error.hasWhere():
        return (f"{geos_reason(error.what())} în punctul {_coords(error.where())}",
                QgsGeometry.fromPointXY(QgsPointXY(error.where())))
    return geos_reason(error.what()), geometry.pointOnSurface()


def _cm(meters) -> str:
    """The distance in centimetres with one decimal; under half a millimetre, where that
    would read 0 cm, in millimetres."""
    if meters < 0.0005:
        return f"{fmt.number(meters * 1000, 3)} mm"
    return f"{fmt.number(meters * 100, 1)} cm"


def _listed(names) -> str:
    """"5", "5 și 6", "5, 6 și 7"."""
    names = list(names)
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} și {names[-1]}"


def polygons_of(geometry) -> Optional[QgsGeometry]:
    """The polygon parts of an overlay result, None when there are none."""
    if geometry is None or geometry.isEmpty():
        return None
    if geometry.type() == Qgis.GeometryType.Polygon:
        return geometry
    parts = [p for p in geometry.asGeometryCollection()
             if not p.isEmpty() and p.type() == Qgis.GeometryType.Polygon]
    return QgsGeometry.collectGeometry(parts) if parts else None


def split_parts(geometry) -> List[QgsGeometry]:
    polygonal = polygons_of(geometry)
    if polygonal is None:
        return []
    return [p for p in polygonal.asGeometryCollection() if not p.isEmpty()]


def significant_area(geometry, min_area, min_width=0.0) -> Optional[float]:
    """The area of an overlay result when it is a finding, else None. A zero area never
    counts, even with min_area 0: shapes that only touch give one. Neither does a result
    narrower than min_width everywhere."""
    if geometry is None:
        return None
    area = geometry.area()
    if area <= 0 or area < min_area:
        return None
    # shrinking by half the width leaves nothing of a sliver thinner than the width
    if min_width > 0 and geometry.buffer(-min_width / 2, 2).isEmpty():
        return None
    return area


def clean(geometry) -> Optional[QgsGeometry]:
    """A valid polygon copy for overlays; check_invalid reports the original."""
    if geometry is None or geometry.isEmpty():
        return None
    if geometry.isGeosValid():
        return geometry
    return polygons_of(geometry.makeValid())


@dataclass
class Parcel:
    label: str
    sector: Optional[str]
    geometry: QgsGeometry
    key: Optional[str] = None       # value the construcții link field refers to
    clean: Optional[QgsGeometry] = None

    def __post_init__(self):
        if self.clean is None:
            self.clean = clean(self.geometry)

    @property
    def name(self):
        return f"imobil {self.label}"


# own: index of the building's imobil or None; best: index of the imobil it covers most or
# None; cover: {imobil index: area covered}; link_found: False when its link value matches
# no imobil
Placement = namedtuple("Placement", "own best cover link_found")


@dataclass
class Building:
    label: str
    geometry: QgsGeometry
    link: Optional[str] = None      # imobil identifier from the link field
    parcel: Optional[int] = None    # index of the imobil, known from a CGXML file
    clean: Optional[QgsGeometry] = None
    sector: Optional[str] = None    # its imobil's sector, set by place_buildings
    placement: Optional[Placement] = field(default=None, repr=False)

    def __post_init__(self):
        if self.clean is None:
            self.clean = clean(self.geometry)

    @property
    def name(self):
        return f"construcție {self.label}"


@dataclass
class Finding:
    kind: str
    severity: str
    sector: str
    subject: str
    other: str
    value: Optional[float]
    unit: str
    detail: str
    geometry: QgsGeometry = field(repr=False)

    @property
    def is_area(self):
        return self.geometry.type() == Qgis.GeometryType.Polygon


def _sector_text(*sectors) -> str:
    known = sorted({s for s in sectors if s is not None}, key=lambda s: (len(s), s))
    return "/".join(known)


def _in_order(sectors) -> list:
    """The sectors by number, 2 before 10, the missing one last."""
    return sorted(sectors, key=lambda s: (s is None, len(s or ""), s or ""))


def _index(items):
    index = QgsSpatialIndex()
    for i, item in enumerate(items):
        if item.clean is not None:
            index.addFeature(i, item.clean.boundingBox())
    return index


def _prepared(geometry):
    """A geometry engine for repeated contains() and intersects() tests against geometry,
    which has to stay alive as long as the engine."""
    engine = QgsGeometry.createGeometryEngine(geometry.constGet())
    engine.prepareGeometry()
    return engine


def _labels(names, limit=8):
    shown = ", ".join(names[:limit])
    return shown + (", ..." if len(names) > limit else "")


class Coverage:
    """The imobile of a run with what the checks compute from them once: the spatial index,
    the union of each sector and of all sectors, a point inside each imobil, the imobile of
    each link value. A check given a list of imobile builds its own."""

    def __init__(self, parcels):
        self.parcels = list(parcels)
        self.index = _index(self.parcels)
        self.sectors = {}       # sector: indexes of its imobile with a geometry
        self.by_key = {}        # link value: indexes of its imobile
        for i, p in enumerate(self.parcels):
            if p.clean is not None:
                self.sectors.setdefault(p.sector, []).append(i)
            if p.key is not None:
                self.by_key.setdefault(p.key, []).append(i)
        self._unions = {}
        self._everything = None
        self._points = {}

    def union_of(self, sector) -> QgsGeometry:
        """The union of the imobile of a sector."""
        if sector not in self._unions:
            self._unions[sector] = overlay.union(
                self.parcels[i].clean for i in self.sectors.get(sector, ()))
        return self._unions[sector]

    def everything(self, stop=_never) -> Optional[QgsGeometry]:
        """The union of all imobile; None when no imobil has a geometry, and when stop()
        turns true before the union is computed."""
        if self._everything is None and self.sectors:
            for sector in self.sectors:
                if stop():
                    return None
                self.union_of(sector)
            unions = [self._unions[sector] for sector in self.sectors]
            self._everything = unions[0] if len(unions) == 1 else overlay.union(unions)
        return self._everything

    def point(self, i) -> QgsGeometry:
        """A point inside the imobil with index i."""
        if i not in self._points:
            self._points[i] = self.parcels[i].clean.pointOnSurface()
        return self._points[i]


# --- checks ---

def check_invalid(items, stop: Callable[[], bool] = _never) -> List[Finding]:
    """Polygons that are not GEOS-valid. A building's sector is known after
    place_buildings."""
    found = []
    for item in items:
        if stop():
            break
        problem = validity_problem(item.geometry)
        if problem is None:
            continue
        reason, where = problem
        found.append(Finding(
            INVALID, ERROR, _sector_text(item.sector), item.name, "", None, "",
            f"{item.name[0].upper()}{item.name[1:]}: {reason}.", where))
    return found


def check_overlaps(coverage, min_area=MIN_AREA, stop=_never, roads=ROADS,
                   min_width=SNAP_PRECISION) -> List[Finding]:
    """Pairs of imobile sharing more than min_area, wider than min_width somewhere; with a
    road of the roads sector only a warning."""
    parcels, index = coverage.parcels, coverage.index
    found = []
    for i, a in enumerate(parcels):
        if stop():
            break
        if a.clean is None:
            continue
        for j in index.intersects(a.clean.boundingBox()):
            if j <= i:
                continue
            b = parcels[j]
            if not a.clean.intersects(b.clean):
                continue
            shared = polygons_of(overlay.intersection(a.clean, b.clean))
            area = significant_area(shared, min_area, min_width)
            if area is None:
                continue
            on_road = _road(a.sector, roads) or _road(b.sector, roads)
            found.append(Finding(
                OVERLAP, WARNING if on_road else ERROR, _sector_text(a.sector, b.sector),
                a.name, b.name, round(area, 4), "mp",
                f"Imobilele {a.label} și {b.label} se suprapun pe {fmt.area(area, 3)}.",
                shared))
    return found


def _pockets(union, width=0.0):
    """The holes of a union, each as a polygon. With a width the union is first closed by it,
    so a crack narrower than the width does not join a hole to the outside."""
    if width > 0:
        union = union.buffer(width / 2, 4).buffer(-width / 2, 4)
    for part in union.constParts():
        if not isinstance(part, QgsCurvePolygon):
            continue
        for i in range(part.numInteriorRings()):
            pocket = QgsPolygon()
            pocket.setExteriorRing(part.interiorRing(i).clone())
            yield QgsGeometry(pocket)


def _bordering(coverage, geometry, reach=0.01) -> list:
    """The imobile within reach of geometry."""
    box = geometry.boundingBox()
    box.grow(reach)
    parcels = coverage.parcels
    return [parcels[k] for k in sorted(coverage.index.intersects(box))
            if parcels[k].clean is not None and parcels[k].clean.distance(geometry) <= reach]


def _uncovered_pieces(part, taken, min_area, min_width) -> list:
    """[(piece, its area)] of part minus the geometries of taken, for the pieces that
    significant_area() reports."""
    box = part.boundingBox()
    near = [geometry for geometry in taken if geometry.boundingBox().intersects(box)]
    if near:
        part = overlay.difference(part, overlay.union(near))
    found = []
    for piece in split_parts(part):
        area = significant_area(piece, min_area, min_width)
        if area is not None:
            found.append((piece, area))
    return found


def check_gaps(coverage, min_area=MIN_AREA, stop=_never, roads=ROADS,
               min_width=SNAP_PRECISION, outlines=()) -> List[Finding]:
    """Ground enclosed by imobile that no imobil of any sector covers, larger than min_area
    and wider than min_width somewhere. A hole joined to the outside only by a crack narrower
    than min_width is a hole. An enclave or a road in a hole fills it.

    A hole in the union of one sector is an error. A hole that only the imobile of several
    sectors enclose is a warning; its ground inside one of outlines, the sector outlines
    check_sector_limits checks, is left to that check. Uncovered ground at a sector's edge
    that the imobile do not enclose needs the sector outline too.
    """
    everything = coverage.everything(stop)
    if everything is None:
        return []
    sectors = [s for s in _in_order(coverage.sectors) if not _road(s, roads)]
    holes = []      # (sector, area of the sector's hole, an uncovered part of it)
    for sector in sectors:
        if stop():
            return []
        for pocket in _pockets(coverage.union_of(sector), min_width):
            size = pocket.area()
            holes += [(sector, size, part)
                      for part in split_parts(overlay.difference(pocket, everything))]
    # a sector enclosed by another lies in a hole of that other, and so do its own holes:
    # each uncovered piece is reported once, for the smallest hole around it
    kept, taken = {}, []
    for n in sorted(range(len(holes)), key=lambda n: holes[n][1]):
        kept[n] = _uncovered_pieces(holes[n][2], taken, min_area, min_width)
        taken += [piece for piece, _area in kept[n]]
    found = []
    for n, (sector, _size, _part) in enumerate(holes):
        for piece, area in kept[n]:
            around = [p.label for p in _bordering(coverage, piece)]
            if len(around) == 1:
                where = f" în interiorul imobilului {around[0]}"
            elif around:
                where = f" între imobilele {_labels(around)}"
            else:
                where = ""
            found.append(Finding(
                GAP, ERROR, _sector_text(sector), "", _labels(around, 4), round(area, 4), "mp",
                f"Gol de {fmt.area(area, 3)}{where}.", piece))
    if len(sectors) < 2 or stop():
        return found
    ground = (everything if len(sectors) == len(coverage.sectors)
              else overlay.union(coverage.union_of(s) for s in sectors))
    taken += [outline.shape for outline in outlines]
    for pocket in _pockets(ground, min_width):
        for part in split_parts(overlay.difference(pocket, everything)):
            for piece, area in _uncovered_pieces(part, taken, min_area, min_width):
                around = _bordering(coverage, piece)
                names = _in_order({p.sector for p in around
                                   if p.sector is not None and not _road(p.sector, roads)})
                labels = [p.label for p in around]
                between = f" între sectoarele {_listed(names)}" if len(names) > 1 else (
                    " între sectoare")
                if len(labels) == 1:
                    by = f", mărginit de imobilul {labels[0]}"
                elif labels:
                    by = f", mărginit de imobilele {_labels(labels)}"
                else:
                    by = ""
                found.append(Finding(
                    GAP, WARNING, _sector_text(*names), "", _labels(labels, 4), round(area, 4),
                    "mp", f"Gol de {fmt.area(area, 3)}{between}{by}.", piece))
    return found


def check_snapping(coverage, precision=SNAP_PRECISION, distance=SNAP_DISTANCE,
                   stop=_never, known_gaps=None, roads=ROADS) -> List[Finding]:
    """Vertices just short of a neighbour's boundary or just inside it.

    A vertex near a neighbour is reported only when the ground between them is uncovered or
    the vertex lies inside the neighbour; near a third imobil it is legitimate. The roads
    sector and gaps already in known_gaps are skipped.
    """
    parcels, index = coverage.parcels, coverage.index
    reported = overlay.union(known_gaps) if known_gaps else None

    def covered(point):
        probe = QgsGeometry.fromPointXY(point)
        box = QgsRectangle(point.x() - precision, point.y() - precision,
                           point.x() + precision, point.y() + precision)
        return any(parcels[k].clean is not None
                   and parcels[k].clean.distance(probe) <= precision / 10
                   for k in index.intersects(box))

    found = []
    for i, a in enumerate(parcels):
        if stop():
            break
        if _road(a.sector, roads) or a.clean is None:
            continue
        seen = set()
        for vertex in a.geometry.vertices():
            key = (round(vertex.x(), 6), round(vertex.y(), 6))
            if key in seen:
                continue
            seen.add(key)
            point = QgsPointXY(vertex.x(), vertex.y())
            box = QgsRectangle(point.x() - distance, point.y() - distance,
                               point.x() + distance, point.y() + distance)
            worst = None
            for j in index.intersects(box):
                b = parcels[j]
                if j == i or _road(b.sector, roads) or b.clean is None:
                    continue
                squared, nearest, _after, _left = b.clean.closestSegmentWithContext(point)
                if squared < 0:
                    continue
                gap = math.sqrt(squared)
                if not precision < gap <= distance:
                    continue
                inside = b.clean.contains(QgsGeometry.fromPointXY(point))
                middle = QgsPointXY((point.x() + nearest.x()) / 2, (point.y() + nearest.y()) / 2)
                if not inside and covered(middle):
                    continue
                if (not inside and reported is not None
                        and reported.distance(QgsGeometry.fromPointXY(middle)) <= precision):
                    continue
                if worst is None or gap < worst[0]:
                    worst = (gap, b, inside)
            if worst is None:
                continue
            gap, b, inside = worst
            where = "în interiorul imobilului" if inside else "de limita imobilului"
            tail = "" if inside else ", cu un gol între ele"
            found.append(Finding(
                UNSNAPPED, ERROR, _sector_text(a.sector, b.sector), a.name, b.name,
                round(gap, 4), "m",
                f"Punctul {_coords(point)} al imobilului {a.label} este la {_cm(gap)} "
                f"{where} {b.label}{tail}.",
                QgsGeometry.fromPointXY(point)))
    return found


@dataclass
class Outline:
    """A sector outline with the indexes of the imobile checked against it. sector is None
    for an outline without a sector number that took none from the imobile inside it."""
    sector: Optional[str]
    shape: QgsGeometry = field(repr=False)
    members: List[int] = field(default_factory=list)


def _voted(coverage, shape, roads, stop) -> tuple:
    """(the most frequent sector of the imobile with a point inside shape, the first by number
    on a tie, None when there is none; the indexes of the roads that fill shape).

    Roads cross the sectors and do not vote. They fill shape when no imobil of another
    sector lies there and the roads sector covers more than half of it, as it does a polygon
    of its own outline. A road in the outline of a sector that is not among the checked
    imobile does not."""
    engine = _prepared(shape)
    votes, held = defaultdict(int), []
    for i in coverage.index.intersects(shape.boundingBox()):
        if stop():
            return None, []
        sector = coverage.parcels[i].sector
        if sector is None:
            continue
        point = coverage.point(i)
        if point.isNull() or not engine.contains(point.constGet()):
            continue
        if _road(sector, roads):
            held.append(i)
        else:
            votes[sector] += 1
    if votes:
        most = max(votes.values())
        return _in_order(sector for sector in votes if votes[sector] == most)[0], []
    if held:
        covered = polygons_of(overlay.intersection(shape, coverage.union_of(roads)))
        if covered is not None and covered.area() > shape.area() / 2:
            return None, held
    return None, []


def _most_overlapped(coverage, i, outlines, index, engines) -> Optional[int]:
    """The position in outlines of the outline the imobil with index i overlaps most, the
    first of them on a tie; None when it overlaps none. index holds the outlines' bounding
    boxes, engines their prepared engines as they get made."""
    shape = coverage.parcels[i].clean
    candidates = sorted(index.intersects(shape.boundingBox()))
    for n in candidates:
        if n not in engines:
            engines[n] = _prepared(outlines[n].shape)
        if engines[n].contains(shape.constGet()):
            return n
    # an imobil that lies in an outline but for a sliver along its limit
    inner = shape.buffer(-SNAP_PRECISION, 2)
    if not inner.isEmpty() and inner.area() > shape.area() / 2:
        for n in candidates:
            if engines[n].contains(inner.constGet()):
                return n
    # more than half of the imobil in one outline settles it, and the outline around a point
    # of the imobil is the likeliest
    point = coverage.point(i)
    if not point.isNull():
        candidates.sort(key=lambda n: not engines[n].contains(point.constGet()))
    best, most = None, 0.0
    for n in candidates:
        if not engines[n].intersects(shape.constGet()):
            continue
        shared = polygons_of(overlay.intersection(shape, outlines[n].shape))
        if shared is not None and shared.area() > most:
            best, most = n, shared.area()
            if most > shape.area() / 2:
                break
    return best


def assign_outlines(coverage, limits, roads=ROADS, stop=_never, progress=None) -> List[Outline]:
    """The sector outlines of limits, [(sector or None, geometry)], each with its imobile.

    The outlines of one sector make one outline. An outline without a sector takes the most
    frequent sector of the imobile with a point inside it. The outlines without a sector
    that roads fill are the outline of the roads sector when together they hold every
    imobil of that sector. Any other outline without a sector stays on its own. The imobile
    of an outline are those of its sector. An imobil outside the roads sector whose sector
    has no outline belongs to the outline it overlaps most, when no imobil carries the
    sector of that outline. progress receives 0 to 1.
    """
    limits = list(limits)
    by_sector, alone, on_roads, held = defaultdict(list), [], [], set()
    for n, (sector, geometry) in enumerate(limits):
        if stop():
            return []
        shape = clean(geometry)
        if shape is None:
            continue
        filling = []
        if sector is None:
            sector, filling = _voted(coverage, shape, roads, stop)
        if sector is not None:
            by_sector[sector].append(shape)
        elif filling:
            on_roads.append(shape)
            held.update(filling)
        else:
            alone.append(shape)
        if progress is not None:
            progress(0.5 * (n + 1) / len(limits))
    # outlines that hold some of the roads are not known to be the outline of the roads
    # sector: checked against them, every other road would lie outside its sector
    if on_roads and len(held) == len(coverage.sectors[roads]):
        by_sector[roads] += on_roads
    else:
        alone += on_roads
    outlines = []
    for sector in _in_order(by_sector):
        shapes = by_sector[sector]
        outlines.append(Outline(sector, shapes[0] if len(shapes) == 1 else overlay.union(shapes),
                                list(coverage.sectors.get(sector, ()))))
    outlines += [Outline(None, shape) for shape in alone]

    # by position: the imobile whose sector has no outline, into the outlines whose sector no
    # imobil carries
    unclaimed = {n for n, outline in enumerate(outlines) if not outline.members}
    named = {outline.sector for outline in outlines if outline.sector is not None}
    unplaced = sorted(i for sector, members in coverage.sectors.items()
                      if sector not in named and not _road(sector, roads) for i in members)
    if unclaimed and unplaced:
        index, engines = QgsSpatialIndex(), {}
        for n, outline in enumerate(outlines):
            index.addFeature(n, outline.shape.boundingBox())
        for done, i in enumerate(unplaced):
            if stop():
                return []
            best = _most_overlapped(coverage, i, outlines, index, engines)
            if best in unclaimed:
                outlines[best].members.append(i)
            if progress is not None and done % 200 == 0:
                progress(0.5 + 0.5 * done / len(unplaced))
    return outlines


def check_sector_limits(coverage, outlines, known_gaps, min_area=MIN_AREA, stop=_never,
                        min_width=SNAP_PRECISION, progress=None) -> List[Finding]:
    """Uncovered ground inside each sector outline and imobile that extend outside their
    sector, both larger than min_area and wider than min_width somewhere.

    outlines are the ones assign_outlines() made; an outline without imobile is not checked.
    known_gaps are the geometries of the gaps check_gaps found with these outlines, which
    are not reported again as uncovered ground. progress receives 0 to 1.
    """
    checked = [outline for outline in outlines if outline.members]
    if not checked or stop():
        return []
    parcels = coverage.parcels
    total, done = sum(len(outline.members) for outline in checked), 0
    found = []
    for outline in checked:
        if stop():
            break
        sector = outline.sector
        bare = outline.shape
        near = {parcels[i].sector for i in coverage.index.intersects(bare.boundingBox())}
        # the outline's own sector first: it leaves little ground for the others
        for other in sorted(_in_order(near), key=lambda s: s != sector):
            if bare.isEmpty() or stop():
                break
            bare = overlay.difference(bare, coverage.union_of(other))
        where = f"din sectorul {sector}" if sector is not None else "din limita sectorului"
        for part, area in _uncovered_pieces(bare, known_gaps, min_area, min_width):
            found.append(Finding(
                UNCOVERED, ERROR, _sector_text(sector), "", "", round(area, 4), "mp",
                f"Suprafață de {fmt.area(area, 3)} {where}, neacoperită de niciun imobil.",
                part))
        limit = f"limita sectorului {sector}" if sector is not None else "limita sectorului"
        engine = _prepared(outline.shape)
        for i in outline.members:
            if stop():
                break
            done += 1
            if progress is not None and done % 100 == 0:
                progress(done / total)
            p = parcels[i]
            if engine.contains(p.clean.constGet()):
                continue
            # moved inward by half the width and inside the outline: what the imobil has
            # outside is narrower than the width
            inner = p.clean.buffer(-min_width / 2, 2) if min_width > 0 else None
            if inner is not None and not inner.isEmpty() and engine.contains(inner.constGet()):
                continue
            out = polygons_of(overlay.difference(p.clean, outline.shape))
            area = significant_area(out, min_area, min_width)
            if area is not None:
                found.append(Finding(
                    OUTSIDE_SECTOR, ERROR, _sector_text(sector), p.name, "", round(area, 4),
                    "mp", f"Imobilul {p.label} iese din {limit} cu {fmt.area(area, 3)}.", out))
        if progress is not None:
            progress(done / total)
    return found


def unmatched_sectors(parcels, limits) -> List[str]:
    """The sectors of the outlines in limits ([(sector or None, geometry)]) that no imobil
    carries."""
    carried = {p.sector for p in parcels}
    return sorted({sector for sector, _geometry in limits
                   if sector is not None and sector not in carried},
                  key=lambda s: (len(s), s))


def placement(building, coverage) -> Placement:
    """The imobil of a building with a valid geometry, from its CGXML file, else its link
    value, else the largest overlap, and the imobile it covers. Verificare Topologică and
    Topologie Construcții both place a construcție with it."""
    parcels = coverage.parcels
    cover = {}
    for j in coverage.index.intersects(building.clean.boundingBox()):
        if parcels[j].clean is not None and parcels[j].clean.intersects(building.clean):
            shared = polygons_of(overlay.intersection(parcels[j].clean, building.clean))
            if shared is not None and shared.area() > 0:
                cover[j] = shared.area()
    own, link_found = building.parcel, True
    if own is None and building.link is not None:
        matches = coverage.by_key.get(building.link, [])
        if matches:
            own = max(matches, key=lambda j: cover.get(j, 0.0))
        else:
            link_found = False
    if own is None and cover:
        own = max(cover, key=lambda j: cover[j])
    best = max(cover, key=lambda j: cover[j]) if cover else None
    return Placement(own, best, cover, link_found)


def farthest_outside(geometry, parcel) -> tuple:
    """(how far the farthest point of geometry lies outside the polygon parcel, that point as
    a geometry), (0.0, None) when no point lies outside. A point nearer than the overlay grid
    is on the limit: a point computed on a limit misses it by less."""
    worst, where = 0.0, None
    for vertex in geometry.vertices():
        probe = QgsGeometry.fromPointXY(QgsPointXY(vertex.x(), vertex.y()))
        if parcel.intersects(probe):
            continue
        gap = parcel.distance(probe)
        if gap > max(worst, overlay.GRID):
            worst, where = gap, probe
    return worst, where


# how a construcție lies outside its imobil: distance and point as farthest_outside() gives
# them, ground the part of it outside the imobil, None without any, and the area of that part
Outside = namedtuple("Outside", "distance point ground area")


def outside(geometry, parcel, tolerance, valid=None) -> Optional[Outside]:
    """How the construcție geometry lies outside the polygon parcel, None when it does not.
    Verificare Topologică reports a construcție by this rule and Topologie Construcții cuts
    it by the same.

    It lies outside when one of its points is more than tolerance outside the imobil, the
    test Generare CG makes, or when its ground outside the imobil is wider than tolerance
    somewhere: a wall across a notch of the limit puts ground there and no point. Ground
    that shrinking by half the tolerance removes is within the tolerance, and a strip
    narrower than two steps of the overlay grid comes from the grid. valid is the valid copy
    of an invalid geometry, for the overlay.
    """
    if geometry.within(parcel):
        return None
    distance, point = farthest_outside(geometry, parcel)
    ground = polygons_of(overlay.difference(geometry if valid is None else valid, parcel))
    if distance <= tolerance and (
            ground is None or ground.buffer(-max(tolerance / 2, overlay.GRID), 2).isEmpty()):
        return None
    return Outside(distance, point, ground, ground.area() if ground is not None else 0.0)


def place_buildings(buildings, coverage, stop=_never):
    """Give each building its Placement and the sector of its imobil; check_buildings reuses
    them."""
    for b in buildings:
        if stop():
            break
        if b.clean is None or b.placement is not None:
            continue
        b.placement = placement(b, coverage)
        own = b.placement.own
        if b.sector is None and own is not None:
            b.sector = coverage.parcels[own].sector


def check_buildings(buildings, coverage, tolerance=BUILDING_TOLERANCE,
                    min_area=MIN_AREA, stop=_never, min_width=SNAP_PRECISION) -> List[Finding]:
    """Construcții outside their imobil, on another imobil, on none, or overlapping.

    The imobil comes from the CGXML file, else the link value, else the largest overlap. A
    construcție is outside its imobil by the rule of outside().
    """
    parcels = coverage.parcels
    place_buildings(buildings, coverage, stop)
    found = []
    for b in buildings:
        if stop():
            break
        if b.clean is None or b.placement is None:
            continue
        own, best, cover, link_found = b.placement
        if not link_found:
            used = " S-a folosit imobilul pe care se află." if cover else ""
            found.append(Finding(
                LINK_MISSING, WARNING, _sector_text(b.sector), b.name, "", None, "",
                f"Construcția {b.label} indică imobilul '{b.link}', care nu există "
                f"în datele verificate.{used}", b.clean.pointOnSurface()))
        if not cover:
            detail = f"Construcția {b.label} nu se află pe niciun imobil."
            if own is not None:
                detail = (f"Construcția {b.label} aparține imobilului {parcels[own].label}, dar "
                          "nu se află pe niciun imobil.")
            found.append(Finding(
                BUILDING_ALONE, ERROR, _sector_text(b.sector), b.name, "",
                round(b.clean.area(), 4), "mp", detail, b.clean))
            continue
        parcel = parcels[own]
        if parcel.clean is None:
            continue
        if best != own and cover[best] > cover.get(own, 0.0):
            other = parcels[best]
            on_other = polygons_of(overlay.intersection(other.clean, b.clean))
            found.append(Finding(
                BUILDING_ELSEWHERE, ERROR, _sector_text(parcel.sector), b.name, other.name,
                round(cover[best], 4), "mp",
                f"Construcția {b.label} aparține imobilului {parcel.label}, dar stă în cea "
                f"mai mare parte pe imobilul {other.label} ({fmt.area(cover[best], 2)} din "
                f"{fmt.area(b.clean.area(), 2)}).", on_other or b.clean))
            continue
        out = outside(b.geometry, parcel.clean, tolerance, b.clean)
        if out is None:
            continue
        # the imobil the outside part lies on most, if any
        neighbours = [j for j in cover if j != own]
        other = parcels[max(neighbours, key=lambda j: cover[j])].name if neighbours else ""
        if out.distance > tolerance:
            value, unit = round(out.distance, 4), "m"
            detail = (f"Un punct al construcției {b.label} iese din imobilul {parcel.label} cu "
                      f"{_cm(out.distance)} ({fmt.area(out.area, 3)} în afară).")
        else:
            # ground outside the imobil and no point there: there is no distance to give
            value, unit = round(out.area, 4), "mp"
            detail = (f"O latură a construcției {b.label} iese din imobilul {parcel.label} "
                      f"({fmt.area(out.area, 3)} în afară).")
        found.append(Finding(
            BUILDING_OUTSIDE, ERROR, _sector_text(parcel.sector), b.name, other, value, unit,
            detail, out.point if out.point is not None and out.area <= 1e-6 else out.ground))

    bindex = _index(buildings)
    for i, a in enumerate(buildings):
        if stop():
            break
        if a.clean is None:
            continue
        for j in bindex.intersects(a.clean.boundingBox()):
            if j <= i:
                continue
            b = buildings[j]
            if not a.clean.intersects(b.clean):
                continue
            shared = polygons_of(overlay.intersection(a.clean, b.clean))
            area = significant_area(shared, min_area, min_width)
            if area is not None:
                found.append(Finding(
                    BUILDING_OVERLAP, ERROR, _sector_text(a.sector, b.sector), a.name, b.name,
                    round(area, 4), "mp",
                    f"Construcțiile {a.label} și {b.label} se suprapun pe "
                    f"{fmt.area(area, 3)}.", shared))
    return found


# --- inputs ---

def unnamed(snapshot, feature) -> str:
    """The name in the results of a feature without an ID: its layer and row number."""
    return f"{snapshot.name} #{feature.id()}"


def parcels_from_layers(snapshots, id_field=None, sector_field=None, empty=None):
    """Parcels from snapshots, labelled "<layer> #<fid>" without an id_field. empty, a list,
    receives the label of each feature without geometry, which is not checked."""
    parcels = []
    for snapshot in snapshots:
        id_index = snapshot.fields.lookupField(id_field) if id_field else -1
        sector_index = snapshot.fields.lookupField(sector_field) if sector_field else -1
        for feature, geometry in snapshot.features(with_empty=True):
            values = feature.attributes()
            key = normalize_key(values[id_index]) if id_index >= 0 else None
            label = key or unnamed(snapshot, feature)
            if geometry is None:
                if empty is not None:
                    empty.append(label)
                continue
            sector = normalize_sector(values[sector_index]) if sector_index >= 0 else None
            parcels.append(Parcel(label, sector, QgsGeometry(geometry), key=key))
    return parcels


def buildings_from_layers(snapshots, link_field=None, number_field=None, empty=None):
    """Buildings labelled "<imobil> <number>" like in a CGXML file when both columns are
    filled, else "<layer> #<fid>", with the number in brackets when known. empty, a list,
    receives the label of each feature without geometry, which is not checked."""
    buildings = []
    for snapshot in snapshots:
        link_index = snapshot.fields.lookupField(link_field) if link_field else -1
        number_index = snapshot.fields.lookupField(number_field) if number_field else -1
        for feature, geometry in snapshot.features(with_empty=True):
            values = feature.attributes()
            link = normalize_key(values[link_index]) if link_index >= 0 else None
            number = normalize_key(values[number_index]) if number_index >= 0 else None
            if link and number:
                label = f"{link} {number}"
            elif number:
                label = f"{unnamed(snapshot, feature)} ({number})"
            else:
                label = unnamed(snapshot, feature)
            if geometry is None:
                if empty is not None:
                    empty.append(label)
                continue
            buildings.append(Building(label, QgsGeometry(geometry), link=link))
    return buildings


# a CGXML file read by from_cgxml: key is _key_of() of its imobil, label the imobil's name in
# the results, buildings [(nr_constr, geometry)] of its construcții with points and unbuilt
# the number of those without
_ReadFile = namedtuple("_ReadFile",
                       "source key uat label sector geometry buildings unbuilt")


def _key_of(record) -> Optional[str]:
    """The key of a CGXML imobil: its ID, else "IE <its IE>", else None."""
    if record.get("id") is not None:
        return str(record["id"])
    if record.get("ie") is not None:
        return f"IE {record['ie']}"
    return None


def _depth(source) -> int:
    """How many folders or archives a list_sources() entry lies below its chosen source."""
    return source[0].replace("\\", "/").count("/")


def _file_names(sources) -> dict:
    """{list_sources() entry: the name of its file in the results}: its display name, or its
    whole path when two chosen sources of the same name give it the display name of another
    file."""
    counts = defaultdict(int)
    for source in sources:
        counts[source[0]] += 1
    names = {}
    for source in sources:
        display, path, member = source
        if counts[display] > 1:
            display = os.path.join(path, *member.split("/")) if member else path
        names[source] = display
    return names


def _same_place(a, b) -> bool:
    """Whether two outlines overlap by at least half of the smaller one."""
    a, b = clean(a), clean(b)
    if a is None or b is None:
        return False
    shared = polygons_of(overlay.intersection(a, b))
    smaller = min(a.area(), b.area())
    return shared is not None and smaller > 0 and shared.area() >= smaller / 2


def _versions(read, found) -> list:
    """The files found (indexes into read) grouped by imobil: [[files of one imobil]], each
    with the file fewest folders below the chosen sources first (the first listed of those).
    A file joins the first imobil whose first file it overlaps by at least half of the
    smaller outline."""
    imobile = []
    for n in sorted(found, key=lambda n: (_depth(read[n].source), n)):
        same = next((files for files in imobile
                     if _same_place(read[files[0]].geometry, read[n].geometry)), None)
        if same is None:
            imobile.append([n])
        else:
            same.append(n)
    return imobile


def from_cgxml(paths, recursive, stop=_never, progress=None):
    """(parcels, buildings, errors, skipped, duplicates, reused_keys) from CGXML sources.
    skipped counts the records without coordinates.

    Files of one imobil hold, within one UAT, the same ID, else the same IE, else the same file
    name, and their outlines overlap by at least half of the smaller one, like two versions of
    a delivery. The imobil is checked once, from the file fewest folders below the chosen
    sources (the first listed of those); its other files and their construcții are left out.
    duplicates is [(imobil, [its files, the checked one first])].

    Files with the same ID or IE whose outlines do not overlap that much hold different
    imobile, and each is checked. reused_keys is [(imobil, [those files])].
    """
    sources = cgxml_sources.list_sources(paths, recursive)
    names = _file_names(sources)
    errors, read = [], []
    skipped = 0
    for source, parsed, problem in cgxml_sources.read_each(sources, stop, progress):
        if problem is not None:
            errors.append((names[source], problem))
            continue
        imobile, constructii, _warnings = parsed
        record = imobile[0]
        geometry = cgxml_export.build_geometry(record.get("rings"))
        if geometry is None:
            skipped += 1 + len(constructii)
            continue
        key = _key_of(record)
        shapes = [(c.get("nr_constr"), cgxml_export.build_geometry(c.get("rings")))
                  for c in constructii]
        read.append(_ReadFile(
            source, key, record.get("uat_siruta") or None,
            key if key is not None else names[source], normalize_sector(record.get("sector")),
            geometry, [(number, shape) for number, shape in shapes if shape is not None],
            sum(1 for _number, shape in shapes if shape is None)))

    files_of = defaultdict(list)
    for n, file in enumerate(read):
        if file.key is not None:
            files_of[(file.uat, file.key)].append(n)
        else:
            files_of[(file.uat, None, cgxml_sources.file_name(file.source))].append(n)
    left_out, duplicates, reused_keys = set(), [], []
    for (_uat, key, *_name), found in files_of.items():
        if len(found) < 2:
            continue
        imobile = _versions(read, found)
        for files in imobile:
            left_out.update(files[1:])
            if len(files) > 1:
                duplicates.append((read[files[0]].label,
                                   [names[read[n].source] for n in files]))
        if key is not None and len(imobile) > 1:
            reused_keys.append((read[found[0]].label,
                                [names[read[files[0]].source] for files in imobile]))

    parcels, buildings = [], []
    for n, file in enumerate(read):
        if n in left_out:
            continue
        parcels.append(Parcel(file.label, file.sector, file.geometry, key=file.key))
        own = len(parcels) - 1
        skipped += file.unbuilt
        for number, shape in file.buildings:
            buildings.append(Building(f"{file.label} {number or ''}".strip(), shape,
                                      parcel=own, sector=file.sector))
    return parcels, buildings, errors, skipped, duplicates, reused_keys


# --- report ---

def counts_by_kind(findings):
    counts = {kind: 0 for kind in KINDS}
    for f in findings:
        counts[f.kind] += 1
    return counts


# (layer field, Finding attribute)
COLUMNS = (("tip", "kind"), ("gravitate", "severity"), ("sector", "sector"),
           ("obiect", "subject"), ("vecin", "other"), ("valoare", "value"),
           ("unitate", "unit"), ("detalii", "detail"))


def findings_fields() -> QgsFields:
    fields = QgsFields()
    for name, _attr in COLUMNS:
        if name == "valoare":
            fields.append(QgsField(name, FIELD_DOUBLE, "double", 20, 4))
        else:
            fields.append(QgsField(name, FIELD_STRING))
    return fields


def findings_features(findings, fields, wkb):
    """The findings as features; polygons become multipolygons for a MultiPolygon layer."""
    features = []
    for finding in findings:
        feature = QgsFeature(fields)
        geometry = QgsGeometry(finding.geometry)
        if wkb == Qgis.WkbType.MultiPolygon and not geometry.isMultipart():
            geometry = geometry.coerceToType(wkb)[0]
        feature.setGeometry(geometry)
        feature.setAttributes([getattr(finding, attr) for _name, attr in COLUMNS])
        features.append(feature)
    return features


def write_excel(path, findings, facts):
    wb = xlsx_report.new_workbook()
    summary = xlsx_report.Summary(wb, "Verificare Topologică")
    summary.section("Informații")
    summary.facts(facts)
    summary.section("Rezultat")
    # one row per kind and severity: an overlap with a road is a warning, the others errors
    counts = defaultdict(int)
    for f in findings:
        counts[(f.kind, f.severity)] += 1
    rows = [[kind, severity, counts[(kind, severity)]]
            for kind in KINDS for severity in (ERROR, WARNING) if counts[(kind, severity)]]
    if rows:
        summary.table(["Tip", "Gravitate", "Număr"], rows,
                      row_colors=[xlsx_report.BAD if r[1] == ERROR else xlsx_report.MIXED
                                  for r in rows])
    else:
        summary.note("Nu s-a găsit nicio problemă.")
    headers = ["Tip", "Gravitate", "Sector", "Obiect", "Vecin", "Valoare", "Unitate",
               "Detalii", "X", "Y"]
    table = []
    colors = []
    for f in findings:
        anchor = f.geometry.pointOnSurface().asPoint() if f.is_area else f.geometry.asPoint()
        table.append([f.kind, f.severity, f.sector, f.subject, f.other, f.value, f.unit,
                      f.detail, round(anchor.x(), 3), round(anchor.y(), 3)])
        colors.append(xlsx_report.BAD if f.severity == ERROR else xlsx_report.MIXED)
    xlsx_report.write_table(wb, "Probleme", headers, table, row_colors=colors,
                            text_columns=["Sector", "Obiect", "Vecin"])
    xlsx_report.save(wb, path)


# --- the task ---

class TopologyCheckTask(CadexTask):
    """The checks as a background task, on layer snapshots or CGXML paths. Findings are in
    Stereo 70. facts are the Excel Sumar's lines on the sources; the task adds the
    tolerances and the counts. With stop_if_nothing_read, a run that has no imobil to check
    fails before any check and before the report is written: CGXML paths where no imobil
    with coordinates could be read, imobile layers without a feature with geometry."""

    def __init__(self, *, imobile=None, constructii=None, id_field=None, sector_field=None,
                 link_field=None, number_field=None, cgxml_paths=None, recursive=True,
                 limits=None,
                 limit_field=None, min_area=MIN_AREA, snap_precision=SNAP_PRECISION,
                 snap_distance=SNAP_DISTANCE, building_tolerance=BUILDING_TOLERANCE,
                 roads_sector=ROADS, excel_path=None, facts=None, finished_cb=None,
                 stop_if_nothing_read=False):
        super().__init__("CADex: Verificare Topologică", finished_cb)
        self.stop_if_nothing_read = stop_if_nothing_read
        self.imobile = imobile or []
        self.constructii = constructii or []
        self.id_field = id_field
        self.sector_field = sector_field
        self.link_field = link_field
        self.number_field = number_field
        self.cgxml_paths = (cgxml_sources.split_paths(cgxml_paths)
                            if isinstance(cgxml_paths, str) else list(cgxml_paths or []))
        self.recursive = recursive
        self.limits = limits
        self.limit_field = limit_field
        self.min_area = min_area
        self.snap_precision = snap_precision
        self.snap_distance = snap_distance
        self.building_tolerance = building_tolerance
        self.roads = normalize_sector(roads_sector)
        self.excel_path = excel_path
        self.facts = list(facts or [])

        self.crs = stereo70.crs()
        self.findings = []
        self.read_errors = []
        self.skipped = 0
        self.duplicates = []            # (imobil, [files, the checked one first])
        self.reused_keys = []           # (imobil, [files of different imobile, each checked])
        self.unmatched_sectors = []     # outline sectors no imobil carries
        self.empty_outlines = 0         # outlines without a sector and without imobile
        self.parcel_count = 0
        self.building_count = 0
        self.sector_count = 0

    def snapshots(self):
        return self.imobile + self.constructii + ([self.limits] if self.limits else [])

    def run(self):
        try:
            if self.cgxml_paths:
                (parcels, buildings, self.read_errors, self.skipped, self.duplicates,
                 self.reused_keys) = from_cgxml(self.cgxml_paths, self.recursive, self.stopped,
                                                lambda share: self.report(30.0 * share))
                if self.stop_if_nothing_read and not parcels:
                    self.error_msg = self._no_imobil_read()
                    return False
            else:
                empty = []
                parcels = parcels_from_layers(self.imobile, self.id_field, self.sector_field,
                                              empty)
                buildings = buildings_from_layers(self.constructii, self.link_field,
                                                  self.number_field, empty)
                self.skipped = len(empty)
                if self.stop_if_nothing_read and not parcels:
                    self.error_msg = self._no_imobil()
                    return False
            if self.stopped():
                return False
            self.parcel_count = len(parcels)
            self.building_count = len(buildings)
            self.sector_count = len({p.sector for p in parcels
                                     if p.sector is not None and not _road(p.sector, self.roads)})
            self.report(30)

            stop = self.stopped
            coverage = Coverage(parcels)
            self._add(check_invalid(parcels, stop), 1)
            place_buildings(buildings, coverage, stop)
            self._add(check_invalid(buildings, stop), 2)
            width = self.snap_precision
            self._add(check_overlaps(coverage, self.min_area, stop, roads=self.roads,
                                     min_width=width), 3)
            limits = self._limit_outlines()
            self.unmatched_sectors = unmatched_sectors(parcels, limits)
            outlines = assign_outlines(coverage, limits, self.roads, stop,
                                       lambda share: self._within(3, share / 2))
            self.empty_outlines = sum(1 for outline in outlines
                                      if outline.sector is None and not outline.members)
            gaps = check_gaps(coverage, self.min_area, stop, roads=self.roads, min_width=width,
                              outlines=[outline for outline in outlines if outline.members])
            self._add(gaps, 4)
            known = [gap.geometry for gap in gaps]
            # a point within the snapping distance of its neighbour is an error also where
            # the gap between them, lying between sectors, is only a warning
            errors = [gap.geometry for gap in gaps if gap.severity == ERROR]
            self._add(check_snapping(coverage, self.snap_precision, self.snap_distance, stop,
                                     errors, roads=self.roads), 5)
            self._add(check_sector_limits(coverage, outlines, known, self.min_area, stop,
                                          min_width=width,
                                          progress=lambda share: self._within(5, share)), 6)
            self._add(check_buildings(buildings, coverage, self.building_tolerance,
                                      self.min_area, stop, min_width=width), 7)
            if stop():
                return False

            if self.excel_path:
                self.write_report(lambda: write_excel(self.excel_path, self.findings,
                                                      self.report_facts()), self.excel_path)
            self.report(100)
            return True
        except Exception as e:
            return self.failed("Verificare Topologică", e)

    def report_facts(self):
        """The Excel Sumar's lines: the caller's facts, the tolerances, the counts."""
        def metres(value):
            return f"{fmt.number(value, 3)} m"
        facts = list(self.facts) + [
            ("Suprafață minimă", fmt.area(self.min_area, 3)),
            ("Toleranță de racordare", metres(self.snap_precision)),
            ("Distanță de racordare", metres(self.snap_distance)),
            ("Cât poate ieși construcția din imobil", metres(self.building_tolerance)),
            ("Sectorul drumurilor", self.roads or "-"),
        ]
        if self.limits is not None:
            facts.append(("Limitele sectoarelor", self.limits.name))
        return facts + [
            ("Imobile verificate", self.parcel_count),
            ("Construcții verificate", self.building_count),
            ("Sectoare", self.sector_count),
            ("Sistem de coordonate", stereo70.NAME),
        ]

    def notes(self) -> List[str]:
        """Romanian sentences on what the run left out or compared differently: features or
        records without geometry, imobile found in several files, IDs or IEs shared by
        different imobile, outline sectors that no imobil carries, outlines without a sector
        that hold no imobil."""
        notes = []
        if self.skipped and self.cgxml_paths:
            notes.append(fmt.plural(
                self.skipped, "{0} fără coordonate nu a fost verificată.",
                "{0} fără coordonate nu au fost verificate.").format(
                    fmt.count(self.skipped, "înregistrare", "înregistrări")))
        elif self.skipped:
            notes.append(fmt.plural(
                self.skipped, "{0} fără geometrie nu a fost verificat.",
                "{0} fără geometrie nu au fost verificate.").format(
                    fmt.count(self.skipped, "element", "elemente")))
        if self.duplicates:
            names = [imobil for imobil, _files in self.duplicates]
            notes.append(fmt.plural(
                len(names),
                "{0} apare în mai multe fișiere: {1}. S-a verificat dintr-un singur fișier.",
                "{0} apar în mai multe fișiere: {1}. Fiecare s-a verificat dintr-un singur "
                "fișier.").format(fmt.count(len(names), "imobil", "imobile"), _labels(names)))
        if self.reused_keys:
            names = [imobil for imobil, _files in self.reused_keys]
            notes.append(fmt.plural(
                len(names), "{0} apare în mai multe fișiere, în locuri diferite: {1}.",
                "{0} apar în mai multe fișiere, în locuri diferite: {1}.").format(
                    fmt.count(len(names), "imobil", "imobile"), _labels(names))
                + " Fiecare fișier s-a verificat ca imobil separat.")
        if self.unmatched_sectors:
            notes.append(fmt.plural(
                len(self.unmatched_sectors), "Sectorul {0} din limite nu apare la niciun imobil.",
                "Sectoarele {0} din limite nu apar la niciun imobil.").format(
                    ", ".join(self.unmatched_sectors)))
        if self.empty_outlines:
            notes.append(fmt.plural(
                self.empty_outlines,
                "O limită de sector nu cuprinde niciun imobil și nu a fost verificată.",
                "{0} nu cuprind niciun imobil și nu au fost verificate.").format(
                    fmt.count(self.empty_outlines, "limită de sector", "limite de sector")))
        return notes

    def _no_imobil_read(self) -> str:
        """The reason a run on CGXML sources without an imobil to check stops."""
        # every file read gives an imobil or a record without coordinates
        if self.read_errors and not self.skipped:
            return cgxml_export.nothing_read(self.read_errors)
        reason = fmt.plural(
            len(self.cgxml_paths),
            "Niciun imobil nu a putut fi verificat: sursa aleasă nu are niciun imobil cu "
            "coordonate.",
            "Niciun imobil nu a putut fi verificat: sursele alese nu au niciun imobil cu "
            "coordonate.")
        if self.read_errors:
            reason += " " + fmt.plural(
                len(self.read_errors), "{0} nu a putut fi citit:",
                "{0} nu au putut fi citite:").format(
                    fmt.count(len(self.read_errors), "fișier", "fișiere"))
            reason += "\n\n" + cgxml_export.problem_lines(self.read_errors)
        return reason

    def _no_imobil(self) -> str:
        """The reason a run on layers without an imobil to check stops."""
        names = ", ".join(f"'{snapshot.name}'" for snapshot in self.imobile)
        return fmt.plural(
            len(self.imobile),
            "Niciun imobil nu a putut fi verificat: layerul {0} nu are niciun element cu "
            "geometrie.",
            "Niciun imobil nu a putut fi verificat: layerele {0} nu au niciun element cu "
            "geometrie.").format(names)

    STEPS = 7

    def _add(self, findings, step):
        self.findings.extend(findings)
        self._within(step, 0.0)

    def _within(self, step, share):
        """Report the progress share, 0 to 1, into the check that follows step."""
        self.report(30.0 + 65.0 * (step + share) / self.STEPS)

    def _limit_outlines(self):
        outlines = []
        if self.limits is None:
            return outlines
        index = self.limits.fields.indexOf(self.limit_field) if self.limit_field else -1
        for feature, geometry in self.limits.features():
            sector = normalize_sector(feature.attributes()[index]) if index >= 0 else None
            outlines.append((sector, QgsGeometry(geometry)))
        return outlines
