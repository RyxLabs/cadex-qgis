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
# defaults:
# - a vertex and the segment it should lie on, both rounded to the millimetre, can end up
#   1.4 mm apart, so 2 mm still counts as snapped
# - a vertex 2 mm to 50 cm short of a neighbour leaves an open sliver no area check finds
# - overlaps under 0.01 mp come from coordinate rounding
# - so do overlaps and gaps narrower than the snapping precision, whatever their area: 1 mm
#   along a 100 m boundary is 0.1 mp
# - a construcție vertex more than 1 cm outside its imobil is an error, not rounding

import math
import os
from collections import defaultdict, namedtuple
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from qgis.core import (  # type: ignore
    Qgis, QgsCurvePolygon, QgsFeature, QgsField, QgsFields, QgsGeometry, QgsPointXY,
    QgsPolygon, QgsRectangle, QgsSpatialIndex,
)

from . import cgxml_export, cgxml_sources, fmt, stereo70, xlsx_report
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
    return f"{fmt.number(meters * 100, 1)} cm"


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


def _index(items):
    index = QgsSpatialIndex()
    for i, item in enumerate(items):
        if item.clean is not None:
            index.addFeature(i, item.clean.boundingBox())
    return index


def _labels(names, limit=8):
    shown = ", ".join(names[:limit])
    return shown + (", ..." if len(names) > limit else "")


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


def check_overlaps(parcels, min_area=MIN_AREA, stop=_never, roads=ROADS,
                   min_width=SNAP_PRECISION) -> List[Finding]:
    """Pairs of imobile sharing more than min_area, wider than min_width somewhere; with a
    road of the roads sector only a warning."""
    index = _index(parcels)
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
            shared = polygons_of(a.clean.intersection(b.clean))
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


def _bordering(parcels, index, geometry, reach=0.01):
    box = geometry.boundingBox()
    box.grow(reach)
    return [parcels[k].label for k in sorted(index.intersects(box))
            if parcels[k].clean is not None and parcels[k].clean.distance(geometry) <= reach]


def check_gaps(parcels, min_area=MIN_AREA, stop=_never, roads=ROADS,
               min_width=SNAP_PRECISION) -> List[Finding]:
    """Holes in a sector's union that no imobil of any sector covers, larger than min_area
    and wider than min_width somewhere. A hole joined to the outside only by a crack narrower
    than min_width is a hole.

    An enclave or a road crossing the sector fills its hole. Uncovered ground at a sector's
    edge needs the sector outline and is found by check_sector_limits.
    """
    valid = [p for p in parcels if p.clean is not None]
    if not valid:
        return []
    everything = QgsGeometry.unaryUnion([p.clean for p in valid])
    index = _index(parcels)
    groups = defaultdict(list)
    for p in valid:
        if not _road(p.sector, roads):
            groups[p.sector].append(p)
    found = []
    for sector in sorted(groups, key=lambda s: (s is None, len(s or ""), s or "")):
        if stop():
            break
        union = QgsGeometry.unaryUnion([p.clean for p in groups[sector]])
        for pocket in _pockets(union, min_width):
            for part in split_parts(pocket.difference(everything)):
                area = significant_area(part, min_area, min_width)
                if area is None:
                    continue
                around = _bordering(parcels, index, part)
                if len(around) == 1:
                    where = f" în interiorul imobilului {around[0]}"
                elif around:
                    where = f" între imobilele {_labels(around)}"
                else:
                    where = ""
                found.append(Finding(
                    GAP, ERROR, _sector_text(sector), "", _labels(around, 4), round(area, 4), "mp",
                    f"Gol de {fmt.area(area, 3)}{where}.", part))
    return found


def check_snapping(parcels, precision=SNAP_PRECISION, distance=SNAP_DISTANCE,
                   stop=_never, known_gaps=None, roads=ROADS) -> List[Finding]:
    """Vertices just short of a neighbour's boundary or just inside it.

    A vertex near a neighbour is reported only when the ground between them is uncovered or
    the vertex lies inside the neighbour; near a third imobil it is legitimate. The roads
    sector and gaps already in known_gaps are skipped.
    """
    index = _index(parcels)
    reported = QgsGeometry.unaryUnion(known_gaps) if known_gaps else None

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


