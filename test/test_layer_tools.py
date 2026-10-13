# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Raport Intravilan / Extravilan, Comparator and Topologie Construcții on hand-built layers
with known areas. run() is called directly."""

import os
import sys
import tempfile
import unittest

import helpers

helpers.start_qgis(gui=False)

from qgis.core import (  # noqa: E402  # type: ignore
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsProject, QgsVectorLayer,
)

from cadex.services import building_topology_service as bts  # noqa: E402
from cadex.services import compare_service, intravilan_service, xlsx  # noqa: E402
from cadex.services.layer_sources import LayerSnapshot, merged_fields  # noqa: E402

square = helpers.square


class IntravilanTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_intra_")
        # 100 x 100 squares: A inside, B half inside, C outside, D touching the limit by 0.2 mp
        self.imobile = helpers.polygon_layer("Imobile", [
            (square(0, 0, 100), ["A", "POPESCU ION"]),
            (square(150, 0, 100), ["B", "NEIDENTIFICAT"]),
            (square(400, 0, 100), ["C", None]),
            (square(-100.002, 200, 100), ["D", "IONESCU"]),
        ], fields="cod:string,proprietar:string")
        # two intravilan polygons that overlap over x 0..100, which must count once
        self.intra = helpers.polygon_layer("Intravilan", [
            (square(-10, -10, 210), ["T1"]),
            (square(0, -10, 100), ["T2"]),
            (square(-200, 200, 100.0), ["T3"]),
        ], fields="nume:string")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def run_task(self, **overrides):
        options = dict(
            imobile=[LayerSnapshot(self.imobile)],
            intravilan=[LayerSnapshot(self.intra)],
            excel_fields=["cod", "proprietar"], id_field="cod",
            owner_field="proprietar", tolerance=0.5,
            gpkg_path=os.path.join(self.tmp, "raport.gpkg"),
            excel_path=os.path.join(self.tmp, "raport.xlsx"), only_integral=False)
        options.update(overrides)
        task = intravilan_service.IntravilanTask(**options)
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_statuses_and_overlap_counted_once(self):
        rows = {r["attrs"]["cod"]: r for r in self.run_task().rows}
        self.assertEqual(rows["A"]["situatie"], intravilan_service.INTEGRAL_INTRA)
        # T1 and T2 overlap over A - summing each would give 20000 mp inside a 10000 mp parcel
        self.assertEqual(rows["A"]["sup_intra"], 10000)
        self.assertEqual(rows["B"]["situatie"], intravilan_service.PARTIAL)
        self.assertEqual((rows["B"]["sup_intra"], rows["B"]["sup_extra"]), (5000, 5000))
        self.assertEqual(rows["C"]["situatie"], intravilan_service.INTEGRAL_EXTRA)
        # D reaches 0.2 mp into T3, under the 0.5 mp tolerance
        self.assertEqual(rows["D"]["situatie"], intravilan_service.INTEGRAL_EXTRA)

    def test_imobile_without_geometry_are_counted(self):
        from qgis.core import QgsFeature  # type: ignore
        feature = QgsFeature(self.imobile.fields())
        feature.setAttributes(["E", None])
        self.imobile.dataProvider().addFeatures([feature])
        task = self.run_task()
        self.assertEqual(task.skipped, 1)
        self.assertEqual(len(task.rows), 4)

    def test_areas_inside_and_outside_add_up_to_the_total(self):
        # 10.6 mp with 5.3 inside: rounded one by one they give 11, 5 and 5
        imobile = helpers.polygon_layer(
            "Imobile", [("POLYGON((0 0, 10.6 0, 10.6 1, 0 1, 0 0))", ["X", None])],
            fields="cod:string,proprietar:string")
        intra = helpers.polygon_layer(
            "Intravilan", [("POLYGON((-1 -1, 5.3 -1, 5.3 2, -1 2, -1 -1))", ["T"])],
            fields="nume:string")
        row = self.run_task(imobile=[LayerSnapshot(imobile)],
                            intravilan=[LayerSnapshot(intra)]).rows[0]
        self.assertEqual(row["sup_totala"], 11)
        self.assertEqual(row["sup_intra"] + row["sup_extra"], row["sup_totala"])

    def test_tolerance_zero_counts_every_sliver(self):
        rows = {r["attrs"]["cod"]: r for r in self.run_task(tolerance=0.0).rows}
        self.assertEqual(rows["D"]["situatie"], intravilan_service.PARTIAL)

    def test_outputs(self):
        task = self.run_task()
        layer = QgsVectorLayer(f"{task.gpkg_path}|layername={task.table}", "r", "ogr")
        self.assertTrue(layer.isValid())
        self.assertEqual(layer.featureCount(), 4)
        self.assertIn("situatie", layer.fields().names())
        self.assertEqual(xlsx.sheet_names(task.excel_path),
                         ["Sumar", "Toate"] + list(intravilan_service.STATUSES)
                         + ["Proprietari neidentificați"])
        _names, unknown = xlsx.read_rows(task.excel_path, "Proprietari neidentificați")
        # NEIDENTIFICAT and an empty owner
        self.assertEqual(sorted(r[0] for r in unknown[1:]), ["B", "C"])
        _names, summary = xlsx.read_rows(task.excel_path, "Sumar")
        rows = {r[0]: r for r in summary if r and r[0]}
        self.assertTrue(rows["Toleranță"][1].startswith("0,5 mp"), rows["Toleranță"])
        # 1 of 4 imobile, a number and not the text "25.0%"
        self.assertAlmostEqual(rows[intravilan_service.PARTIAL][2], 0.25)

    def test_only_integral(self):
        task = self.run_task(only_integral=True)
        layer = QgsVectorLayer(f"{task.gpkg_path}|layername={task.table}", "r", "ogr")
        self.assertEqual(layer.featureCount(), 1)

    def test_owner_state(self):
        self.assertEqual(intravilan_service.owner_state("Neidentificat"), "neidentificat")
        self.assertEqual(intravilan_service.owner_state("PROPRIETAR NEIDENTIFICAT"),
                         "neidentificat")
        self.assertEqual(intravilan_service.owner_state(None), "lipsă")
        self.assertEqual(intravilan_service.owner_state("  "), "lipsă")
        self.assertEqual(intravilan_service.owner_state("POPESCU"), "identificat")

    def test_several_imobile_layers_and_a_reprojected_one(self):
        second = helpers.polygon_layer("Imobile S2", [(square(0, 0, 100), ["A2"])],
                                       fields="cod:string")
        task = self.run_task(
            imobile=[LayerSnapshot(self.imobile), LayerSnapshot(second)],
            intravilan=[LayerSnapshot(self._reprojected_intra())])
        rows = {r["attrs"].get("cod"): r for r in task.rows}
        self.assertEqual(rows["A2"]["layer"], "Imobile S2")
        self.assertEqual(rows["A"]["sup_intra"], 10000)

    def _reprojected_intra(self):
        """The same polygons stored in UTM 35N; the snapshot returns them in Stereo 70."""
        utm = QgsCoordinateReferenceSystem("EPSG:32635")
        transform = QgsCoordinateTransform(self.intra.crs(), utm, QgsProject.instance())
        rows = []
        for feature in self.intra.getFeatures():
            geometry = QgsGeometry(feature.geometry())
            geometry.densifyByCount(20)
            geometry.transform(transform)
            rows.append((geometry.asWkt(), [feature["nume"]]))
        return helpers.polygon_layer("Intravilan UTM", rows, fields="nume:string",
                                     crs="EPSG:32635")

    def test_merged_fields_widen_conflicting_types_to_text(self):
        a = helpers.polygon_layer("a", [], fields="cod:integer,x:double")
        b = helpers.polygon_layer("b", [], fields="cod:string,y:integer")
        merged = merged_fields([LayerSnapshot(a), LayerSnapshot(b)])
        self.assertEqual(merged.names(), ["cod", "x", "y"])
        self.assertEqual(merged.field("cod").typeName().lower(), "string")


class CompareTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_")
        self.old = helpers.polygon_layer("vechi", [
            (square(0, 0, 10), [100001.0, "POPESCU", "12"]),
            (square(20, 0, 10), [100002.0, "IONESCU", "7"]),
            (square(40, 0, 10), [100003.0, "VASILE", "0123"]),
            (square(60, 0, 10), [None, "FARA CHEIE", "1"]),
        ], fields="IE:double,propr:string,nr:string")
        self.new = helpers.polygon_layer("nou", [
            (square(0, 0, 10), ["100001", " POPESCU ", "12"]),
            (square(20, 0, 10.5), ["100002", "IONESCU", "7"]),
            (square(40, 0, 10), ["100003", "VASILE", "123"]),
            (square(80, 0, 10), ["100004", "NOU", "1"]),
            (square(80, 0, 10), ["100004", "NOU DUBLU", "1"]),
        ], fields="IE:string,propr:string,nr:string")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def run_task(self, compare_geometry=True, excel=True):
        task = compare_service.CompareTask(
            old=LayerSnapshot(self.old),
            new=LayerSnapshot(self.new),
            key_old="IE", key_new="IE", fields=["propr", "nr"],
            compare_geometry=compare_geometry, tolerance=0.1,
            excel_path=os.path.join(self.tmp, "cmp.xlsx") if excel else "")
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_diff(self):
        task = self.run_task()
        # a SHP real key and a GPKG text key are the same imobil
        self.assertEqual(task.added, ["100004"])
        self.assertEqual(task.deleted, [])
        # surrounding spaces are no change, "0123" to "123" is one
        self.assertEqual(task.changed, {"100003": [("nr", "0123", "123")]})
        self.assertEqual(set(task.geometry_changes), {"100002"})
        self.assertEqual(task.changed_keys(), ["100002", "100003"])
        self.assertEqual(task.old_missing, 1)
        self.assertEqual(task.new_dups, {"100004": 2})

    def test_without_geometry(self):
        task = self.run_task(compare_geometry=False, excel=False)
        self.assertEqual(task.geometry_changes, {})
        self.assertEqual(task.changed_keys(), ["100003"])

    def test_excel(self):
        task = self.run_task()
        self.assertEqual(xlsx.sheet_names(task.excel_path),
                         ["Sumar", "Apărute", "Modificate", "Chei duplicate"])
        _names, detail = xlsx.read_rows(task.excel_path, "Modificate")
        self.assertIn(("100002", compare_service.GEOMETRY), [(r[0], r[2]) for r in detail[1:]])

    def test_changed_records_are_classed_as_boundary_data_or_both_changed(self):
        # 100001 moved and has a new owner, 100002 only moved, 100003 only has a new number
        self.new = helpers.polygon_layer("nou", [
            (square(0, 0, 11), ["100001", "POPESCU ION", "12"]),
            (square(20, 0, 10.5), ["100002", "IONESCU", "7"]),
            (square(40, 0, 10), ["100003", "VASILE", "123"]),
        ], fields="IE:string,propr:string,nr:string")
        task = self.run_task()
        self.assertEqual({key: task.change_kind(key) for key in task.changed_keys()},
                         {"100001": "Limita și datele", "100002": "Doar limita",
                          "100003": "Doar datele"})
        _names, summary = xlsx.read_rows(task.excel_path, "Sumar")
        rows = [r[:2] for r in summary]
        self.assertIn(["Modificate", 3], rows)
        for kind in ("Doar limita", "Doar datele", "Limita și datele"):
            self.assertIn(["   " + kind, 1], rows)
        _names, detail = xlsx.read_rows(task.excel_path, "Modificate")
        self.assertEqual(detail[0][:3], ["Cheie", "Tip modificare", "Coloana"])
        self.assertIn(["100002", "Doar limita", compare_service.GEOMETRY],
                      [r[:3] for r in detail[1:]])
        # without the geometry a moved boundary is not detected, so the changes are not
        # split by kind
        task = self.run_task(compare_geometry=False)
        _names, detail = xlsx.read_rows(task.excel_path, "Modificate")
        self.assertEqual(detail[0][:2], ["Cheie", "Coloana"])
        _names, summary = xlsx.read_rows(task.excel_path, "Sumar")
        self.assertNotIn("   Doar datele", [r[0] for r in summary])

    def test_excel_leaves_out_the_geopackage_row_numbers(self):
        self.new = helpers.gpkg_layer(self.new, os.path.join(self.tmp, "nou.gpkg"))
        self.assertIn("fid", self.new.fields().names())
        task = self.run_task()
        _sheets, rows = xlsx.read_rows(task.excel_path, "Apărute")
        self.assertEqual(rows[0], ["IE", "propr", "nr"])

    def test_an_excel_report_is_written_after_a_geopackage_write_in_the_same_session(self):
        self.run_task()
        writer_layer = helpers.polygon_layer("x", [(square(0, 0, 1), [1])])
        from qgis.core import QgsCoordinateTransformContext, QgsVectorFileWriter  # type: ignore
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        QgsVectorFileWriter.writeAsVectorFormatV3(
            writer_layer, os.path.join(self.tmp, "x.gpkg"),
            QgsCoordinateTransformContext(), options)
        self.run_task()

    def building_task(self, **overrides):
        """Construcții keyed by imobil and number: 100001 C1 unchanged, 100001 C2 moved,
        100002 C1 with another destination, 100002 C2 gone, 100004 C1 new."""
        old = helpers.polygon_layer("constr vechi", [
            (square(1, 1, 3), [100001, "C1", "CL"]), (square(5, 5, 3), [100001, "C2", "CA"]),
            (square(21, 1, 3), [100002, "C1", "CL"]), (square(25, 5, 3), [100002, "C2", "CA"]),
        ], fields="id:integer,nr_constr:string,destinatie:string")
        new = helpers.polygon_layer("constr nou", [
            (square(1, 1, 3), ["100001", "C1", "CL"]), (square(5.5, 5, 3), ["100001", "C2", "CA"]),
            (square(21, 1, 3), ["100002", "C1", "CIE"]), (square(81, 1, 3), ["100004", "C1", "CL"]),
        ], fields="id:string,nr_constr:string,destinatie:string")
        buildings = compare_service.CompareTask(
            old=LayerSnapshot(old), new=LayerSnapshot(new),
            key_old=["id", "nr_constr"], key_new=["id", "nr_constr"], fields=["destinatie"],
            compare_geometry=True, tolerance=0.1, excel_path=None)
        options = dict(old=LayerSnapshot(self.old), new=LayerSnapshot(self.new),
                       key_old="IE", key_new="IE", fields=["propr", "nr"],
                       compare_geometry=True, tolerance=0.1,
                       excel_path=os.path.join(self.tmp, "cmp.xlsx"), buildings=buildings)
        options.update(overrides)
        task = compare_service.CompareTask(**options)
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_buildings_by_imobil_and_number(self):
        task = self.building_task()
        b = task.buildings
        self.assertEqual(b.added, ["100004 C1"])
        self.assertEqual(b.deleted, ["100002 C2"])
        self.assertEqual(b.changed, {"100002 C1": [("destinatie", "CL", "CIE")]})
        self.assertEqual(set(b.geometry_changes), {"100001 C2"})
        self.assertEqual(b.unchanged_count(), 1)
        # the imobile are compared as in a run without buildings
        self.assertEqual(task.changed_keys(), ["100002", "100003"])

    def test_buildings_in_the_excel_report(self):
        task = self.building_task()
        self.assertEqual(xlsx.sheet_names(task.excel_path),
                         ["Sumar", "Apărute", "Modificate", "Chei duplicate",
                          "Construcții apărute", "Construcții dispărute",
                          "Construcții modificate"])
        _names, detail = xlsx.read_rows(task.excel_path, "Construcții modificate")
        self.assertIn(["100002 C1", "Doar datele", "destinatie", "CL", "CIE"],
                      [r[:5] for r in detail])
        _names, summary = xlsx.read_rows(task.excel_path, "Sumar")
        self.assertIn("Construcții", [r[0] for r in summary if r])

    def test_value_rules(self):
        differ = compare_service.values_differ
        self.assertFalse(differ(12, 12.0))
        self.assertFalse(differ(None, ""))
        self.assertFalse(differ(" a ", "a"))
        self.assertFalse(differ(100001.0, "100001"))
        self.assertTrue(differ("0123", "123"))
        self.assertTrue(differ("a", "b"))
        self.assertEqual(compare_service.normalize_key(100001.0), "100001")
        self.assertIsNone(compare_service.normalize_key("  "))

    def test_cgxml_columns_compare_by_what_they_hold(self):
        differ = compare_service.values_differ
        # the parcels of an imobil in another order, or one parcel holding several numbers
        self.assertFalse(differ("CC, A", "A, CC", column="categ_fol"))
        self.assertFalse(differ("305, 306, 307", "305, 306, 307, 305,306,307",
                                column="nr_parcele"))
        self.assertTrue(differ("CC", "A, CC", column="categ_fol"))
        self.assertTrue(differ("CC, A", "A, CC"))
        # numbers typed by hand: case and spaces carry no meaning, nor the column's own letter
        self.assertFalse(differ("4417 N Cristian", "4417  N CRISTIAN", column="nr_cf_vechi"))
        self.assertFalse(differ("93 N Cristian", "93N Cristian", column="nr_cf_vechi"))
        self.assertFalse(differ("731/4B", "731/4b", column="nr_parcele"))
        self.assertFalse(differ("T7", "7", column="nr_tarla"))
        self.assertFalse(differ("P 45, P 46", "45, 46", column="nr_parcele"))
        self.assertFalse(differ("CF 4417 N Cristian", "4417 N CRISTIAN", column="nr_cf_vechi"))
        self.assertFalse(differ("2210.", "2210", column="nr_cf_vechi"))
        self.assertTrue(differ("1731/4", "731/4", column="nr_parcele"))
        # the use category written before the parcel number
        self.assertFalse(differ("A 88/12/3, V90/7, din A 88/12/5", "88/12/3, 90/7, 88/12/5",
                                column="nr_parcele"))
        self.assertTrue(differ("A 88/12/3", "88/12/4", column="nr_parcele"))
        # the electronic CF number eTerra writes after the old one
        self.assertFalse(differ("560/N(e:50311)", "560/N", column="nr_cf_vechi"))
        self.assertTrue(differ("560/N(e:50311)", "561/N", column="nr_cf_vechi"))
        # a dash, a dot, a zero or N/A stands for no number
        for placeholder in ("-", ".", "0", "N/A"):
            self.assertFalse(differ(placeholder, None, column="nr_titlu"), placeholder)
        self.assertTrue(differ("-", "59", column="nr_tarla"))
        # owners in another order; their names still follow the case option
        self.assertFalse(differ("POPESCU ION, IONESCU ANA", "IONESCU ANA, POPESCU ION",
                                column="propr"))
        self.assertTrue(differ("Popescu Ion", "POPESCU ION", column="propr"))
        self.assertFalse(differ("Popescu Ion", "POPESCU ION", True, column="propr"))
        # the other persons are listed with their role, which can hold a comma
        self.assertFalse(differ("A (ipotecă); B (notare)", "B (notare); A (ipotecă)",
                                column="alte_pers"))
        self.assertTrue(differ("A (proprietate, C1); B (proprietate, C2)",
                               "A (proprietate, C2); B (proprietate, C1)", column="alte_pers"))


class BuildingTopologyTests(unittest.TestCase):

    def setUp(self):
        # two neighbouring parcels, 0..100 and 100..200
        self.imobile = helpers.polygon_layer("Imobile", [
            (square(0, 0, 100), ["P1", "POPESCU"]),
            (square(100, 0, 100), ["P2", "IONESCU"]),
        ], fields="cod:string,propr:string")
        self.constructii = helpers.polygon_layer("Construcții", [
            (square(10, 10, 20), ["C1"]),                 # inside P1
            (square(90, 10, 20), ["C2"]),                 # half on P1, half on P2
            (square(95, 50, 20), ["C3"]),                 # mostly in P2, 100 mp over into P1
            (square(500, 500, 10), ["C4"]),               # on no parcel
            # within 0.5 mm of the boundary
            ("POLYGON((80 80, 99 80, 99.9995 90, 99 99, 80 99, 80 80))", ["C5"]),
        ], fields="nr:string")

    def run_task(self, threshold=50.0, join=True, buffer_m=0.002):
        imobile = [LayerSnapshot(self.imobile)]
        join_fields = [merged_fields(imobile).field("cod")] if join else []
        task = bts.BuildingTopologyTask(
            imobile=imobile, constructii=[LayerSnapshot(self.constructii)],
            buffer_m=buffer_m, threshold=threshold, join_fields=join_fields)
        self.assertTrue(task.run(), task.error_msg)
        return {row["attrs"][0]: row for row in task.results[0]}, task

    def test_a_building_without_geometry_is_copied(self):
        from qgis.core import QgsFeature  # type: ignore
        feature = QgsFeature(self.constructii.fields())
        feature.setAttributes(["C6"])
        self.constructii.dataProvider().addFeatures([feature])
        rows, task = self.run_task()
        self.assertEqual(rows["C6"]["stare"], bts.NO_GEOMETRY)
        self.assertEqual(task.counts[bts.NO_GEOMETRY], 1)
        fields, _names, _join = bts.output_fields(self.constructii.fields(), [])
        features = bts.result_features(task.results[0], fields, [], self.constructii.wkbType())
        self.assertEqual(len(features), 6)
        self.assertFalse([f for f in features if f["nr"] == "C6"][0].hasGeometry())

    def test_states(self):
        rows, task = self.run_task()
        self.assertEqual(rows["C1"]["stare"], bts.UNCHANGED)
        self.assertEqual(rows["C4"]["stare"], bts.NO_PARCEL)
        # C3 belongs to P2, where most of it stands, and loses its 100 mp in P1, over the
        # 50 mp threshold
        self.assertEqual(rows["C3"]["stare"], bts.LOSS)
        self.assertEqual(rows["C3"]["join"]["cod"], "P2")
        self.assertAlmostEqual(rows["C3"]["sup_pierduta"], 100.0, delta=0.1)
        self.assertEqual(sum(task.counts.values()), 5)

    def test_cut_stays_inside_the_parcel_by_the_buffer(self):
        rows, _task = self.run_task()
        cut = rows["C3"]["geometry"]
        parcel = QgsGeometry.fromWkt(square(100, 0, 100))
        self.assertTrue(cut.within(parcel))
        self.assertGreater(cut.boundingBox().xMinimum(), 100.0)

    def test_a_sub_millimeter_overhang_is_corrected_not_flagged(self):
        rows, _task = self.run_task()
        self.assertEqual(rows["C5"]["stare"], bts.UNCHANGED)

    def test_split_building_reports_its_pieces(self):
        # a parcel with a notch, so cutting the building leaves two pieces
        notched = helpers.polygon_layer("Imobile", [(
            "POLYGON((0 0, 100 0, 100 100, 60 100, 60 40, 40 40, 40 100, 0 100, 0 0))", ["N"])],
            fields="cod:string")
        building = helpers.polygon_layer("C", [(square(30, 50, 40), ["C6"])], fields="nr:string")
        task = bts.BuildingTopologyTask(
            imobile=[LayerSnapshot(notched)], constructii=[LayerSnapshot(building)],
            buffer_m=0.002, threshold=1.0, join_fields=[])
        self.assertTrue(task.run(), task.error_msg)
        row = task.results[0][0]
        self.assertEqual(row["stare"], bts.LOSS)
        self.assertIn("2 părți", row["obs_topo"])

    def _linked(self, rows_wkt, link_field="imobil", key_field="cod"):
        constructii = helpers.polygon_layer("Construcții", rows_wkt,
                                            fields="nr:string,imobil:string")
        task = bts.BuildingTopologyTask(
            imobile=[LayerSnapshot(self.imobile)],
            constructii=[LayerSnapshot(constructii)],
            buffer_m=0.002, threshold=50.0, join_fields=[], link_field=link_field,
            key_field=key_field)
        self.assertTrue(task.run(), task.error_msg)
        return {row["attrs"][0]: row for row in task.results[0]}

    def test_link_keeps_a_building_off_the_wrong_imobil(self):
        # C3 stands mostly on P2, but its column holds P1
        rows = self._linked([(square(95, 50, 20), ["C3", "P1"]),
                             (square(95, 10, 20), ["C7", "P2"])])
        self.assertEqual(rows["C3"]["stare"], bts.ELSEWHERE)
        self.assertIn("imobilul P2", rows["C3"]["obs_topo"])
        self.assertEqual(rows["C3"]["sup_inainte"], rows["C3"]["sup_dupa"])
        self.assertTrue(rows["C3"]["geometry"].equals(QgsGeometry.fromWkt(square(95, 50, 20))))
        # C7 is linked to the imobil it mostly stands on, so it is cut
        self.assertEqual(rows["C7"]["stare"], bts.LOSS)

    def test_unknown_link_falls_back_to_the_overlap(self):
        rows = self._linked([(square(95, 50, 20), ["C3", "P9"])])
        self.assertEqual(rows["C3"]["stare"], bts.LOSS)
        self.assertIn("Imobilul 'P9' nu a fost găsit", rows["C3"]["obs_topo"])

    def test_link_without_key_column_is_ignored(self):
        rows = self._linked([(square(95, 50, 20), ["C3", "P1"])], key_field="")
        self.assertEqual(rows["C3"]["stare"], bts.LOSS)

    def test_output_fields_never_collide(self):
        source = helpers.polygon_layer("x", [], fields="stare:string,imb_cod:string").fields()
        fields, names, join = bts.output_fields(source, [merged_fields(
            [LayerSnapshot(self.imobile)]).field("cod")])
        self.assertEqual(names["stare"], "stare_2")
        self.assertEqual(join["cod"], "imb_cod_2")
        self.assertEqual(len(set(fields.names())), fields.count())


class StyleTests(unittest.TestCase):

    def test_converted_layers_are_labelled_only_where_labels_can_be_read(self):
        # at a whole UAT, 10,000 labels cover the map and take ten times longer to draw
        from cadex.services import layer_style
        for which, scale in (("imobile", 10000), ("constructii", 2500)):
            layer = helpers.polygon_layer("x", [], fields="id:integer")
            layer_style.style_cadastral(layer, which, [("id", "ID ")])
            settings = layer.labeling().settings()
            self.assertTrue(settings.scaleVisibility, which)
            self.assertEqual(settings.minimumScale, scale, which)
            self.assertTrue(layer.labelsEnabled())

    @unittest.skipUnless(sys.platform == "win32", "Qt's headless fallback font is a Windows gap")
    def test_labels_made_without_a_screen_use_a_font_windows_has(self):
        # run without a screen, as here, Qt names Helvetica, and every project or file with
        # the label would warn that the font is missing
        from cadex.services import layer_style
        layer = helpers.polygon_layer("x", [], fields="id:integer")
        layer_style.style_cadastral(layer, "imobile", [("id", "ID ")])
        self.assertEqual(layer.labeling().settings().format().font().family(), "Arial")


class ServicesTaskTests(unittest.TestCase):
    """What every tool's task does with a file it cannot write or open."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_task_")
        self.path = os.path.join(self.tmp, "raport.xlsx")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def task(self, write):
        from cadex.services.tasks import CadexTask

        class Task(CadexTask):
            def run(task):
                try:
                    task.findings = ["suprapunere"]
                    task.write_report(write, self.path)
                    return True
                except Exception as error:
                    return task.failed("Test", error)
        return Task("CADex: test")

    def test_a_report_that_cannot_be_written_keeps_the_runs_results(self):
        def open_in_excel():
            raise PermissionError(13, "Permission denied", self.path)
        task = self.task(open_in_excel)
        self.assertTrue(task.run())
        self.assertEqual(task.findings, ["suprapunere"])
        self.assertIsNone(task.error_msg)
        self.assertTrue(task.report_error.startswith(
            "Fișierul 'raport.xlsx' nu poate fi scris: este deschis în alt program"),
            task.report_error)

    @unittest.skipUnless(os.name == "nt", "Excel's way of holding a file is a Windows lock")
    def test_a_workbook_open_in_excel_is_reported_and_kept(self):
        from test_pure_services import hold_like_excel
        workbook = xlsx.Workbook()
        workbook.add_sheet("Raportul de ieri")
        workbook.save(self.path)
        report = xlsx.Workbook()
        report.add_sheet("Raportul de azi")
        release = hold_like_excel(self.path)
        try:
            task = self.task(lambda: report.save(self.path))
            self.assertTrue(task.run())
        finally:
            release()
        self.assertIn("este deschis în alt program", task.report_error)
        self.assertEqual(xlsx.sheet_names(self.path), ["Raportul de ieri"])

    def test_a_file_that_cannot_be_written_fails_the_run_in_romanian(self):
        from cadex.services import outputs
        from cadex.services.tasks import CadexTask
        task = CadexTask("CADex: test")
        task.excel_path = self.path
        task.gpkg_path = os.path.join(self.tmp, "imobile.gpkg")
        locked = PermissionError(13, "Permission denied", self.path)
        self.assertFalse(task.failed("Test", locked))
        self.assertEqual(task.error_msg, outputs.write_error(self.path, locked))
        self.assertNotIn("Errno", task.error_msg)
        # os.replace names the temporary file first and the user's file second
        moved = PermissionError(13, "Access is denied", task.gpkg_path + ".tmp", None,
                                task.gpkg_path)
        task.failed("Test", moved)
        self.assertIn("'imobile.gpkg' nu poate fi scris", task.error_msg)
        task.excel_path = os.path.join(self.tmp, "lipsa", "raport.xlsx")
        missing = FileNotFoundError(2, "No such file or directory", task.excel_path)
        task.failed("Test", missing)
        self.assertIn("folderul lui nu există", task.error_msg)
        # an error that names no file keeps its own text
        task.failed("Test", OSError("Creation of field id failed"))
        self.assertEqual(task.error_msg, "Creation of field id failed")

    def test_an_input_that_cannot_be_opened_is_not_called_unwritable(self):
        from cadex.services.tasks import CadexTask
        task = CadexTask("CADex: test")
        task.excel_path = self.path
        archive = os.path.join(self.tmp, "livrare.zip")
        task.failed("Test", PermissionError(13, "Permission denied", archive))
        self.assertEqual(task.error_msg, (
            "Fișierul 'livrare.zip' nu poate fi deschis: este folosit de alt program sau nu "
            "aveți drept de acces la el."))
        task.failed("Test", PermissionError(13, "Permission denied", self.tmp))
        self.assertTrue(task.error_msg.startswith(
            f"Folderul '{os.path.basename(self.tmp)}' nu poate fi deschis"), task.error_msg)
        task.failed("Test", FileNotFoundError(2, "No such file or directory", archive))
        self.assertEqual(task.error_msg, "Fișierul 'livrare.zip' nu a fost găsit.")


