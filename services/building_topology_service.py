# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Topologie Construcții: cuts construcții back inside their imobil.

A construcție belongs to the imobil named in its link column, else to the imobil it overlaps
most. Each part of a multipart construcție is cut on its own. An invalid construcție is not
cut.
"""

from qgis.core import (  # type: ignore
    Qgis, QgsFeature, QgsField, QgsFields, QgsGeometry, QgsSpatialIndex,
)

from . import fmt
from .compare_service import normalize_key
from .layer_sources import unique_name
from .tasks import CadexTask
from .topology_check_service import clean, validity_problem
from ..qt_compat import FIELD_DOUBLE, FIELD_STRING

UNCHANGED = "Neschimbată"
FIXED = "Corectată"
LOSS = "Pierdere peste prag"
ELSEWHERE = "Pe alt imobil"
NO_PARCEL = "Fără imobil"
NO_GEOMETRY = "Fără geometrie"
FAILED = "Eroare"
STATES = (UNCHANGED, FIXED, LOSS, ELSEWHERE, NO_PARCEL, NO_GEOMETRY, FAILED)
STATE_COLORS = {UNCHANGED: "#8A93A1", FIXED: "#2E9E5B", LOSS: "#E3A008",
                ELSEWHERE: "#AE3EC9", NO_PARCEL: "#D64545", NO_GEOMETRY: "#495057",
                FAILED: "#7B3FB0"}

# area changes below this are floating-point noise from the intersection
AREA_EPSILON = 1e-6

JOIN_PREFIX = "imb_"


def _flatten(geometry):
    if geometry.isMultipart():
        for part in geometry.asGeometryCollection():
            yield from _flatten(part)
    else:
        yield geometry


def largest_polygon(geometry):
    """(largest polygon part, polygon part count) of an intersection result, which can also
    hold lines and points."""
    if geometry is None or geometry.isEmpty():
        return None, 0
    polygons = [p for p in _flatten(geometry) if not p.isEmpty()
                and p.type() == Qgis.GeometryType.Polygon and p.area() > AREA_EPSILON]
    if not polygons:
        return None, 0
    return max(polygons, key=lambda p: p.area()), len(polygons)


def cut(geometry, parcel, shrunk):
    """(the construcție cut back inside the shrunk imobil, or None when nothing is left; the
    number of
    pieces of the part the cut split most). Each part is cut on its own: a part inside the
    imobil parcel stays as it is, and when the cut splits a part, its largest piece is kept."""
    kept, pieces = [], 0
    for part in _flatten(geometry):
        if part.isEmpty() or part.type() != Qgis.GeometryType.Polygon:
            continue
        if part.within(parcel):
            kept.append(part)
            continue
        piece, count = largest_polygon(part.intersection(shrunk))
        pieces = max(pieces, count)
        if piece is not None:
            kept.append(piece)
    if not kept:
        return None, pieces
    return (kept[0] if len(kept) == 1 else QgsGeometry.collectGeometry(kept)), pieces


def output_fields(source_fields: QgsFields, join_fields):
    """(fields, {column key: name}, {join field: name}): the source fields, the result
    columns, then the joined imobil columns, all names unique."""
    fields = QgsFields(source_fields)
    names = {}
    for key, kind in (("stare", FIELD_STRING), ("sup_inainte", FIELD_DOUBLE),
                      ("sup_dupa", FIELD_DOUBLE), ("sup_pierduta", FIELD_DOUBLE),
                      ("obs_topo", FIELD_STRING)):
        names[key] = unique_name(fields, key)
        if kind == FIELD_DOUBLE:
            fields.append(QgsField(names[key], kind, "double", 20, 4))
        else:
            fields.append(QgsField(names[key], kind))
    join_names = {}
    for field in join_fields:
        name = unique_name(fields, JOIN_PREFIX + field.name())
        copy = QgsField(field)
        copy.setName(name)
        fields.append(copy)
        join_names[field.name()] = name
    return fields, names, join_names


def result_features(rows, fields, join_fields, wkb_type):
    """The corrected construcții as features. A cut is a single 2D polygon, so each geometry
    is coerced to the layer's type."""
    features = []
    for row in rows:
        feature = QgsFeature(fields)
        if row["geometry"] is not None:
            geometry = QgsGeometry(row["geometry"])
            coerced = geometry.coerceToType(wkb_type)
            if len(coerced) == 1:
                geometry = coerced[0]
            feature.setGeometry(geometry)
        values = list(row["attrs"])
        values += [row["stare"], row["sup_inainte"], row["sup_dupa"], row["sup_pierduta"],
                   row["obs_topo"]]
        values += [row["join"].get(f.name()) for f in join_fields]
        feature.setAttributes(values)
        features.append(feature)
    return features