def check_sector_limits(parcels, limits, min_area=MIN_AREA, stop=_never,
                        roads=ROADS, min_width=SNAP_PRECISION) -> List[Finding]:
    """Uncovered ground inside each sector outline and imobile that extend outside their
    sector, both larger than min_area and wider than min_width somewhere.

    limits is [(sector or None, geometry)]; an outline without a sector takes the most
    common sector of the imobile inside it. The imobile of an outline are those of its
    sector; when none carries that sector, those outside the roads sector whose
    pointOnSurface lies inside it, or on its edge when it is the outline they overlap most.
    """
    valid = [p for p in parcels if p.clean is not None]
    outlines = defaultdict(list)
    for sector, geometry in limits:
        outline = clean(geometry)
        if outline is None:
            continue
        if sector is None:
            votes = defaultdict(int)
            for p in valid:
                if (p.sector is not None and not _road(p.sector, roads)
                        and outline.contains(p.clean.pointOnSurface())):
                    votes[p.sector] += 1
            sector = max(votes, key=lambda s: votes[s]) if votes else None
        outlines[sector].append(outline)
    if not outlines:
        return []
    everything = QgsGeometry.unaryUnion([p.clean for p in valid]) if valid else None
    order = sorted(outlines, key=lambda s: (s is None, len(s or ""), s or ""))
    shapes = {sector: QgsGeometry.unaryUnion(outlines[sector]) for sector in order}
    members = {sector: [p for p in valid if sector is not None and p.sector == sector]
               for sector in order}
    members.update(_members_by_place(valid, shapes, [s for s in order if not members[s]],
                                     roads))

    found = []
    for sector in order:
        if stop():
            break
        outline = shapes[sector]
        bare = outline if everything is None else outline.difference(everything)
        # holes inside the sector's own imobile are reported by check_gaps
        if members[sector] and not _road(sector, roads):
            holes = list(_pockets(QgsGeometry.unaryUnion([p.clean for p in members[sector]]),
                                  min_width))
            if holes:
                bare = bare.difference(QgsGeometry.unaryUnion(holes))
        where = f"din sectorul {sector}" if sector is not None else "din limita sectorului"
        for part in split_parts(bare):
            area = significant_area(part, min_area, min_width)
            if area is not None:
                found.append(Finding(
                    UNCOVERED, ERROR, _sector_text(sector), "", "", round(area, 4), "mp",
                    f"Suprafață de {fmt.area(area, 3)} {where}, neacoperită de niciun imobil.",
                    part))
        limit = f"limita sectorului {sector}" if sector is not None else "limita sectorului"
        for p in members[sector]:
            out = polygons_of(p.clean.difference(outline))
            area = significant_area(out, min_area, min_width)
            if area is not None:
                found.append(Finding(
                    OUTSIDE_SECTOR, ERROR, _sector_text(sector), p.name, "", round(area, 4),
                    "mp", f"Imobilul {p.label} iese din {limit} cu {fmt.area(area, 3)}.", out))
    return found


def _members_by_place(parcels, shapes, sectors, roads) -> dict:
    """{sector: imobile} for the given sectors of shapes ({sector: outline}): the imobile
    outside the roads sector whose pointOnSurface lies inside the sector's outline. When the
    point lies on the edge of one of these outlines and inside none of them, the imobil
    belongs to the outline of shapes it overlaps most (the first in shapes' order on a tie),
    and counts here only when that is one of the given sectors."""
    members = {sector: [] for sector in sectors}
    if not members:
        return members
    for p in parcels:
        if _road(p.sector, roads):
            continue
        point = p.clean.pointOnSurface()
        inside = [sector for sector in sectors if shapes[sector].contains(point)]
        if not inside and any(shapes[sector].intersects(point) for sector in sectors):
            edge = [sector for sector, outline in shapes.items() if outline.intersects(point)]
            best = max(edge, key=lambda s: _overlap_area(p.clean, shapes[s]))
            inside = [best] if best in members else []
        for sector in inside:
            members[sector].append(p)
    return members


def _overlap_area(a, b) -> float:
    shared = polygons_of(a.intersection(b))
    return shared.area() if shared is not None else 0.0


def unmatched_sectors(parcels, limits) -> List[str]:
    """The sectors of the outlines in limits ([(sector or None, geometry)]) that no imobil
    carries."""
    carried = {p.sector for p in parcels}
    return sorted({sector for sector, _geometry in limits
                   if sector is not None and sector not in carried},
                  key=lambda s: (len(s), s))


def place_buildings(buildings, parcels, stop=_never):
    """Find the imobil of each building, from the CGXML file, else the link value, else the
    largest overlap, and the imobile it covers. Each building gets its Placement and its
    imobil's sector; check_buildings reuses them."""
    index = _index(parcels)
    by_key = defaultdict(list)
    for i, p in enumerate(parcels):
        if p.key is not None:
            by_key[p.key].append(i)
    for b in buildings:
        if stop():
            break
        if b.clean is None or b.placement is not None:
            continue
        cover = {}
        for j in index.intersects(b.clean.boundingBox()):
            if parcels[j].clean is not None and parcels[j].clean.intersects(b.clean):
                shared = polygons_of(parcels[j].clean.intersection(b.clean))
                if shared is not None and shared.area() > 0:
                    cover[j] = shared.area()
        own, link_found = b.parcel, True
        if own is None and b.link is not None:
            matches = by_key.get(b.link, [])
            if matches:
                own = max(matches, key=lambda j: cover.get(j, 0.0))
            else:
                link_found = False
        if own is None and cover:
            own = max(cover, key=lambda j: cover[j])
        best = max(cover, key=lambda j: cover[j]) if cover else None
        b.placement = Placement(own, best, cover, link_found)
        if b.sector is None and own is not None:
            b.sector = parcels[own].sector