class ServicesMergedLayersTests(unittest.TestCase):
    """Raport Intravilan over several imobile layers whose columns differ in details."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_merged_")
        self.intra = helpers.polygon_layer("Intravilan", [(square(-10, -10, 1000), ["T"])],
                                           fields="nume:string")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def run_task(self, layers, id_field):
        gpkg = os.path.join(self.tmp, "raport.gpkg")
        task = intravilan_service.IntravilanTask(
            imobile=[LayerSnapshot(layer) for layer in layers],
            intravilan=[LayerSnapshot(self.intra)], excel_fields=[id_field], id_field=id_field,
            owner_field=None, tolerance=0.5, gpkg_path=gpkg)
        self.assertTrue(task.run(), task.error_msg)
        return QgsVectorLayer(f"{gpkg}|layername={task.table}", "r", "ogr")

    def test_two_sector_geopackages_give_one_report_with_all_their_imobile(self):
        sectors = [helpers.gpkg_layer(helpers.polygon_layer(
            f"Imobile sector {n}", [(square(200 * i, 300 * n, 100), [f"{n}0{i}"])
                                    for i in range(1, n + 2)], fields="id:string"),
            os.path.join(self.tmp, f"sector{n}.gpkg")) for n in (1, 2)]
        report = self.run_task(sectors, "id")
        self.assertEqual(sorted(f["id"] for f in report.getFeatures()),
                         ["101", "102", "201", "202", "203"])

    def test_columns_spelled_or_sized_differently_hold_every_value(self):
        long_owner = "POPESCU ION, POPESCU MARIA"
        first = helpers.polygon_layer("Imobile S1", [(square(0, 0, 100), ["A", "SCURT"])],
                                      fields="ID:string,propr:string(10)")
        second = helpers.polygon_layer("Imobile S2", [(square(200, 0, 100), ["B", long_owner]),
                                                      (square(400, 0, 100), ["C", "X"])],
                                       fields="id:string,PROPR:string(100)")
        report = self.run_task([first, second], "ID")
        self.assertEqual([n for n in report.fields().names() if n.lower() in ("id", "propr")],
                         ["ID", "propr"])
        rows = sorted((f["ID"], f["propr"]) for f in report.getFeatures())
        self.assertEqual(rows, [("A", "SCURT"), ("B", long_owner), ("C", "X")])

    def test_the_excel_report_holds_every_layers_values_whatever_their_spelling(self):
        first = helpers.polygon_layer("Imobile S1", [(square(0, 0, 100), ["A", "POPESCU"])],
                                      fields="ID:string,propr:string")
        # B lies partly outside the intravilan, which ends at x = 990
        second = helpers.polygon_layer("Imobile S2", [(square(950, 0, 100), ["B", "IONESCU"])],
                                       fields="id:string,PROPR:string")
        excel = os.path.join(self.tmp, "raport.xlsx")
        task = intravilan_service.IntravilanTask(
            imobile=[LayerSnapshot(first), LayerSnapshot(second)],
            intravilan=[LayerSnapshot(self.intra)], excel_fields=["ID", "propr"], id_field="ID",
            owner_field="propr", tolerance=0.5, excel_path=excel)
        self.assertTrue(task.run(), task.error_msg)
        # both owners are filled in, so no imobil is listed as without an identified owner
        self.assertNotIn("Proprietari neidentificați", xlsx.sheet_names(excel))
        _names, rows = xlsx.read_rows(excel, "Toate")
        self.assertEqual(rows[0][:3], ["Layer", "ID", "propr"])
        self.assertEqual(sorted(tuple(row[1:3]) for row in rows[1:]),
                         [("A", "POPESCU"), ("B", "IONESCU")])
        # the Sumar names the partial imobil by its ID, not by its row number
        _names, summary = xlsx.read_rows(excel, "Sumar")
        header = next(i for i, row in enumerate(summary) if row and row[0] == "Imobil")
        self.assertEqual(summary[header + 1][0], "B")

    def test_buildings_take_the_column_of_their_imobil_whatever_its_spelling(self):
        first = helpers.polygon_layer("Imobile S1", [(square(0, 0, 100), ["P1"])],
                                      fields="COD:string")
        second = helpers.polygon_layer("Imobile S2", [(square(200, 0, 100), ["P2"])],
                                       fields="cod:string")
        constructii = helpers.polygon_layer("Construcții", [(square(10, 10, 20), ["C1"]),
                                                            (square(210, 10, 20), ["C2"])],
                                            fields="nr:string")
        imobile = [LayerSnapshot(first), LayerSnapshot(second)]
        task = bts.BuildingTopologyTask(
            imobile=imobile, constructii=[LayerSnapshot(constructii)], buffer_m=0.002,
            threshold=50.0, join_fields=[merged_fields(imobile).field("COD")])
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual({row["attrs"][0]: row["join"].get("COD") for row in task.results[0]},
                         {"C1": "P1", "C2": "P2"})


class BuildingTopologyCutTests(unittest.TestCase):
    """Imobil 1 from x 0 to 20, imobil 2 from x 20 to 40."""

    def setUp(self):
        self.imobile = helpers.polygon_layer("Imobile", [
            ("POLYGON((0 0, 20 0, 20 20, 0 20, 0 0))", ["1"]),
            ("POLYGON((20 0, 40 0, 40 20, 20 20, 20 0))", ["2"]),
        ], fields="cod:string")

    def run_task(self, rows, geometry="MultiPolygon", link=False):
        constructii = helpers.polygon_layer("Construcții", rows, fields="nr:string,imobil:string",
                                            geometry=geometry)
        task = bts.BuildingTopologyTask(
            imobile=[LayerSnapshot(self.imobile)], constructii=[LayerSnapshot(constructii)],
            buffer_m=0.002, threshold=1.0, join_fields=[],
            link_field="imobil" if link else None, key_field="cod" if link else None)
        self.assertTrue(task.run(), task.error_msg)
        return {row["attrs"][0]: row for row in task.results[0]}

    def test_each_part_of_a_multipart_building_is_cut_on_its_own(self):
        # a 4 x 4 m body inside imobil 1 and a 5 x 5 m body 5 cm over its limit
        rows = self.run_task([("MULTIPOLYGON(((2 2, 6 2, 6 6, 2 6, 2 2)),"
                               "((15 5, 20.05 5, 20.05 10, 15 10, 15 5)))", ["C1", None])])
        row = rows["C1"]
        self.assertEqual(row["stare"], bts.FIXED)
        self.assertAlmostEqual(row["sup_dupa"], 16 + 4.998 * 5, places=3)
        self.assertEqual(len(row["geometry"].asGeometryCollection()), 2)
        self.assertNotIn("părți", row["obs_topo"])

    def test_an_invalid_building_is_reported_as_invalid_and_left_as_it_is(self):
        bow_tie = "POLYGON((25 5, 30 10, 30 5, 25 10, 25 5))"
        rows = self.run_task([(bow_tie, ["C1", None])], geometry="Polygon")
        row = rows["C1"]
        self.assertEqual(row["stare"], bts.FAILED)
        self.assertEqual(row["obs_topo"], "Geometrie invalidă: conturul se intersectează cu el "
                                          "însuși în punctul 27,5 7,5. Nu a fost tăiată.")
        self.assertAlmostEqual(row["sup_inainte"], 12.5, places=4)
        self.assertTrue(row["geometry"].equals(QgsGeometry.fromWkt(bow_tie)))

    def test_an_unknown_link_on_no_imobil_is_noted_only_as_not_found(self):
        rows = self.run_task([("POLYGON((120 5, 125 5, 125 10, 120 10, 120 5))", ["C1", "9"]),
                              ("POLYGON((15 5, 20.05 5, 20.05 10, 15 10, 15 5))", ["C2", "9"])],
                             geometry="Polygon", link=True)
        self.assertEqual(rows["C1"]["stare"], bts.NO_PARCEL)
        self.assertEqual(rows["C1"]["obs_topo"],
                         "Nu se suprapune cu niciun imobil. Imobilul '9' nu a fost găsit.")
        self.assertIn("Imobilul '9' nu a fost găsit; s-a folosit imobilul pe care se află.",
                      rows["C2"]["obs_topo"])


class IntravilanReportTests(unittest.TestCase):
    """Raport Intravilan / Extravilan for imobile along the intravilan limit, and its files."""

    SPIKE = "POLYGON((0 0, 10 0, 10 10, 5 10, 5 20, 5 10, 0 10, 0 0))"
    FLAT_AND_SQUARE = "MULTIPOLYGON(((20 0, 30 0, 40 0, 20 0)),((50 0, 60 0, 60 10, 50 10, 50 0)))"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_intrarep_")
        self.intra = helpers.polygon_layer("Intravilan", [(square(0, 0, 1000), ["T"])],
                                           fields="nume:string")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def run_task(self, rows, geometry="Polygon", **options):
        imobile = helpers.polygon_layer("Imobile", rows, fields="cod:string",
                                        geometry=geometry)
        settings = dict(imobile=[LayerSnapshot(imobile)], intravilan=[LayerSnapshot(self.intra)],
                        excel_fields=["cod"], id_field="cod", owner_field=None, tolerance=0.5)
        settings.update(options)
        task = intravilan_service.IntravilanTask(**settings)
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_an_imobil_within_the_tolerance_of_the_limit_has_no_extravilan_metre(self):
        # 100.6 mp, of which 0.2 mp lie west of the limit
        row = self.run_task([("POLYGON((-0.2 0, 100.4 0, 100.4 1, -0.2 1, -0.2 0))",
                              ["A"])]).rows[0]
        self.assertEqual(row["situatie"], intravilan_service.INTEGRAL_INTRA)
        self.assertEqual((row["sup_totala"], row["sup_intra"], row["sup_extra"]), (101, 101, 0))

    def test_a_small_imobil_outside_the_intravilan_is_extravilan(self):
        row = self.run_task([(square(2000, 0, 0.6), ["fântână"])]).rows[0]
        self.assertEqual(row["situatie"], intravilan_service.INTEGRAL_EXTRA)
        # both parts within a large tolerance: the larger part gives the status
        self.assertEqual(intravilan_service.classify(8.0, 0.0, 10.0),
                         intravilan_service.INTEGRAL_EXTRA)
        self.assertEqual(intravilan_service.classify(8.0, 7.0, 10.0),
                         intravilan_service.INTEGRAL_INTRA)

    def test_repaired_imobile_reach_the_geopackage_as_polygons(self):
        from qgis.core import QgsWkbTypes  # type: ignore
        gpkg = os.path.join(self.tmp, "raport.gpkg")
        task = self.run_task([(self.SPIKE, ["spic"]), (self.FLAT_AND_SQUARE, ["plat"]),
                              (square(100, 0, 10), ["patrat"])],
                             geometry="MultiPolygon", gpkg_path=gpkg)
        self.assertEqual([QgsWkbTypes.displayString(f.geometry().wkbType())
                          for f in task.output_features()], ["MultiPolygon"] * 3)
        layer = QgsVectorLayer(f"{gpkg}|layername={task.table}", "r", "ogr")
        self.assertEqual({f["cod"]: round(f.geometry().area()) for f in layer.getFeatures()},
                         {"spic": 100, "plat": 100, "patrat": 100})

    def test_a_spike_in_one_part_keeps_the_other_parts_of_a_contour_and_an_imobil(self):
        from qgis.core import QgsWkbTypes  # type: ignore
        # two bodies of intravilan, the first with a spike on its top edge
        self.intra = helpers.polygon_layer(
            "Intravilan", [("MULTIPOLYGON(((0 0, 100 0, 100 100, 50 100, 50 130, 50 100, "
                            "0 100, 0 0)),((300 0, 400 0, 400 100, 300 100, 300 0)))", ["T"])],
            fields="nume:string", geometry="MultiPolygon")
        # an imobil in each body, and one with a part in each, the first part with a spike
        two_parts = ("MULTIPOLYGON(((40 40, 60 40, 60 60, 50 60, 50 70, 50 60, 40 60, 40 40)),"
                     "((350 50, 360 50, 360 60, 350 60, 350 50)))")
        gpkg = os.path.join(self.tmp, "raport.gpkg")
        task = self.run_task([(square(10, 10, 20), ["1"]), (square(310, 10, 20), ["2"]),
                              (two_parts, ["3"])], geometry="MultiPolygon", gpkg_path=gpkg)
        self.assertEqual(task.skipped, 0)
        self.assertEqual({row["attrs"]["cod"]: row["situatie"] for row in task.rows},
                         dict.fromkeys("123", intravilan_service.INTEGRAL_INTRA))
        layer = QgsVectorLayer(f"{gpkg}|layername={task.table}", "r", "ogr")
        self.assertEqual({f["cod"]: (QgsWkbTypes.displayString(f.geometry().wkbType()),
                                     round(f.geometry().area()))
                          for f in layer.getFeatures()},
                         {"1": ("MultiPolygon", 400), "2": ("MultiPolygon", 400),
                          "3": ("MultiPolygon", 500)})

    def test_an_existing_geopackage_keeps_its_other_layers(self):
        from qgis.core import QgsCoordinateTransformContext, QgsVectorFileWriter  # type: ignore
        gpkg = os.path.join(self.tmp, "proiect.gpkg")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = "limite"
        QgsVectorFileWriter.writeAsVectorFormatV3(
            helpers.polygon_layer("Limite", [(square(0, 0, 50), [1]), (square(60, 0, 50), [2])]),
            gpkg, QgsCoordinateTransformContext(), options)
        rows = [(square(0, 0, 10), ["A"]), (square(2000, 0, 10), ["B"])]
        for _run in range(2):
            task = self.run_task(rows, gpkg_path=gpkg)
        report = QgsVectorLayer(f"{gpkg}|layername={task.table}", "r", "ogr")
        self.assertEqual(sorted(f["cod"] for f in report.getFeatures()), ["A", "B"])
        self.assertEqual(QgsVectorLayer(f"{gpkg}|layername=limite", "l", "ogr").featureCount(), 2)

    def test_the_excel_report_has_a_sheet_per_status(self):
        excel = os.path.join(self.tmp, "raport.xlsx")
        self.run_task([(square(0, 0, 10), ["intra"]), (square(995, 0, 10), ["partial"]),
                       (square(2000, 0, 10), ["extra"])], excel_path=excel)
        self.assertEqual(xlsx.sheet_names(excel), ["Sumar", "Toate"]
                         + list(intravilan_service.STATUSES))
        for status, cod in zip(intravilan_service.STATUSES, ("intra", "partial", "extra")):
            _names, rows = xlsx.read_rows(excel, status)
            self.assertEqual([row[0] for row in rows[1:]], [cod], status)

    def test_a_report_that_cannot_be_written_keeps_the_layer(self):
        gpkg = os.path.join(self.tmp, "raport.gpkg")
        # nobody can open a folder for writing, just as nobody can write a workbook open in
        # Excel
        excel = os.path.join(self.tmp, "raport.xlsx")
        os.makedirs(excel)
        task = self.run_task([(square(0, 0, 10), ["A"])], gpkg_path=gpkg, excel_path=excel)
        self.assertTrue(task.report_error.startswith(
            "Fișierul 'raport.xlsx' nu poate fi scris"), task.report_error)
        self.assertEqual(QgsVectorLayer(f"{gpkg}|layername={task.table}", "r", "ogr")
                         .featureCount(), 1)

    def test_imobile_the_geopackage_refuses_are_counted(self):
        from unittest import mock
        from qgis.core import QgsVectorFileWriter  # type: ignore
        create = QgsVectorFileWriter.create

        class Refusing:
            """A GeoPackage writer that refuses the second feature."""

            def __init__(self, writer):
                self.writer = writer
                self.offered = 0

            def __getattr__(self, name):
                return getattr(self.writer, name)

            def addFeature(self, feature, *args):
                self.offered += 1
                return self.offered != 2 and self.writer.addFeature(feature, *args)

            def errorMessage(self):
                return "UNIQUE constraint failed: raport_intravilan.fid"

        def refusing(*args):
            return Refusing(create(*args))

        gpkg = os.path.join(self.tmp, "raport.gpkg")
        rows = [(square(i * 20, 0, 10), [str(i)]) for i in range(3)]
        with mock.patch.object(intravilan_service.QgsVectorFileWriter, "create", refusing):
            task = self.run_task(rows, gpkg_path=gpkg)
        self.assertEqual((task.written, task.expected), (2, 3))
        self.assertEqual(task.write_error, "UNIQUE constraint failed: raport_intravilan.fid")


class CompareEngineLayerTests(unittest.TestCase):
    """The layer comparison's results as the window and Processing receive them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_engine_")
        # 4 rows, one without a key; 5 rows, key 100004 twice
        self.old = helpers.polygon_layer("vechi", [
            (square(0, 0, 10), [100001, "POPESCU"]), (square(20, 0, 10), [100002, "IONESCU"]),
            (square(40, 0, 10), [100003, "VASILE"]), (square(60, 0, 10), [None, "FARA"]),
        ], fields="id:integer,propr:string")
        self.new = helpers.polygon_layer("nou", [
            (square(0, 0, 10), [100001, "POPESCU"]), (square(20, 0, 11), [100002, "ION"]),
            (square(40, 0, 10), [100003, "VASILE"]), (square(80, 0, 10), [100004, "NOU"]),
            (square(80, 0, 10), [100004, "NOU DUBLU"]),
        ], fields="id:integer,propr:string")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def task(self, old=None, new=None, **overrides):
        options = dict(old=LayerSnapshot(old or self.old), new=LayerSnapshot(new or self.new),
                       key_old="id", key_new="id", fields=["propr"], compare_geometry=True,
                       tolerance=0.1, excel_path=os.path.join(self.tmp, "cmp.xlsx"))
        options.update(overrides)
        return compare_service.CompareTask(**options)

    def test_a_report_that_cannot_be_written_keeps_the_comparison(self):
        task = self.task(excel_path=os.path.join(self.tmp, "lipsa", "cmp.xlsx"))
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(task.added, ["100004"])
        self.assertEqual(task.changed_keys(), ["100002"])
        self.assertIn("'cmp.xlsx' nu poate fi scris", task.report_error)

    def test_the_record_counts_are_the_rows_of_each_layer(self):
        task = self.task()
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual((task.old_read, task.new_read), (4, 5))
        _names, summary = xlsx.read_rows(task.excel_path, "Sumar")
        rows = {r[0]: r[1] for r in summary if r}
        self.assertEqual(rows["Înregistrări în layerul vechi"], 4)
        self.assertEqual(rows["Înregistrări în layerul nou"], 5)

    def test_a_column_one_layer_lacks_stops_the_run_with_its_name(self):
        old = helpers.polygon_layer("vechi", [(square(0, 0, 10), [1, "A", "x"])],
                                    fields="id:integer,propr:string,nota:string")
        task = self.task(old, fields=["propr", "nota"])
        self.assertFalse(task.run())
        self.assertEqual(task.error_msg, "Layerul 'nou' nu are coloana 'nota'.")
        # a key column renamed after it was chosen
        task = self.task(key_new="nr")
        self.assertFalse(task.run())
        self.assertEqual(task.error_msg, "Layerul 'nou' nu are coloana 'nr'.")
        self.assertFalse(os.path.exists(task.excel_path))

    def test_text_keys_with_superscript_digits_are_compared(self):
        old = helpers.polygon_layer("vechi", [(square(0, 0, 10), ["lot 2²"]),
                                              (square(20, 0, 10), ["lot 10"])],
                                    fields="id:string")
        new = helpers.polygon_layer("nou", [(square(0, 0, 10), ["lot 2²"]),
                                            (square(20, 0, 10), ["lot 10"]),
                                            (square(40, 0, 10), ["lot 2"])], fields="id:string")
        task = self.task(old, new, fields=[])
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(task.added, ["lot 2"])
        self.assertEqual(sorted(["lot 10", "lot 2²", "lot 2"], key=compare_service.sort_key),
                         ["lot 2", "lot 2²", "lot 10"])

    def test_the_use_category_na_is_a_value(self):
        differ = compare_service.values_differ
        # NA is the code of Nedeterminată, not "not available"
        self.assertTrue(differ("CC, NA", "CC", column="categ_fol"))
        self.assertTrue(differ("NA", None, column="categ_fol"))
        self.assertFalse(differ("NA, CC", "cc, na", column="categ_fol"))
        self.assertFalse(differ("N/A", None, column="nr_titlu"))

    def test_the_result_features_of_each_category(self):
        from qgis.core import QgsFeature  # type: ignore
        table = QgsVectorLayer("None?field=id:integer&field=propr:string", "tabel", "memory")
        row = QgsFeature(table.fields())
        row.setAttributes([100009, "VECHI"])
        table.dataProvider().addFeatures([row])
        task = self.task(old=table, compare_geometry=False)
        self.assertTrue(task.run(), task.error_msg)
        # the new layer's records keep their geometry though the old layer is a table
        added = task.result_features(compare_service.ADDED)
        self.assertEqual([f["id"] for f in added.features], [100001, 100002, 100003, 100004])
        self.assertTrue(all(f.hasGeometry() for f in added.features))
        self.assertEqual(added.wkb_type, self.new.wkbType())
        deleted = task.result_features(compare_service.DELETED)
        self.assertEqual([f["id"] for f in deleted.features], [100009])
        self.assertEqual(deleted.wkb_type, table.wkbType())
        self.assertIsNone(deleted.kind_field)

    def test_changed_features_name_their_kind_field(self):
        new = helpers.polygon_layer("nou", [(square(0, 0, 10), [100001, "POPESCU", "x"]),
                                            (square(20, 0, 11), [100002, "ION", "y"])],
                                    fields="id:integer,propr:string,tip_modif:string")
        task = self.task(new=new)
        self.assertTrue(task.run(), task.error_msg)
        changed = task.result_features(compare_service.CHANGED)
        self.assertEqual(changed.kind_field, "tip_modif_2")
        self.assertEqual([(f["id"], f["tip_modif_2"], f["modificari"]) for f in changed.features],
                         [(100002, "Limita și datele", "propr, geometrie")])
        self.assertEqual(changed.left_out, 0)


