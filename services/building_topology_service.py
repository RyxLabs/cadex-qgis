# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Topologie Construcții: cuts construcții back inside their imobil.

A construcție belongs to the imobil named in its link column, else to the imobil it overlaps
most, and is cut when it lies outside that imobil by more than the tolerance. The placement
and the rule, topology_check_service.outside(), are those of Verificare Topologică, so with
the same tolerance a construcție is cut exactly when that check reports it.
Each part of a multipart construcție is cut on its own. An invalid construcție is not cut.
"""

from qgis.core import Qgis, QgsFeature, QgsField, QgsFields, QgsGeometry  # type: ignore

from . import fmt, overlay
from .compare_service import normalize_key
from .layer_sources import unique_name
from .tasks import CadexTask
from .topology_check_service import (
    BUILDING_TOLERANCE, Building, Coverage, Parcel, clean, outside, placement, unnamed,
    validity_problem,
)
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

# mp; a piece of an intersection smaller than this is floating-point noise
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


def cut(geometry, parcel, shrunk, tolerance):
    """(the construcție cut back inside the shrunk imobil, or None when nothing is left; the
    number of pieces of the part the cut split most; the number of parts of which nothing is
    left). Each part is cut on its own: a part that does not lie outside the imobil parcel by
    more than tolerance stays as it is, and when the cut splits a part, its largest piece is
    kept."""
    kept, pieces, removed = [], 0, 0
    for part in _flatten(geometry):
        if part.isEmpty() or part.type() != Qgis.GeometryType.Polygon:
            continue
        if outside(part, parcel, tolerance) is None:
            kept.append(part)
            continue
        piece, count = largest_polygon(overlay.intersection(part, shrunk))
        pieces = max(pieces, count)
        if piece is None:
            removed += 1
        else:
            kept.append(piece)
    if not kept:
        return None, pieces, removed
    result = kept[0] if len(kept) == 1 else QgsGeometry.collectGeometry(kept)
    return result, pieces, removed


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
                 link_field=None, key_field=None, tolerance=BUILDING_TOLERANCE,
                 finished_cb=None):
        super().__init__("CADex: Topologie Construcții", finished_cb)
        self.imobile = imobile
        self.constructii = constructii
        self.buffer_m = buffer_m
        self.threshold = threshold
        # how far a construcție may lie outside its imobil without a cut
        self.tolerance = tolerance
        self.join_fields = join_fields  # [QgsField] of the imobile layers
        # link_field in construcții holds the values of key_field in imobile
        self.link_field = link_field if key_field else None
        self.key_field = key_field
        self.results = []   # one list of row dicts per construcții snapshot
        self.counts = {state: 0 for state in STATES}
        self._shrunk = {}   # imobil index: the imobil moved inward by buffer_m

    def snapshots(self):
        return list(self.imobile) + list(self.constructii)

    def run(self):
        try:
            coverage, attrs = self._read_parcels()
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
                        rows.append(self._fix(feature, geometry, coverage, attrs, link))
                    done += 1
                    if done % 50 == 0:
                        self.report(10.0 + 90.0 * done / total)
                self.results.append(rows)
            return True
        except Exception as e:
            return self.failed("Topologie Construcții", e)

    def _read_parcels(self):
        """(the imobile as a Coverage, named as Verificare Topologică names them; the join
        attributes of each)."""
        parcels, attrs = [], []
        join_names = [f.name() for f in self.join_fields]
        for snapshot in self.imobile:
            key_index = snapshot.fields.lookupField(self.key_field) if self.key_field else -1
            for feature, geometry in snapshot.features():
                key = normalize_key(feature.attributes()[key_index]) if key_index >= 0 else None
                parcels.append(Parcel(key or unnamed(snapshot, feature), None,
                                      QgsGeometry(geometry), key=key))
                attrs.append({name: feature[name] for name in join_names
                              if snapshot.fields.lookupField(name) >= 0})
        self.report(10.0)
        return Coverage(parcels), attrs

    def _moved_inward(self, coverage, i):
        """The imobil with index i shrunk by buffer_m; the imobil itself when it is narrower
        than twice buffer_m and nothing of it would be left."""
        if i not in self._shrunk:
            parcel = coverage.parcels[i].clean
            shrunk = clean(parcel.buffer(-self.buffer_m, 8))
            self._shrunk[i] = parcel if shrunk is None else shrunk
        return self._shrunk[i]

    def _fix(self, feature, geometry, coverage, attrs, link=None):
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
        own, best, cover, link_found = placement(
            Building("", geometry, link=link, clean=geometry), coverage)
        remark = ""
        if not link_found:
            remark = (f" Imobilul '{link}' nu a fost găsit; s-a folosit imobilul pe care se află."
                      if cover else f" Imobilul '{link}' nu a fost găsit.")
        if not cover:
            return self._finish(row, NO_PARCEL, area,
                                ("Nu se suprapune cu niciun imobil." + remark).strip())
        if best != own and cover[best] > cover.get(own, 0.0):
            # not cut: cutting would keep it on the wrong imobil
            return self._finish(
                row, ELSEWHERE, area,
                f"Aparține imobilului {link}, dar stă în cea mai mare parte pe imobilul "
                f"{coverage.parcels[best].label} ({fmt.area(cover[best], 2)} din "
                f"{fmt.area(area, 2)}). Nu a fost tăiată.")

        parcel = coverage.parcels[own].clean
        row["join"] = attrs[own]
        if outside(geometry, parcel, self.tolerance) is None:
            return self._finish(row, UNCHANGED, area, remark.strip())
        try:
            result, pieces, removed = cut(geometry, parcel, self._moved_inward(coverage, own),
                                          self.tolerance)
        except Exception as e:
            return self._finish(row, FAILED, None, f"Eroare la tăiere: {str(e)[:120]}")
        if result is None:
            return self._finish(row, FAILED, None,
                                "După tăiere nu a rămas nimic din construcție.")
        after = result.area()
        lost = area - after
        row["geometry"] = result
        note = f"Pierde {fmt.area(lost, 4)} ({fmt.percent(100.0 * lost / area)}).{remark}"
        if pieces > 1:
            what = ("construcția" if geometry.constGet().partCount() == 1
                    else "o parte a construcției")
            note += f" Tăierea a împărțit {what} în {pieces} părți; s-a păstrat cea mai mare."
        if removed:
            note += fmt.plural(
                removed,
                " O parte a construcției a fost eliminată: după tăiere nu a rămas nimic din ea.",
                " {0} ale construcției au fost eliminate: după tăiere nu a rămas nimic din "
                "ele.").format(fmt.count(removed, "parte", "părți"))
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
