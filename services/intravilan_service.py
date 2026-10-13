# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Raport Intravilan / Extravilan: each imobil's area inside and outside the intravilan."""

import errno
import os
import sqlite3
from datetime import datetime

from qgis.core import (  # type: ignore
    Qgis, QgsCoordinateTransformContext, QgsFeature, QgsField, QgsFields,
    QgsGeometry, QgsSpatialIndex, QgsVectorFileWriter, QgsWkbTypes,
)

from . import fmt, stereo70, xlsx_report
from .diminuare_service import distribute
from .layer_sources import merged_fields, merged_value, unique_name
from .roles import fold
from .tasks import CadexTask
from ..qt_compat import FIELD_DOUBLE, FIELD_STRING, is_null

INTEGRAL_INTRA = "Integral în intravilan"
PARTIAL = "Parțial în intravilan"
INTEGRAL_EXTRA = "Integral în extravilan"
STATUSES = (INTEGRAL_INTRA, PARTIAL, INTEGRAL_EXTRA)
STATUS_COLORS = {INTEGRAL_INTRA: "#2E9E5B", PARTIAL: "#E3A008",
                 INTEGRAL_EXTRA: "#D64545"}
STATUS_TINTS = {INTEGRAL_INTRA: xlsx_report.GOOD, PARTIAL: xlsx_report.MIXED,
                INTEGRAL_EXTRA: xlsx_report.BAD}
WKB_TYPE = Qgis.WkbType.MultiPolygon
# the report's layer in its GeoPackage; the other layers of the file stay
TABLE = "raport_intravilan"
# the table in which QGIS saves the styles of a GeoPackage's layers
STYLE_TABLE = "layer_styles"
# seconds a check of the GeoPackage waits for another program's transaction on it to end;
# the window checks on the GUI thread
LOCK_WAIT = 1.0
# how many partial imobile the Sumar ranks, at most, by extravilan area
TOP_PARTIAL = 10

# (field, Excel header), appended after the imobil's own columns
COMPUTED = (("sup_totala", "Suprafața totală (mp)"),
            ("sup_intra", "Suprafața în intravilan (mp)"),
            ("sup_extra", "Suprafața în extravilan (mp)"),
            ("pct_intra", "Procent în intravilan"),
            ("situatie", "Încadrare"))

UNKNOWN_OWNER = "neidentificat"


def classify(total, inside, tolerance):
    """The status of an imobil of area total with inside mp in intravilan. A part no larger
    than tolerance on either side is ignored; when both parts are, the larger one counts."""
    outside = max(total - inside, 0.0)
    if outside <= tolerance and inside <= tolerance:
        return INTEGRAL_INTRA if inside >= outside else INTEGRAL_EXTRA
    if outside <= tolerance:
        return INTEGRAL_INTRA
    if inside <= tolerance:
        return INTEGRAL_EXTRA
    return PARTIAL


def polygonal(geometry) -> QgsGeometry:
    """geometry repaired by makeValid(), without the lines and points that a spike or a
    collapsed ring leaves next to the polygons: a polygon layer refuses a geometry
    collection. Empty when no polygon remains."""
    geometry = geometry.makeValid()
    if _is_collection(geometry):
        parts = list(_polygon_parts(geometry))
        geometry = QgsGeometry.collectGeometry(parts) if parts else QgsGeometry()
    if geometry.type() != Qgis.GeometryType.Polygon:
        return QgsGeometry()
    return geometry


def _is_collection(geometry) -> bool:
    return QgsWkbTypes.flatType(geometry.wkbType()) == Qgis.WkbType.GeometryCollection


def _polygon_parts(collection):
    # a repair that keeps several polygons puts them in one multipolygon member of the
    # collection, which convertGeometryCollectionToSubclass would drop
    for part in collection.asGeometryCollection():
        if _is_collection(part):
            yield from _polygon_parts(part)
        elif part.type() == Qgis.GeometryType.Polygon:
            yield part


def owner_state(value) -> str:
    """"neidentificat", "lipsă" or "identificat"."""
    if is_null(value) or not str(value).strip():
        return "lipsă"
    return "neidentificat" if UNKNOWN_OWNER in fold(value) else "identificat"


def _connect(path):
    # no implicit transactions: the checks below begin their own
    return sqlite3.connect(path, timeout=LOCK_WAIT, isolation_level=None)


