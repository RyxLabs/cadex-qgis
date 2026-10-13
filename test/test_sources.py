# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Layer snapshots in Stereo 70 from any CRS, and CGXML sources from folders, archives and
files, where an unreadable file does not stop the others."""

import os
import tempfile
import unittest
import zipfile

import helpers
import synthetic

helpers.start_qgis(gui=False)

from qgis.core import (  # noqa: E402  # type: ignore
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject,
    QgsRectangle,
)

from cadex.services import cgxml_sources, stereo70  # noqa: E402
from cadex.services.layer_sources import LayerSnapshot  # noqa: E402

square = helpers.square


class Stereo70Tests(unittest.TestCase):

    def test_stereo70_and_its_variants_are_read_as_they_are(self):
        for crs in (QgsCoordinateReferenceSystem("EPSG:3844"),
                    QgsCoordinateReferenceSystem("EPSG:31700"),
                    QgsCoordinateReferenceSystem.fromProj(
                        "+proj=sterea +lat_0=46 +lon_0=25 +k=0.99975 +x_0=500000 "
                        "+y_0=500000 +ellps=krass +towgs84=33.4,-146.6,-76.3,-0.359,-0.053,"
                        "0.844,-0.84 +units=m +no_defs")):
            transform, state = stereo70.transform_for(
                crs, QgsRectangle(400000, 300000, 600000, 500000))
            self.assertIsNone(transform, crs.toProj())
            self.assertEqual(state, stereo70.SAME)

    def test_other_systems_are_reprojected_and_no_crs_is_assumed(self):
        transform, state = stereo70.transform_for(QgsCoordinateReferenceSystem("EPSG:4326"))
        self.assertIsNotNone(transform)
        self.assertEqual(state, stereo70.REPROJECTED)
        self.assertEqual(stereo70.transform_for(QgsCoordinateReferenceSystem()),
                         (None, stereo70.ASSUMED))

    def test_degrees_without_a_crs_are_refused(self):
        invalid = QgsCoordinateReferenceSystem()
        degrees = QgsRectangle(25.1, 45.2, 25.3, 45.4)
        metres = QgsRectangle(450000, 350000, 451000, 351000)
        self.assertIn("grade", stereo70.unusable_reason("x", invalid, degrees))
        self.assertEqual(stereo70.unusable_reason("x", invalid, metres), "")
        # a declared geographic CRS is reprojected, not refused
        self.assertEqual(stereo70.unusable_reason(
            "x", QgsCoordinateReferenceSystem("EPSG:4326"), degrees), "")

    def test_a_wgs84_layer_is_measured_in_stereo70(self):
        # a 100 m square in Stereo 70, stored in WGS 84 degrees
        to_wgs = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3844"),
                                        QgsCoordinateReferenceSystem("EPSG:4326"),
                                        QgsProject.instance())
        geometry = QgsGeometry.fromWkt(square(450000, 350000, 100))
        geometry.densifyByCount(10)
        geometry.transform(to_wgs)
        layer = helpers.polygon_layer("gps", [(geometry.asWkt(), [1])], crs="EPSG:4326")
        snapshot = LayerSnapshot(layer)
        self.assertEqual(snapshot.crs_state, stereo70.REPROJECTED)
        (_feature, measured), = list(snapshot.features())
        self.assertAlmostEqual(measured.area(), 10000.0, delta=0.5)
        self.assertEqual(stereo70.notes([snapshot]), [
            "Layerul 'gps' (EPSG:4326) a fost reproiectat în Stereo 70 (EPSG:3844)."])

    def test_a_layer_without_crs_is_read_as_stereo70(self):
        layer = helpers.polygon_layer("dxf", [(square(450000, 350000, 100), [1])], crs="")
        snapshot = LayerSnapshot(layer)
        self.assertEqual(snapshot.crs_state, stereo70.ASSUMED)
        (_feature, geometry), = list(snapshot.features())
        self.assertAlmostEqual(geometry.area(), 10000.0, places=6)
        self.assertIn("citit ca Stereo 70", stereo70.notes([snapshot])[0])


class SourceTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_src_")
        self.a = os.path.join(self.tmp, "sector1")
        self.b = os.path.join(self.tmp, "sector2")
        for folder in (self.a, self.b):
            synthetic.write(folder, "10188.cgxml", synthetic.Land(
                rings=[synthetic.square(0, 0, 10)], cadgenno="10188"))
        synthetic.write(os.path.join(self.a, "sub"), "10189.cgxml",
                        synthetic.Land(rings=[synthetic.square(0, 0, 10)], cadgenno="10189"))
        with open(os.path.join(self.a, "10188.dbf"), "w") as fp:
            fp.write("not a cgxml")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def test_split_and_join(self):
        self.assertEqual(cgxml_sources.split_paths(r"C:\date\livrare 1"), [r"C:\date\livrare 1"])
        self.assertEqual(cgxml_sources.split_paths('"C:\\a b.cgxml" "C:\\c.cgxml"'),
                         ["C:\\a b.cgxml", "C:\\c.cgxml"])
        self.assertEqual(cgxml_sources.split_paths("  "), [])
        for paths in (["C:\\x"], ["C:\\a b.cgxml", "C:\\c.cgxml"]):
            self.assertEqual(cgxml_sources.split_paths(cgxml_sources.join_paths(paths)), paths)

    def test_one_folder_keeps_relative_names(self):
        displays = [s[0] for s in cgxml_sources.list_sources(self.a)]
        self.assertEqual(displays, ["10188.cgxml", os.path.join("sub", "10189.cgxml")])
        self.assertEqual(len(cgxml_sources.list_sources(self.a, recursive=False)), 1)

    def test_several_roots_are_named_by_their_folder_and_not_repeated(self):
        single = os.path.join(self.b, "10188.cgxml")
        sources = cgxml_sources.list_sources([self.a, self.b, single])
        displays = [s[0] for s in sources]
        self.assertEqual(len(sources), 3)
        self.assertIn(os.path.join("sector1", "10188.cgxml"), displays)
        self.assertIn(os.path.join("sector2", "10188.cgxml"), displays)
        self.assertEqual([cgxml_sources.file_name(s) for s in sources].count("10188.cgxml"), 2)

    def test_a_bad_member_does_not_stop_the_others(self):
        archive = os.path.join(self.tmp, "livrare.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(os.path.join(self.a, "10188.cgxml"), "10188.cgxml")
            zf.writestr("stricat.cgxml", "<CGXML><Land>")
        sources = cgxml_sources.list_sources([archive, self.b])
        results = list(cgxml_sources.read_each(sources))
        problems = {cgxml_sources.file_name(s): p for s, _r, p in results}
        self.assertEqual(len(results), 3)
        self.assertIsNone(problems["10188.cgxml"])
        self.assertIsNotNone(problems["stricat.cgxml"])

    def test_mac_metadata_files_are_not_cgxml(self):
        archive = os.path.join(self.tmp, "livrare_mac.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(os.path.join(self.a, "10188.cgxml"), "livrare/10188.cgxml")
            zf.writestr("__MACOSX/livrare/._10188.cgxml", b"\x00\x05\x16\x07")
        with open(os.path.join(self.b, "._10188.cgxml"), "wb") as fp:
            fp.write(b"\x00\x05\x16\x07")
        names = [cgxml_sources.file_name(s) for s in cgxml_sources.list_sources([archive, self.b])]
        self.assertEqual(names, ["10188.cgxml", "10188.cgxml"])

    def test_stop_ends_the_reading(self):
        sources = cgxml_sources.list_sources([self.a, self.b])
        self.assertEqual(list(cgxml_sources.read_each(sources, stop=lambda: True)), [])


def stereo70_square(x, size=10):
    return square(450000 + x, 350000, size)


def keyed_layer(name, ids, fields="id:string", uat=None, converted=False):
    """A layer of squares holding ids, with the UAT code in a second column when uat is given;
    converted marks it as a Conversie CGXML output."""
    from cadex.services import roles
    columns = fields + (",uat:string" if uat is not None else "")
    rows = [(stereo70_square(20 * i), [key] + ([uat] if uat is not None else []))
            for i, key in enumerate(ids)]
    layer = helpers.polygon_layer(name, rows, fields=columns)
    if converted:
        roles.mark_batch(layer, roles.new_batch())
        roles.mark_columns(layer, [("id", "id")] + ([("uat", "uat")] if uat is not None else []))
    return layer


class ServicesCrsTests(unittest.TestCase):
    """Coordinates that cannot be in the CRS the layer declares are refused before a run."""

    def wgs84(self):
        return QgsCoordinateReferenceSystem("EPSG:4326")

    def test_stereo70_metres_labelled_wgs84_are_refused(self):
        # a GeoJSON without a crs member is read as EPSG:4326
        metres = QgsRectangle(550000, 460000, 550200, 460100)
        reason = stereo70.unusable_reason("imobile geojson", self.wgs84(), metres)
        self.assertIn("Layerul 'imobile geojson'", reason)
        self.assertIn("EPSG:4326", reason)

    def test_degrees_labelled_stereo70_are_refused(self):
        degrees = QgsRectangle(25.1, 45.2, 25.3, 45.4)
        reason = stereo70.unusable_reason("gps", QgsCoordinateReferenceSystem("EPSG:3844"),
                                          degrees)
        self.assertIn("EPSG:3844", reason)
        self.assertIn("grade", reason)

    def test_coordinates_outside_romania_are_refused(self):
        # UTM 35N metres read as Stereo 70, and a Stereo 70 layer far outside Romania
        utm = QgsRectangle(400000, 5000000, 400500, 5000500)
        self.assertIn("nu se află toate în România",
                      stereo70.unusable_reason("dxf", QgsCoordinateReferenceSystem(), utm))
        outside = QgsRectangle(-50000, 300000, -40000, 310000)
        self.assertIn("nu se află toate în România", stereo70.unusable_reason(
            "x", QgsCoordinateReferenceSystem("EPSG:3844"), outside))
        # Paris in degrees
        self.assertIn("nu se află toate în România", stereo70.unusable_reason(
            "x", self.wgs84(), QgsRectangle(2.3, 48.8, 2.4, 48.9)))

    def test_a_refusal_for_a_stray_feature_shows_the_extent_that_contains_it(self):
        # a WGS 84 layer in Romania with one point at 0, 0: its CRS is right, so the message
        # gives the extent and no CRS instruction
        reason = stereo70.unusable_reason("puncte gps", self.wgs84(),
                                          QgsRectangle(0, 0, 26.25, 46.5))
        self.assertIn("Layerul 'puncte gps' are sistemul de coordonate EPSG:4326", reason)
        self.assertIn("de la (0; 0) până la (26,25; 46,5)", reason)
        self.assertNotIn("Alegeți-i", reason)
        # without a CRS, the extent and the instruction for data in another system
        reason = stereo70.unusable_reason("dxf", QgsCoordinateReferenceSystem(),
                                          QgsRectangle(-120.5, 350000, 451000, 351000))
        self.assertIn("de la (-120,5; 350.000) până la (451.000; 351.000)", reason)
        self.assertIn("alegeți-l din proprietățile layerului", reason)

    def test_layers_whose_coordinates_fit_their_crs_are_read(self):
        stereo = QgsRectangle(450000, 350000, 451000, 351000)
        for crs, extent in ((QgsCoordinateReferenceSystem("EPSG:3844"), stereo),
                            (QgsCoordinateReferenceSystem("EPSG:31700"), stereo),
                            (self.wgs84(), QgsRectangle(25.1, 45.2, 25.3, 45.4)),
                            (QgsCoordinateReferenceSystem("EPSG:3857"),
                             QgsRectangle(2790000, 5650000, 2791000, 5651000)),
                            (QgsCoordinateReferenceSystem(), stereo)):
            self.assertEqual(stereo70.unusable_reason("x", crs, extent), "", crs.authid())
        # a layer without features has nothing to check
        self.assertEqual(stereo70.unusable_reason("x", self.wgs84(), QgsRectangle()), "")


class ServicesLayerSourceTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_layers_")

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    def test_a_geopackage_keyed_by_the_cadastral_number_keeps_its_id_column(self):
        from qgis.core import (  # type: ignore
            QgsCoordinateTransformContext, QgsVectorFileWriter, QgsVectorLayer,
        )
        from cadex.services import layer_sources
        source = keyed_layer("imobile", [10188, 10200, 10377], fields="id:integer")
        path = os.path.join(self.tmp, "imobile.gpkg")
        # ogr2ogr -lco FID=id makes the column the table's primary key
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerOptions = ["FID=id"]
        QgsVectorFileWriter.writeAsVectorFormatV3(source, path, QgsCoordinateTransformContext(),
                                                  options)
        keyed = QgsVectorLayer(path, "imobile", "ogr")
        self.assertEqual([keyed.fields().at(i).name() for i in keyed.primaryKeyAttributes()],
                         ["id"])
        self.assertEqual(layer_sources.row_number_fields(keyed), [])
        self.assertEqual(LayerSnapshot(keyed).data_field_names(), ["id"])
        # GDAL's own FID column still only numbers the rows
        numbered = helpers.gpkg_layer(source, os.path.join(self.tmp, "numerotat.gpkg"))
        self.assertEqual(layer_sources.row_number_fields(numbered), ["fid"])

    def test_merged_fields_leave_out_the_row_numbers_of_each_geopackage(self):
        from cadex.services.layer_sources import merged_fields
        sectors = [helpers.gpkg_layer(keyed_layer(f"sector {n}", ["1", "2"]),
                                      os.path.join(self.tmp, f"sector{n}.gpkg"))
                   for n in (1, 2)]
        merged = merged_fields([LayerSnapshot(layer) for layer in sectors])
        self.assertEqual(merged.names(), ["id"])

    def test_merged_fields_match_names_regardless_of_case_and_widen_numbers(self):
        from cadex.qt_compat import FIELD_DOUBLE, FIELD_LONGLONG, FIELD_STRING
        from cadex.services.layer_sources import merged_fields
        first = helpers.polygon_layer(
            "s1", [], fields="ID:integer,propr:string(10),sup:integer,cod:integer,nota:string(0)")
        second = helpers.polygon_layer(
            "s2", [], fields="id:long,PROPR:string(100),sup:double,cod:string,nota:string(5)")
        merged = merged_fields([LayerSnapshot(first), LayerSnapshot(second)])
        self.assertEqual(merged.names(), ["ID", "propr", "sup", "cod", "nota"])
        types = [merged.field(name).type() for name in merged.names()]
        self.assertEqual(types, [FIELD_LONGLONG, FIELD_STRING, FIELD_DOUBLE, FIELD_STRING,
                                 FIELD_STRING])
        # the longest owner list fits; a text field without a length limit keeps none
        self.assertEqual(merged.field("propr").length(), 100)
        self.assertEqual(merged.field("nota").length(), 0)

    def test_a_few_shared_numbers_do_not_make_two_versions(self):
        from cadex.services.layer_sources import shared_keys
        first = keyed_layer("sector 1", [str(n) for n in range(1, 51)])
        second = keyed_layer("sector 2", [str(n) for n in range(50, 100)])
        self.assertIsNone(shared_keys([first, second], "id"))

    def test_two_versions_of_the_same_imobile_are_found_with_their_count(self):
        from cadex.services.layer_sources import shared_keys
        first = keyed_layer("v1", [str(n) for n in range(1, 21)])
        second = keyed_layer("v2", [float(n) for n in range(3, 23)], fields="id:double")
        found = shared_keys([first, second], "id")
        self.assertIsNotNone(found)
        older, newer, sample, total = found
        self.assertEqual((older, newer), (first, second))
        # 100001.0 from a Shapefile is 100001
        self.assertEqual(sample, ["3", "4", "5", "6", "7"])
        self.assertEqual(total, 18)

    def test_converted_layers_of_two_uats_are_not_versions_of_each_other(self):
        from cadex.services.layer_sources import shared_keys
        ids = [str(n) for n in range(1, 21)]
        first = keyed_layer("uat 1", ids, uat="100", converted=True)
        second = keyed_layer("uat 2", ids, uat="200", converted=True)
        self.assertIsNone(shared_keys([first, second], "id"))
        again = keyed_layer("uat 1 din nou", ids, uat="100", converted=True)
        self.assertEqual(shared_keys([first, again], "id")[3], 20)

    def test_converted_keys_are_read_once_until_the_layer_changes(self):
        from qgis.core import QgsFeature, QgsGeometry  # type: ignore
        from cadex.services import layer_sources
        layer = keyed_layer("Imobile", ["1", "2"], uat="100", converted=True)
        QgsProject.instance().addMapLayer(layer)
        reads = []
        read = layer.getFeatures
        layer.getFeatures = lambda *args: reads.append(1) or read(*args)
        keys = layer_sources.imobil_keys([layer])
        self.assertEqual(keys, {("id", "100", "1"), ("id", "100", "2")})
        first_reads = len(reads)
        # imobil_keys is called again: no second scan
        self.assertEqual(layer_sources.imobil_keys([layer]), keys)
        self.assertEqual(len(reads), first_reads)
        # an edit, saved or not, changes the keys
        layer.startEditing()
        feature = QgsFeature(layer.fields())
        feature.setAttributes(["3", "100"])
        feature.setGeometry(QgsGeometry.fromWkt(stereo70_square(100)))
        layer.addFeature(feature)
        self.assertIn(("id", "100", "3"), layer_sources.imobil_keys([layer]))
        layer.rollBack()
        self.assertEqual(layer_sources.imobil_keys([layer]), keys)

    def test_the_keys_of_a_removed_layer_are_not_kept(self):
        from cadex.services import layer_sources
        layer = keyed_layer("Imobile", ["1"], uat="100", converted=True)
        layer_id = layer.id()
        QgsProject.instance().addMapLayer(layer)
        layer_sources.imobil_keys([layer])
        QgsProject.instance().removeMapLayer(layer_id)
        self.assertNotIn(layer_id, layer_sources._KEYS)


class ColumnSpellingSourceTests(unittest.TestCase):
    """Imobile layers that spell the same column in different letter case."""

    def test_two_versions_of_the_same_imobile_are_found_whatever_the_spelling_of_the_key(self):
        from cadex.services.layer_sources import shared_keys
        first = keyed_layer("v1", [str(n) for n in range(1, 21)])
        second = keyed_layer("v2", [str(n) for n in range(1, 21)], fields="ID:string")
        found = shared_keys([first, second], "id")
        self.assertIsNotNone(found)
        self.assertEqual(found[3], 20)


class ConversionSourceTests(unittest.TestCase):
    """A damaged, locked or unreadable archive or folder is one unreadable source, reported in
    Romanian, and the other sources are still read."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_src_")
        self.good = os.path.join(self.tmp, "livrare")
        synthetic.write(self.good, "10188.cgxml", synthetic.Land(
            rings=[synthetic.square(0, 0, 10)], cadgenno="10188"))

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def problems(self, paths, recursive=True):
        sources = cgxml_sources.list_sources(paths, recursive)
        return {cgxml_sources.file_name(s): p for s, _r, p in cgxml_sources.read_each(sources)}

    def archive(self, name="arhiva.zip"):
        path = os.path.join(self.tmp, name)
        with zipfile.ZipFile(path, "w") as zf:
            zf.write(os.path.join(self.good, "10188.cgxml"), "10189.cgxml")
        return path

    @staticmethod
    def hold_exclusively(path):
        """Open path so that no other program may even read it. Returns the release."""
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.HANDLE)
        handle = kernel32.CreateFileW(path, 0xC0000000, 0, None, 3, 0, None)
        return lambda: kernel32.CloseHandle(handle)

    def test_a_damaged_archive_is_reported_and_the_rest_is_read(self):
        damaged = os.path.join(self.tmp, "descarcare.zip")
        with open(damaged, "wb") as fp:
            fp.write(b"PK\x03\x04 o descarcare intrerupta")
        self.assertEqual(self.problems([self.good, damaged]), {
            "10188.cgxml": None,
            "descarcare.zip": "arhiva ZIP este deteriorată sau incompletă"})

    @unittest.skipUnless(os.name == "nt", "a lock that keeps readers out is a Windows lock")
    def test_a_locked_archive_or_file_is_reported_in_romanian(self):
        archive = self.archive()
        cgxml = os.path.join(self.good, "10188.cgxml")
        releases = [self.hold_exclusively(archive), self.hold_exclusively(cgxml)]
        try:
            problems = self.problems([self.good, archive])
        finally:
            for release in releases:
                release()
        self.assertEqual(problems, {
            "10188.cgxml": "fișierul nu poate fi deschis: este folosit de alt program sau nu "
                           "aveți drept de acces la el",
            "arhiva.zip": "arhiva ZIP nu poate fi deschisă: este folosită de alt program sau "
                          "nu aveți drept de acces la ea"})

    def test_a_folder_that_cannot_be_listed_is_reported(self):
        from unittest import mock
        locked = os.path.join(self.tmp, "sector2")
        synthetic.write(locked, "20001.cgxml", synthetic.Land(
            rings=[synthetic.square(0, 0, 10)], cadgenno="20001"))

        def refuse(real):
            def listing(path="."):
                if os.path.normcase(os.fspath(path)) == os.path.normcase(locked):
                    raise PermissionError(13, "Permission denied", locked)
                return real(path)
            return listing
        for recursive in (True, False):
            with self.subTest(recursive=recursive), \
                    mock.patch.object(os, "scandir", refuse(os.scandir)), \
                    mock.patch.object(os, "listdir", refuse(os.listdir)):
                self.assertEqual(self.problems([self.good, locked], recursive), {
                    "10188.cgxml": None,
                    "sector2": "folderul nu poate fi citit: nu aveți drept de acces la el"})

    def test_a_subfolder_nobody_may_list_is_skipped(self):
        from unittest import mock
        # as System Volume Information at the root of a drive
        protected = os.path.join(self.good, "System Volume Information")
        os.makedirs(protected)
        scandir = os.scandir

        def listing(path="."):
            if os.path.normcase(os.fspath(path)) == os.path.normcase(protected):
                raise PermissionError(13, "Access is denied", protected)
            return scandir(path)
        with mock.patch.object(os, "scandir", listing):
            self.assertEqual(self.problems(self.good), {"10188.cgxml": None})


def tearDownModule():
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