class BuildingTopologyTask(CadexTask):
    """The cut as a background task, in Stereo 70. self.results holds plain values; the
    layers are built on the main thread."""

    def __init__(self, *, imobile, constructii, buffer_m, threshold, join_fields,
                 link_field=None, key_field=None, finished_cb=None):
        super().__init__("CADex: Topologie Construcții", finished_cb)
        self.imobile = imobile
        self.constructii = constructii
        self.buffer_m = buffer_m
        self.threshold = threshold
        self.join_fields = join_fields  # [QgsField] of the imobile layers
        # link_field in construcții holds the values of key_field in imobile
        self.link_field = link_field if key_field else None
        self.key_field = key_field
        self.results = []   # one list of row dicts per construcții snapshot
        self.counts = {state: 0 for state in STATES}
        self._by_key = {}

    def snapshots(self):
        return list(self.imobile) + list(self.constructii)

    def run(self):
        try:
            parcels, index = self._index_parcels()
            total = sum(s.count for s in self.constructii) or 1
            done = 0
            for snapshot in self.constructii:
                rows = []
                link_index = (snapshot.fields.lookupField(self.link_field) if self.link_field
                              else -1)
                for feature, geometry in snapshot.features(with_empty=True):
                    if self.stopped():
                        return False
                    if geometry is None:
                        rows.append(self._without_geometry(feature))
                    else:
                        link = (normalize_key(feature.attributes()[link_index])
                                if link_index >= 0 else None)
                        rows.append(self._fix(feature, geometry, parcels, index, link))
                    done += 1
                    if done % 50 == 0:
                        self.report(10.0 + 90.0 * done / total)
                self.results.append(rows)
            return True
        except Exception as e:
            return self.failed("Topologie Construcții", e)

    def _index_parcels(self):
        parcels = {}
        index = QgsSpatialIndex()
        next_id = 0
        join_names = [f.name() for f in self.join_fields]
        for snapshot in self.imobile:
            key_index = snapshot.fields.lookupField(self.key_field) if self.key_field else -1
            for feature, geometry in snapshot.features():
                key = normalize_key(feature.attributes()[key_index]) if key_index >= 0 else None
                if key is not None:
                    self._by_key.setdefault(key, []).append(next_id)
                if not geometry.isGeosValid():
                    geometry = geometry.makeValid()
                shrunk = geometry.buffer(-self.buffer_m, 8)
                if shrunk is not None and not shrunk.isEmpty() and not shrunk.isGeosValid():
                    shrunk = shrunk.makeValid()
                if shrunk is None or shrunk.isEmpty():
                    # an imobil narrower than twice the buffer disappears; keep its boundary
                    shrunk = geometry
                attrs = {name: feature[name] for name in join_names
                         if snapshot.fields.lookupField(name) >= 0}
                parcels[next_id] = (geometry, shrunk, attrs, key)
                index.addFeature(next_id, geometry.boundingBox())
                next_id += 1
        self.report(10.0)
        return parcels, index

    def _fix(self, feature, geometry, parcels, index, link=None):
        row = {"attrs": feature.attributes(), "geometry": QgsGeometry(geometry),
               "join": {}}
        problem = validity_problem(geometry)
        if problem is not None:
            # an overlay of an invalid polygon fails or gives a wrong shape
            repaired = clean(geometry)
            row["sup_inainte"] = round(repaired.area(), 4) if repaired is not None else None
            return self._finish(row, FAILED, None,
                                f"Geometrie invalidă: {problem[0]}. Nu a fost tăiată.")
        area = geometry.area()
        row["sup_inainte"] = round(area, 4)
        cover = {}
        for candidate in index.intersects(geometry.boundingBox()):
            parcel = parcels[candidate][0]
            if not parcel.intersects(geometry):
                continue
            overlap = geometry.intersection(parcel)
            overlap_area = overlap.area() if overlap is not None else 0.0
            if overlap_area > 0:
                cover[candidate] = overlap_area
        best = max(cover, key=lambda c: cover[c]) if cover else None

        remark = ""
        if link is not None:
            matches = self._by_key.get(link)
            if matches:
                own = max(matches, key=lambda c: cover.get(c, 0.0))
                if best is not None and best != own and cover[best] > cover.get(own, 0.0):
                    # not cut: cutting would keep it on the wrong imobil
                    other = parcels[best][3] or "?"
                    return self._finish(
                        row, ELSEWHERE, area,
                        f"Aparține imobilului {link}, dar stă în cea mai mare parte pe "
                        f"imobilul {other} ({fmt.area(cover[best], 2)} din "
                        f"{fmt.area(area, 2)}). Nu a fost tăiată.")
                best = own if cover.get(own) else best
            elif best is None:
                remark = f" Imobilul '{link}' nu a fost găsit."
            else:
                remark = (f" Imobilul '{link}' nu a fost găsit; s-a folosit imobilul pe "
                          "care se află.")
        if best is None:
            return self._finish(row, NO_PARCEL, area,
                                ("Nu se suprapune cu niciun imobil." + remark).strip())

        parcel, shrunk, attrs, _key = parcels[best]
        row["join"] = attrs
        if geometry.within(parcel):
            return self._finish(row, UNCHANGED, area, remark.strip())
        try:
            result, pieces = cut(geometry, parcel, shrunk)
        except Exception as e:
            return self._finish(row, FAILED, None, f"Eroare la tăiere: {str(e)[:120]}")
        if result is None:
            return self._finish(row, FAILED, None,
                                "După tăiere nu a rămas nimic din construcție.")
        after = result.area()
        lost = area - after
        if lost <= AREA_EPSILON:
            return self._finish(row, UNCHANGED, area, remark.strip())
        row["geometry"] = result
        note = f"Pierde {fmt.area(lost, 4)} ({fmt.percent(100.0 * lost / area)}).{remark}"
        if pieces > 1:
            what = ("construcția" if geometry.constGet().partCount() == 1
                    else "o parte a construcției")
            note += f" Tăierea a împărțit {what} în {pieces} părți; s-a păstrat cea mai mare."
        state = LOSS if lost > self.threshold else FIXED
        return self._finish(row, state, after, note)

    def _without_geometry(self, feature):
        row = {"attrs": feature.attributes(), "geometry": None, "join": {}, "sup_inainte": None}
        return self._finish(row, NO_GEOMETRY, None,
                            "Construcția nu are geometrie și a fost copiată așa cum este.")

    def _finish(self, row, state, after, note):
        self.counts[state] += 1
        row["stare"] = state
        row["sup_dupa"] = round(after, 4) if after is not None else None
        before = row["sup_inainte"]
        row["sup_pierduta"] = (round(before - after, 4)
                               if after is not None else None)
        row["obs_topo"] = note
        return row