class CompareEngineKeyTests(unittest.TestCase):
    """The key columns the comparator pairs two layers on."""

    @staticmethod
    def plain(name, rows):
        return helpers.polygon_layer(name, [(square(20 * i, 0, 10), row)
                                            for i, row in enumerate(rows)],
                                     fields="id:integer,ie:integer,propr:string")

    @staticmethod
    def converted(name, row, batch):
        from cadex.services import roles
        layer = helpers.polygon_layer(name, [(square(0, 0, 10), row)],
                                      fields="ID_IMOB:integer,ie:integer,sector:integer")
        roles.mark_columns(layer, [("id", "ID_IMOB"), ("ie", "ie"), ("sector", "sector")])
        roles.mark_batch(layer, batch)
        return layer

    def test_plain_layers_pair_on_the_column_with_fewer_empty_values(self):
        old = self.plain("v1", [[1, 70001, "A"], [2, 70002, "B"]])
        # the imobile added in v2 have no IE yet
        new = self.plain("v2", [[1, 70001, "A"], [2, 70002, "B"], [3, None, "C"], [4, None, "D"]])
        self.assertEqual(compare_service.key_columns(old, new), ("id", "id"))
        # an ID column left mostly empty, an IE filled on every row
        old = self.plain("v1", [[None, 70001, "A"], [None, 70002, "B"], [3, 70003, "C"]])
        new = self.plain("v2", [[None, 70001, "A"], [None, 70002, "B"], [3, 70003, "C"]])
        self.assertEqual(compare_service.key_columns(old, new), ("ie", "ie"))
        # both filled on every row: the ID
        old = self.plain("v1", [[1, 70001, "A"], [2, 70002, "B"]])
        new = self.plain("v2", [[1, 70001, "A"], [2, 70002, "B"]])
        self.assertEqual(compare_service.key_columns(old, new), ("id", "id"))

    def test_converted_layers_pair_as_their_files(self):
        first = self.converted("livrare 1", [9001, None, 3], "20260101000000000000")
        second = self.converted("livrare 2", [9001, None, 3], "20260102000000000000")
        # the column layout of the conversion renamed the ID
        self.assertEqual(compare_service.key_columns(first, second), ("ID_IMOB", "ID_IMOB"))
        # an eTerra export has no sectors, and its cadastral numbers are not delivery IDs
        eterra = self.converted("eterra", [9001, 70001, None], "20260103000000000000")
        self.assertEqual(compare_service.key_columns(eterra, second), ("ie", "ie"))

    def test_processing_sources_with_their_layer_info(self):
        from qgis.core import QgsProcessingContext, QgsProcessingFeatureSource  # type: ignore
        from cadex.services.layer_sources import LayerInfo
        old = self.plain("v1", [[1, 70001, "A"]])
        new = self.plain("v2", [[1, 70001, "A"], [2, None, "B"]])
        context = QgsProcessingContext()
        sources = [QgsProcessingFeatureSource(layer, context) for layer in (old, new)]
        self.assertEqual(compare_service.key_columns(*sources, LayerInfo(old), LayerInfo(new)),
                         ("id", "id"))

    def test_a_layer_is_read_again_only_after_it_changes(self):
        old = self.plain("v1", [[None, 70001, "A"], [2, 70002, "B"]])
        new = self.plain("v2", [[None, 70001, "A"], [2, 70002, "B"]])
        reads = []
        for layer in (old, new):
            read = layer.getFeatures
            layer.getFeatures = lambda *args, read=read: (reads.append(1), read(*args))[1]
        self.assertEqual(compare_service.key_columns(old, new), ("ie", "ie"))
        counted = len(reads)
        # the window calls key_columns again at every change of its layers or of their fields
        self.assertEqual(compare_service.key_columns(old, new), ("ie", "ie"))
        self.assertEqual(len(reads), counted)
        # v2 loses its IEs: the ID leaves as few rows without a key
        new.startEditing()
        for fid in (1, 2):
            new.changeAttributeValue(fid, new.fields().indexOf("ie"), None)
        new.commitChanges()
        self.assertEqual(compare_service.key_columns(old, new), ("id", "id"))