def geopackage_tables(path):
    """The tables the GeoPackage at path lists, None when the file is not a GeoPackage:
    empty, damaged or of another kind. Raises an OSError naming the file when it cannot be
    read or written, such as a PermissionError when another program holds it or is writing
    to it."""
    with open(path, "rb") as fp:
        if fp.read(16) != b"SQLite format 3\x00":
            return None
    try:
        connection = _connect(path)
        try:
            listed = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gpkg_contents'"
            ).fetchone()
            if listed is None:
                return None
            tables = [name for (name,) in connection.execute(
                "SELECT table_name FROM gpkg_contents")]
            # the reads above succeed while another program has a write unfinished; starting
            # a write does not
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ROLLBACK")
            return tables
        finally:
            connection.close()
    except sqlite3.OperationalError as error:
        # locked by another program, or out of reach: the file may still hold layers
        raise PermissionError(errno.EACCES, str(error), path) from error
    except sqlite3.DatabaseError:
        # not a database, or a damaged one
        return None


def check_unlocked(path):
    """Raise a PermissionError naming the GeoPackage at path when another program holds it or
    has a transaction open on it. In a file with a rollback journal, as GDAL creates them,
    an open read also keeps a write from finishing."""
    with open(path, "r+b"):
        pass
    try:
        connection = _connect(path)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute("ROLLBACK")
        finally:
            connection.close()
    except sqlite3.OperationalError as error:
        raise PermissionError(errno.EACCES, str(error), path) from error
    except sqlite3.DatabaseError:
        # not a database: the writer's error is reported as it is
        pass