def check_buildings(buildings, parcels, tolerance=BUILDING_TOLERANCE,
                    min_area=MIN_AREA, stop=_never, min_width=SNAP_PRECISION) -> List[Finding]:
    """Construcții outside their imobil, on another imobil, on none, or overlapping.

    The imobil comes from the CGXML file, else the link value, else the largest overlap. Like
    Generare CG, a construcție is outside its imobil when one of its points lies more than
    tolerance outside it.
    """
    place_buildings(buildings, parcels, stop)
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
            on_other = polygons_of(other.clean.intersection(b.clean))
            found.append(Finding(
                BUILDING_ELSEWHERE, ERROR, _sector_text(parcel.sector), b.name, other.name,
                round(cover[best], 4), "mp",
                f"Construcția {b.label} aparține imobilului {parcel.label}, dar stă în cea "
                f"mai mare parte pe imobilul {other.label} ({fmt.area(cover[best], 2)} din "
                f"{fmt.area(b.clean.area(), 2)}).", on_other or b.clean))
            continue
        worst, worst_point = 0.0, None
        for vertex in b.geometry.vertices():
            probe = QgsGeometry.fromPointXY(QgsPointXY(vertex.x(), vertex.y()))
            if parcel.clean.intersects(probe):
                continue
            gap = parcel.clean.distance(probe)
            if gap > worst:
                worst, worst_point = gap, probe
        if worst <= tolerance:
            continue
        outside = polygons_of(b.clean.difference(parcel.clean))
        area = outside.area() if outside is not None else 0.0
        # the imobil the outside part lies on most, if any
        neighbours = [j for j in cover if j != own]
        other = parcels[max(neighbours, key=lambda j: cover[j])].name if neighbours else ""
        found.append(Finding(
            BUILDING_OUTSIDE, ERROR, _sector_text(parcel.sector), b.name, other,
            round(worst, 4), "m",
            f"Un punct al construcției {b.label} iese din imobilul {parcel.label} cu "
            f"{_cm(worst)} ({fmt.area(area, 3)} în afară).",
            outside if outside is not None and area > 1e-6 else worst_point))

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
            shared = polygons_of(a.clean.intersection(b.clean))
            area = significant_area(shared, min_area, min_width)
            if area is not None:
                found.append(Finding(
                    BUILDING_OVERLAP, ERROR, _sector_text(a.sector, b.sector), a.name, b.name,
                    round(area, 4), "mp",
                    f"Construcțiile {a.label} și {b.label} se suprapun pe "
                    f"{fmt.area(area, 3)}.", shared))
    return found


# --- inputs ---

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
            label = key or f"{snapshot.name} #{feature.id()}"
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
                label = f"{snapshot.name} #{feature.id()} ({number})"
            else:
                label = f"{snapshot.name} #{feature.id()}"
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
    shared = polygons_of(a.intersection(b))
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
    tolerances and the counts. With stop_if_nothing_read, a run on CGXML paths where no file
    could be read fails before any check and before the report is written."""

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
                # every file read gives an imobil or a record without coordinates
                if (self.stop_if_nothing_read and self.read_errors and not parcels
                        and not self.skipped):
                    self.error_msg = cgxml_export.nothing_read(self.read_errors)
                    return False
            else:
                empty = []
                parcels = parcels_from_layers(self.imobile, self.id_field, self.sector_field,
                                              empty)
                buildings = buildings_from_layers(self.constructii, self.link_field,
                                                  self.number_field, empty)
                self.skipped = len(empty)
            if self.stopped():
                return False
            self.parcel_count = len(parcels)
            self.building_count = len(buildings)
            self.sector_count = len({p.sector for p in parcels
                                     if p.sector is not None and not _road(p.sector, self.roads)})
            self.report(30)

            stop = self.stopped
            self._add(check_invalid(parcels, stop), 1)
            place_buildings(buildings, parcels, stop)
            self._add(check_invalid(buildings, stop), 2)
            width = self.snap_precision
            self._add(check_overlaps(parcels, self.min_area, stop, roads=self.roads,
                                     min_width=width), 3)
            gaps = check_gaps(parcels, self.min_area, stop, roads=self.roads, min_width=width)
            self._add(gaps, 4)
            self._add(check_snapping(parcels, self.snap_precision, self.snap_distance, stop,
                                     [g.geometry for g in gaps], roads=self.roads), 5)
            outlines = self._limit_outlines()
            self.unmatched_sectors = unmatched_sectors(parcels, outlines)
            self._add(check_sector_limits(parcels, outlines, self.min_area, stop,
                                          roads=self.roads, min_width=width), 6)
            self._add(check_buildings(buildings, parcels, self.building_tolerance,
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
            ("Cât poate ieși un punct al construcției din imobil",
             metres(self.building_tolerance)),
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
        different imobile, outline sectors that no imobil carries."""
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
        return notes

    STEPS = 7

    def _add(self, findings, step):
        self.findings.extend(findings)
        self.report(30.0 + 65.0 * step / self.STEPS)

    def _limit_outlines(self):
        outlines = []
        if self.limits is None:
            return outlines
        index = self.limits.fields.indexOf(self.limit_field) if self.limit_field else -1
        for feature, geometry in self.limits.features():
            sector = normalize_sector(feature.attributes()[index]) if index >= 0 else None
            outlines.append((sector, QgsGeometry(geometry)))
        return outlines