class ColumnSpellingTests(unittest.TestCase):
    """Imobile layers that spell the same column in different letter case."""

    def test_a_building_is_linked_to_an_imobil_whose_key_column_is_spelled_otherwise(self):
        first = helpers.polygon_layer("Imobile T1", [("POLYGON((0 0, 20 0, 20 20, 0 20, 0 0))",
                                                      ["1"])], fields="cod:string")
        second = helpers.polygon_layer("Imobile T2", [
            ("POLYGON((20 0, 40 0, 40 20, 20 20, 20 0))", ["2"])], fields="COD:string")
        # mostly on imobil 1, linked to imobil 2
        constructii = helpers.polygon_layer(
            "Construcții", [("POLYGON((15 5, 22 5, 22 10, 15 10, 15 5))", ["C1", "2"])],
            fields="nr:string,imobil:string")
        task = bts.BuildingTopologyTask(
            imobile=[LayerSnapshot(first), LayerSnapshot(second)],
            constructii=[LayerSnapshot(constructii)], buffer_m=0.002, threshold=100.0,
            join_fields=[], link_field="imobil", key_field="cod")
        self.assertTrue(task.run(), task.error_msg)
        [row] = task.results[0]
        self.assertNotIn("nu a fost găsit", row["obs_topo"])
        self.assertEqual(row["stare"], bts.ELSEWHERE)