class IntravilanTask(CadexTask):
    """The report as a background task. Writes the GeoPackage and Excel file when their
    paths are given; output_features() builds the layer otherwise."""

    def __init__(self, *, imobile, intravilan, excel_fields, id_field, owner_field,
                 tolerance, gpkg_path=None, excel_path=None, only_integral=False,
                 finished_cb=None):
        super().__init__("CADex: Raport Intravilan / Extravilan", finished_cb)
        self.imobile = imobile
        self.intravilan = intravilan
        self.excel_fields = excel_fields
        self.id_field = id_field
        self.owner_field = owner_field
        self.tolerance = tolerance
        self.gpkg_path = gpkg_path or None
        self.excel_path = excel_path or None
        self.only_integral = only_integral
        self.rows = []
        self.skipped = 0
        # the GeoPackage's features: offered, taken, and the first refusal's reason
        self.expected = 0
        self.written = 0
        self.write_error = ""
        self.table = TABLE
        # the layers the GeoPackage already held besides the report and the styles
        self.other_layers = []
        self.fields = QgsFields()
        self.output_fields = QgsFields()
        self.status_field = "situatie"

    def snapshots(self):
        return list(self.imobile) + list(self.intravilan)

    def run(self):
        try:
            self._compute()
            if self.stopped():
                return False
            self.output_fields, names = self._output_fields()
            self.status_field = names["situatie"]
            if self.gpkg_path:
                self._write_gpkg()
            if self.excel_path:
                self.write_report(self._write_excel, self.excel_path)
            self.report(100.0)
            return True
        except Exception as e:
            return self.failed("Raport Intravilan", e)

    def _compute(self):
        index = QgsSpatialIndex()
        zones = {}
        next_id = 0
        for snapshot in self.intravilan:
            for _feature, geometry in snapshot.features():
                geometry = polygonal(geometry)
                if geometry.isEmpty():
                    continue
                zones[next_id] = geometry
                index.addFeature(next_id, geometry.boundingBox())
                next_id += 1

        total = sum(s.count for s in self.imobile) or 1
        done = 0
        self.fields = merged_fields(self.imobile)
        multiple = len(self.imobile) > 1
        for snapshot in self.imobile:
            for feature, geometry in snapshot.features(with_empty=True):
                if self.stopped():
                    return
                done += 1
                if done % 50 == 0:
                    self.report(85.0 * done / total)
                if geometry is None:
                    self.skipped += 1
                    continue
                geometry = polygonal(geometry)
                area = geometry.area()
                if geometry.isEmpty() or area <= 0:
                    self.skipped += 1
                    continue
                candidates = [zones[i] for i in index.intersects(geometry.boundingBox())]
                inside = 0.0
                if candidates:
                    # merged first, so ground shared by two overlapping polygons counts once
                    zone = (candidates[0] if len(candidates) == 1
                            else QgsGeometry.unaryUnion(candidates))
                    if geometry.intersects(zone):
                        part = geometry.intersection(zone)
                        if part is not None and not part.isEmpty():
                            inside = min(part.area(), area)
                status = classify(area, inside, self.tolerance)
                attributes = {name: feature[name] for name in snapshot.fields.names()}
                # both parts rounded to whole square metres, still adding up to the rounded
                # total
                sup_intra, sup_extra = (int(v) for v in distribute([inside, area - inside],
                                                                   area, 0))
                self.rows.append({
                    "layer": snapshot.name if multiple else "",
                    "fid": feature.id(),
                    "attrs": attributes,
                    "geometry": geometry,
                    "sup_totala": sup_intra + sup_extra,
                    "sup_intra": sup_intra,
                    "sup_extra": sup_extra,
                    "pct_intra": round(100.0 * inside / area, 1),
                    "situatie": status,
                    "owner": (merged_value(attributes, self.owner_field)
                              if self.owner_field else None),
                })

    def _output_fields(self):
        fields = QgsFields(self.fields)
        names = {}
        for key, _label in COMPUTED:
            name = unique_name(fields, key)
            names[key] = name
            kind = FIELD_STRING if key == "situatie" else FIELD_DOUBLE
            fields.append(QgsField(name, kind))
        if any(row["layer"] for row in self.rows):
            names["layer"] = unique_name(fields, "layer_sursa")
            fields.append(QgsField(names["layer"], FIELD_STRING))
        return fields, names

    def output_features(self):
        """The result features in Stereo 70; with only_integral, just the imobile entirely in
        intravilan."""
        base = self.fields.names()
        layered = any(row["layer"] for row in self.rows)
        for row in self.rows:
            if self.only_integral and row["situatie"] != INTEGRAL_INTRA:
                continue
            feature = QgsFeature(self.output_fields)
            geometry = QgsGeometry(row["geometry"])
            geometry.convertToMultiType()
            feature.setGeometry(geometry)
            values = [merged_value(row["attrs"], name) for name in base]
            values += [row[key] for key, _label in COMPUTED]
            if layered:
                values.append(row["layer"])
            feature.setAttributes(values)
            yield feature

    def output_count(self) -> int:
        """The number of features output_features() yields."""
        return sum(1 for row in self.rows
                   if not self.only_integral or row["situatie"] == INTEGRAL_INTRA)

    def _write_gpkg(self):
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.fileEncoding = "UTF-8"
        options.layerName = self.table
        if os.path.exists(self.gpkg_path):
            tables = geopackage_tables(self.gpkg_path)
            if tables is None:
                # an empty or damaged file, or another kind of file, has no layers to keep
                os.remove(self.gpkg_path)
            else:
                # only the report's table is replaced; the file's other layers stay
                options.actionOnExistingFile = \
                    QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
                self.other_layers = [name for name in tables
                                     if name.lower() not in (self.table, STYLE_TABLE)]
        writer = QgsVectorFileWriter.create(
            self.gpkg_path, self.output_fields, WKB_TYPE, stereo70.crs(),
            QgsCoordinateTransformContext(), options)
        if writer.hasError() != QgsVectorFileWriter.WriterError.NoError:
            message = writer.errorMessage()
            del writer
            if os.path.exists(self.gpkg_path):
                # another program may have locked the file after the check above, and GDAL's
                # message shows only the SQL statement that failed
                check_unlocked(self.gpkg_path)
            raise OSError(None, message, self.gpkg_path)
        try:
            for feature in self.output_features():
                self.expected += 1
                if writer.addFeature(feature):
                    self.written += 1
                elif not self.write_error:
                    self.write_error = writer.errorMessage()
        finally:
            del writer

    def _row_id(self, row):
        value = merged_value(row["attrs"], self.id_field) if self.id_field else None
        return row["fid"] if is_null(value) else value

    def _write_excel(self):
        rows = self.rows
        wb = xlsx_report.new_workbook()
        multiple = any(row["layer"] for row in rows)
        headers = (["Layer"] if multiple else []) + list(self.excel_fields) \
            + [label for _key, label in COMPUTED]

        def line(row):
            values = [row["layer"]] if multiple else []
            values += [merged_value(row["attrs"], name) for name in self.excel_fields]
            values += [row[key] for key, _label in COMPUTED]
            return values

        # written as text, or Excel drops the leading zero of an ID like 0123
        text_fields = [name for name in self.excel_fields
                       if self.fields.lookupField(name) >= 0
                       and self.fields.at(self.fields.lookupField(name)).type() == FIELD_STRING]

        def sheet(title, subset):
            xlsx_report.write_table(
                wb, title, headers, [line(r) for r in subset],
                row_colors=[STATUS_TINTS[r["situatie"]] for r in subset],
                text_columns=text_fields)

        sheet("Toate", rows)
        for status in STATUSES:
            sheet(status, [r for r in rows if r["situatie"] == status])
        if self.owner_field:
            unknown = [r for r in rows if owner_state(r["owner"]) != "identificat"]
            if unknown:
                sheet("Proprietari neidentificați", unknown)
        self._write_summary(wb)
        xlsx_report.save(wb, self.excel_path)

    def _write_summary(self, wb):
        rows = self.rows
        summary = xlsx_report.Summary(wb, "Raport Intravilan / Extravilan")
        summary.section("Informații")
        summary.facts([
            ("Data", datetime.now().strftime("%d.%m.%Y %H:%M")),
            ("Layere imobile", ", ".join(s.name for s in self.imobile)),
            ("Layere contur intravilan", ", ".join(s.name for s in self.intravilan)),
            ("Sistem de coordonate", stereo70.NAME),
            ("Toleranță", f"{fmt.area(self.tolerance)}: o porțiune de cel mult atât nu "
                          "schimbă încadrarea imobilului"),
            ("Imobile fără geometrie sau cu suprafața 0, ignorate", self.skipped),
        ])

        n = len(rows) or 1
        counts = {s: [r for r in rows if r["situatie"] == s] for s in STATUSES}
        summary.section("Imobile")
        summary.table(
            ["Încadrare", "Nr. imobile", "Procent", "Suprafața totală (mp)"],
            [["Toate imobilele", len(rows), xlsx_report.percent(100),
              sum(r["sup_totala"] for r in rows)]]
            + [[s, len(counts[s]), xlsx_report.percent(100.0 * len(counts[s]) / n),
                sum(r["sup_totala"] for r in counts[s])] for s in STATUSES],
            row_colors=[None] + [STATUS_TINTS[s] for s in STATUSES])

        total_area = sum(r["sup_totala"] for r in rows) or 1
        intra_area = sum(r["sup_intra"] for r in rows)
        extra_area = sum(r["sup_extra"] for r in rows)
        partial = counts[PARTIAL]

        def pct(value):
            return xlsx_report.percent(100.0 * value / total_area)

        summary.section("Suprafețe")
        summary.table(
            ["Descriere", "Suprafață (mp)", "Procent", "Detalii"],
            [["Suprafața tuturor imobilelor", total_area if rows else 0,
              xlsx_report.percent(100), ""],
             ["În intravilan", intra_area, pct(intra_area),
              "Partea din intravilan a fiecărui imobil, inclusiv porțiunile care nu "
              "schimbă încadrarea"],
             ["În extravilan", extra_area, pct(extra_area),
              "Partea din extravilan a fiecărui imobil, inclusiv porțiunile care nu "
              "schimbă încadrarea"],
             ["Partea din intravilan a imobilelor parțiale",
              sum(r["sup_intra"] for r in partial),
              pct(sum(r["sup_intra"] for r in partial)), ""],
             ["Partea din extravilan a imobilelor parțiale",
              sum(r["sup_extra"] for r in partial),
              pct(sum(r["sup_extra"] for r in partial)), ""]],
            row_colors=[None, xlsx_report.GOOD, xlsx_report.BAD,
                        xlsx_report.MIXED, xlsx_report.MIXED])

        if partial:
            bands = ((0, 10), (10, 25), (25, 50), (50, 75), (75, 90), (90, 100))
            band_rows = []
            for low, high in bands:
                members = [r for r in partial
                           if (low < r["pct_intra"] <= high)
                           or (low == 0 and r["pct_intra"] == 0)]
                band_rows.append([f"{low}-{high}%", len(members),
                                  sum(r["sup_extra"] for r in members), ""])
            summary.section("Imobile parțiale după procentul din intravilan")
            summary.table(["Procent în intravilan", "Nr. imobile",
                           "Suprafața în extravilan (mp)", ""], band_rows)

            top = sorted(partial, key=lambda r: r["sup_extra"], reverse=True)[:TOP_PARTIAL]
            if len(top) == 1:
                summary.section("Imobilul parțial și suprafața lui în extravilan")
            else:
                summary.section("Cele {0} cu cele mai mari suprafețe în extravilan".format(
                    fmt.count(len(top), "imobil parțial", "imobile parțiale")))
            summary.table(["Imobil", "În extravilan (mp)", "În intravilan (mp)",
                           "Procent în intravilan"],
                          [[self._row_id(r), r["sup_extra"], r["sup_intra"],
                            xlsx_report.percent(r["pct_intra"])] for r in top])

        if self.owner_field:
            states = {"identificat": [], "neidentificat": [], "lipsă": []}
            for row in rows:
                states[owner_state(row["owner"])].append(row)
            summary.section(f"Proprietari (câmpul '{self.owner_field}')")
            labels = (("identificat", "Proprietar identificat", xlsx_report.GOOD),
                      ("neidentificat", "Proprietar NEIDENTIFICAT", xlsx_report.NOTE),
                      ("lipsă", "Fără proprietar completat", xlsx_report.MIXED))
            summary.table(
                ["Categorie", "Nr. imobile", "Suprafața totală (mp)",
                 "Din care în extravilan (mp)"],
                [[label, len(states[key]), sum(r["sup_totala"] for r in states[key]),
                  sum(r["sup_extra"] for r in states[key])]
                 for key, label, _color in labels],
                row_colors=[color for _key, _label, color in labels])

        summary.note("Suprafețele sunt calculate din geometrie, în planul Stereo 70 "
                     "(EPSG:3844), și rotunjite la metru pătrat.")