class IntravilanGeoPackageTests(unittest.TestCase):
    """Raport Intravilan saved over an existing file with the GeoPackage's name."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_intra_gpkg_")
        self.intra = helpers.polygon_layer("Intravilan", [(square(0, 0, 1000), ["T"])],
                                           fields="nume:string")
        self.imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 10), ["A"]),
                                                         (square(2000, 0, 10), ["B"])],
                                             fields="cod:string")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def task(self, gpkg):
        return intravilan_service.IntravilanTask(
            imobile=[LayerSnapshot(self.imobile)], intravilan=[LayerSnapshot(self.intra)],
            excel_fields=["cod"], id_field="cod", owner_field=None, tolerance=0.5,
            gpkg_path=gpkg)

    @staticmethod
    def hold(path, share):
        """Open path for reading and writing the way another program would, letting others
        open it only as share allows (0 nobody, 1 to read, as Excel holds a workbook).
        Returns the release."""
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.HANDLE)
        handle = kernel32.CreateFileW(path, 0xC0000000, share, None, 3, 0, None)
        return lambda: kernel32.CloseHandle(handle)

    def test_a_file_that_is_not_a_geopackage_is_replaced_by_the_report(self):
        for name, content in (("gol.gpkg", b""),
                              ("deteriorat.gpkg", b"SQLite format 3\x00" + b"\x00" * 50),
                              ("text.gpkg", "nu este un GeoPackage".encode("utf-8"))):
            path = os.path.join(self.tmp, name)
            with open(path, "wb") as fp:
                fp.write(content)
            task = self.task(path)
            self.assertTrue(task.run(), task.error_msg)
            report = QgsVectorLayer(f"{path}|layername={task.table}", "r", "ogr")
            self.assertEqual(sorted(f["cod"] for f in report.getFeatures()), ["A", "B"], name)

    @unittest.skipUnless(os.name == "nt", "another program's hold on a file is a Windows lock")
    def test_a_geopackage_open_in_another_program_is_named_in_the_message(self):
        path = os.path.join(self.tmp, "raport.gpkg")
        first = self.task(path)
        self.assertTrue(first.run(), first.error_msg)
        for share in (0, 1):
            release = self.hold(path, share)
            try:
                task = self.task(path)
                ok = task.run()
            finally:
                release()
            self.assertFalse(ok, share)
            self.assertTrue(task.error_msg.startswith(
                "Fișierul 'raport.gpkg' nu poate fi scris: este deschis în alt program"),
                (share, task.error_msg))

    @staticmethod
    def transaction(path, journal, write):
        """Begin a transaction on the GeoPackage at path as another program would, such as
        DB Browser for SQLite with unsaved changes: a write when write is true, else a read.
        The file stays readable. Returns the release."""
        import sqlite3
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute(f"PRAGMA journal_mode={journal}")
        if write:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE alt_program(x)")
        else:
            connection.execute("BEGIN")
            connection.execute("SELECT * FROM gpkg_contents").fetchall()

        def release():
            connection.execute("ROLLBACK")
            connection.close()
        return release

    def assert_refused_by_transaction(self, path, journal, write):
        first = self.task(path)
        self.assertTrue(first.run(), first.error_msg)
        release = self.transaction(path, journal, write)
        try:
            task = self.task(path)
            ok = task.run()
        finally:
            release()
        self.assertFalse(ok, journal)
        self.assertTrue(task.error_msg.startswith(
            "Fișierul 'raport.gpkg' nu poate fi scris: este deschis în alt program"),
            (journal, task.error_msg))
        # the earlier report is still whole
        report = QgsVectorLayer(f"{path}|layername={task.table}", "r", "ogr")
        self.assertEqual(sorted(f["cod"] for f in report.getFeatures()), ["A", "B"], journal)

    def test_a_geopackage_another_program_is_writing_to_is_named_in_the_message(self):
        path = os.path.join(self.tmp, "raport.gpkg")
        for journal in ("DELETE", "WAL"):
            self.assert_refused_by_transaction(path, journal, write=True)

    def test_a_geopackage_another_program_reads_in_a_transaction_is_named_in_the_message(self):
        # with a rollback journal an open read keeps the report's write from finishing; with
        # a write-ahead log it does not
        self.assert_refused_by_transaction(os.path.join(self.tmp, "raport.gpkg"), "DELETE",
                                           write=False)


def tearDownModule():
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
