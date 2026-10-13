# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conversie CGXML end to end on the synthetic files: every output format, ZIP archives,
subfolders, column layouts and reported problems. run() is called directly."""

import os
import shutil
import tempfile
import unittest
import zipfile

import helpers
import synthetic

helpers.start_qgis(gui=False)

from qgis.core import Qgis, QgsVectorLayer  # noqa: E402  # type: ignore

from cadex.services import cgxml_columns, cgxml_export, xlsx  # noqa: E402

NAMES = {"imobile": "Imobile", "constructii": "Construcții"}


def layouts():
    return {w: cgxml_columns.default_layout(w) for w in ("imobile", "constructii")}


class ConversionTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_conv_")
        self.src = os.path.join(self.tmp, "src")
        shutil.copytree(helpers.DATA_DIR, self.src)
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def run_task(self, fmt, source=None, recursive=True, layout=None, labels=None):
        task = cgxml_export.ConversionTask(
            input_paths=source or self.src, recursive=recursive, output_dir=self.out,
            base_name="rezultat", fmt=fmt, layouts=layout or layouts(),
            layer_names=NAMES, label_config=labels or {})
        self.assertTrue(task.run(), task.error_msg)
        return task

    def layer(self, task, which):
        layer = QgsVectorLayer(task.outputs[which], which, "ogr")
        self.assertTrue(layer.isValid(), task.outputs[which])
        return layer

    def features(self, layer, key):
        return {f[key]: f for f in layer.getFeatures()}


class GeoPackageTests(ConversionTest):

    def test_layers_counts_and_problems(self):
        task = self.run_task("gpkg")
        # 4 readable synthetic files at the top, the broken folder holds 1 readable and 6 refused
        self.assertEqual(task.file_count, 11)
        self.assertEqual(len(task.errors), 6)
        self.assertEqual(len(task.imobile), 5)
        imobile = self.layer(task, "imobile")
        # bom_30003 and bad_values have no geometry and stay out of the layer
        self.assertEqual(imobile.featureCount(), 3)
        self.assertEqual(Qgis.WkbType(imobile.wkbType()), Qgis.WkbType.MultiPolygon)
        self.assertEqual(imobile.crs().authid(), "EPSG:3844")
        self.assertEqual(self.layer(task, "constructii").featureCount(), 1)

    def test_values_nulls_and_hole(self):
        task = self.run_task("gpkg")
        imobile = list(self.layer(task, "imobile").getFeatures())
        eterra = next(f for f in imobile if f["ie"] == 100001)
        self.assertTrue(eterra["id"] is None or str(eterra["id"]) == "NULL")
        self.assertEqual(eterra["propr"], "POPESCU ION, POPESCU MARIA")
        self.assertEqual(eterra["uat"], "Cristian")
        self.assertIn("Strada Mare", eterra["adresa"])
        livrare = next(f for f in imobile if f["id"] == 10188)
        self.assertAlmostEqual(livrare.geometry().area(), 9900.0, places=3)
        self.assertEqual(livrare["tip_teren"], "Mixt")
        building = next(self.layer(task, "constructii").getFeatures())
        self.assertEqual(building["ie"], "100001-C1")

    def test_custom_layout(self):
        layout = layouts()
        imobile = layout["imobile"]
        for column in imobile:
            column["on"] = column["key"] in ("ie", "id", "propr")
        imobile.sort(key=lambda c: ["propr", "ie", "id"].index(c["key"])
                     if c["key"] in ("propr", "ie", "id") else 99)
        imobile[1]["name"] = "NR_IE"
        task = self.run_task("gpkg", layout=layout)
        fields = [f.name() for f in self.layer(task, "imobile").fields() if f.name() != "fid"]
        self.assertEqual(fields, ["propr", "NR_IE", "id"])

    def test_zip_archive_and_top_folder_only(self):
        archive = os.path.join(self.tmp, "livrare.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            for name in ("eterra_100001.cgxml", "livrare_10188.cgxml"):
                zf.write(os.path.join(self.src, name), f"sector1/{name}")
        task = self.run_task("gpkg", source=archive)
        self.assertEqual(task.file_count, 2)
        self.assertEqual(self.layer(task, "imobile").featureCount(), 2)

        task = self.run_task("gpkg", recursive=False)
        self.assertEqual(task.file_count, 4)
        self.assertEqual(task.errors, [])

    def test_duplicate_imobil_is_reported(self):
        shutil.copy(os.path.join(self.src, "livrare_10188.cgxml"),
                    os.path.join(self.src, "copie_10188.cgxml"))
        task = self.run_task("gpkg", recursive=False)
        flagged = [f for f, message in task.warnings if message == "ID 10188 apare în 2 fișiere"]
        # one row per file, so the Probleme sheet can be filtered by file
        self.assertEqual(len(flagged), 2)
        self.assertEqual(sorted(os.path.basename(f) for f in flagged),
                         ["copie_10188.cgxml", "livrare_10188.cgxml"])

    def test_imobile_split_from_one_ie_are_not_duplicates(self):
        # an old imobil split by the systematic work: its IE goes on every new imobil
        folder = os.path.join(self.tmp, "split")
        for i, (name, cadgenno, ie) in enumerate((("1.cgxml", "1", "500"), ("2.cgxml", "2", "500"),
                                                  ("a.cgxml", "", "600"), ("b.cgxml", "", "600"))):
            synthetic.write(folder, name, synthetic.Land(
                rings=[synthetic.square(500000 + 100 * i, 400000, 20)], cadgenno=cadgenno, ie=ie))
        read = cgxml_export.read_all([folder], recursive=False)
        messages = [message for _file, message in read.warnings]
        self.assertNotIn("IE 500 apare în 2 fișiere", messages)
        # without an ID, the IE is all that names the imobil
        self.assertEqual(messages.count("IE 600 apare în 2 fișiere"), 2)

    def test_rerun_replaces_the_file(self):
        self.run_task("gpkg")
        task = self.run_task("gpkg", recursive=False)
        self.assertEqual(self.layer(task, "imobile").featureCount(), 3)


class ShapefileTests(ConversionTest):

    def test_identifiers_come_back_as_whole_numbers(self):
        # dBase numbers wider than 18 digits read back as reals: 10188.0
        task = self.run_task("shp", recursive=False)
        layer = self.layer(task, "imobile")
        for name in ("id", "ie", "sector"):
            self.assertIn(layer.fields().field(name).typeName(), ("Integer", "Integer64"), name)
        livrare = next(f for f in layer.getFeatures() if f["id"] == 10188)
        self.assertIsInstance(livrare["id"], int)

    def test_names_cut_to_ten_and_text_cut_by_bytes(self):
        layout = layouts()
        layout["imobile"][0]["name"] = "identificator"
        long_note = "ă" * 200  # 400 bytes in UTF-8, dBase keeps 254 bytes
        path = os.path.join(self.src, "livrare_10188.cgxml")
        with open(path, encoding="utf-8") as fp:
            text = fp.read().replace("parcelă cu gol interior", long_note)
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(text)
        task = self.run_task("shp", layout=layout)
        self.assertGreaterEqual(task.truncated, 1)
        imobile = self.layer(task, "imobile")
        self.assertIn("identifica", imobile.fields().names())
        note = next(f["note"] for f in imobile.getFeatures() if f["identifica"] == 10188)
        self.assertEqual(note, "ă" * 127)
        self.assertTrue(os.path.basename(task.outputs["constructii"]).endswith("_constructii.shp"))


class ExcelAndDxfTests(ConversionTest):

    def test_excel_lists_everything(self):
        path = self.run_task("xlsx").outputs["file"]
        self.assertEqual(xlsx.sheet_names(path), ["Sumar", "Imobile", "Construcții", "Probleme"])
        _names, rows = xlsx.read_rows(path, "Imobile")
        self.assertEqual(rows[0][-1], "Are geometrie")
        self.assertEqual(len(rows) - 1, 5)  # records without geometry are listed too

    def test_excel_headers_are_the_readable_names(self):
        layout = layouts()
        layout["imobile"][0]["name"] = "NR_CAD"
        path = self.run_task("xlsx", layout=layout).outputs["file"]
        _names, rows = xlsx.read_rows(path, "Imobile")
        self.assertEqual(rows[0][0], cgxml_columns.label_of("imobile", "id"))
        self.assertNotIn("NR_CAD", rows[0])
        sup_mas = rows[0].index(cgxml_columns.label_of("imobile", "sup_mas"))
        self.assertEqual(sorted(r[sup_mas] for r in rows[1:] if r[sup_mas] is not None)[-1],
                         9900)
        _names, problems = xlsx.read_rows(path, "Probleme")
        self.assertTrue(any("empty" in (row[0] or "") for row in problems[1:]))

    @unittest.skipUnless(__import__("importlib").util.find_spec("ezdxf"), "ezdxf missing")
    def test_dxf_polylines_and_labels(self):
        import ezdxf
        labels = {"imobile": [{"key": "id", "prefix": "ID ", "suffix": "", "on": True},
                              {"key": "ie", "prefix": "IE ", "suffix": "", "on": True},
                              {"key": "sup_mas", "prefix": "", "suffix": " mp", "on": True}],
                  "constructii": [{"key": "nr_constr", "on": True}]}
        task = self.run_task("dxf", labels=labels)
        doc = ezdxf.readfile(task.outputs["file"])
        msp = doc.modelspace()
        polylines = [e for e in msp if e.dxftype() == "LWPOLYLINE"]
        texts = sorted(e.dxf.text for e in msp if e.dxftype() == "TEXT")
        # 3 imobile, one of them with a hole, plus the construcție
        self.assertEqual(len(polylines), 5)
        self.assertIn("ID 10188", texts)
        self.assertIn("IE 100001", texts)
        self.assertIn("9900 mp", texts)
        self.assertIn("C1", texts)
        self.assertIn("CONSTRUCTII", [layer.dxf.name for layer in doc.layers])


class HelperTests(unittest.TestCase):

    def test_format_value_for_labels(self):
        self.assertEqual(cgxml_export.format_value(2628.0), "2628")
        self.assertEqual(cgxml_export.format_value(2628.5), "2628,5")
        self.assertEqual(cgxml_export.format_value(None), "")
        self.assertEqual(cgxml_export.format_value("C1"), "C1")

    def test_truncate_utf8_keeps_whole_characters(self):
        self.assertEqual(cgxml_export.truncate_utf8("ăăă", 5), "ăă")
        self.assertEqual(cgxml_export.truncate_utf8("abc", 5), "abc")

    def test_table_name(self):
        self.assertEqual(cgxml_export.table_name("Construcții sector 2"), "constructii_sector_2")
        self.assertEqual(cgxml_export.table_name("!!!"), "layer")


@unittest.skipUnless(os.name == "nt", "Excel's way of holding a file is a Windows lock")
class ServicesConversionOutputTests(ConversionTest):
    """A conversion whose output file another program holds open."""

    def test_an_output_open_in_another_program_fails_the_run_in_romanian(self):
        from test_pure_services import hold_like_excel
        os.makedirs(self.out)
        for fmt in ("gpkg", "xlsx"):
            name = f"rezultat.{fmt}"
            path = os.path.join(self.out, name)
            with open(path, "wb") as fp:
                fp.write(b"x")
            release = hold_like_excel(path)
            try:
                task = cgxml_export.ConversionTask(
                    input_paths=self.src, recursive=True, output_dir=self.out,
                    base_name="rezultat", fmt=fmt, layouts=layouts(), layer_names=NAMES,
                    label_config={})
                self.assertFalse(task.run())
            finally:
                release()
            self.assertTrue(task.error_msg.startswith(
                f"Fișierul '{name}' nu poate fi scris: este deschis în alt program"),
                task.error_msg)


class ConversionExportTests(ConversionTest):
    """Duplicates per UAT, records left off the map, refused features, sources that cannot be
    read, and runs canceled while writing."""

    def folder(self, name, *lands):
        """A folder of synthetic files, one per (file name, Land)."""
        path = os.path.join(self.tmp, name)
        for file_name, land in lands:
            synthetic.write(path, file_name, land)
        return path

    def task(self, fmt, source, layout=None, labels=None):
        return cgxml_export.ConversionTask(
            input_paths=source, recursive=True, output_dir=self.out, base_name="rezultat",
            fmt=fmt, layouts=layout or layouts(), layer_names=NAMES, label_config=labels or {})

    def many(self, name, count=30):
        return self.folder(name, *((f"{n}.cgxml", synthetic.Land(
            rings=[synthetic.square(500000 + 30 * n, 400000, 20)], cadgenno=str(n)))
            for n in range(1, count + 1)))

    @staticmethod
    def cancel_while_writing(task):
        """Cancel the task once its progress passes 80, where reading ends."""
        seen = []
        task.progress_hook = seen.append
        task.stop_hook = lambda: any(p > 80 for p in seen)

    def test_the_same_number_in_two_uats_is_not_a_duplicate(self):
        def uat(name, sirsup):
            return self.folder(name, ("501.cgxml", synthetic.Land(
                rings=[synthetic.square(500000, 400000, 20)], cadgenno="501", sirsup=sirsup,
                siruta=sirsup)))
        first, other_uat = uat("uat1", "40900"), uat("uat2", "179132")
        again = uat("din_nou", "40900")
        messages = [m for _f, m in cgxml_export.read_all([first, other_uat]).warnings]
        self.assertNotIn("ID 501 apare în 2 fișiere", messages)
        messages = [m for _f, m in cgxml_export.read_all([first, again]).warnings]
        self.assertEqual(messages.count("ID 501 apare în 2 fișiere"), 2)

    @unittest.skipUnless(os.name == "nt", "drive letters are a Windows matter")
    def test_sources_on_two_drives_are_named_after_the_first(self):
        other = "Y:" if os.path.splitdrive(self.tmp)[0].upper() == "Z:" else "Z:"
        self.assertEqual(cgxml_export.source_stem(
            [os.path.join(self.tmp, "livrare"), other + "\\arhive\\livrare_v2.zip"]), "livrare")

    def test_records_left_off_the_map_are_counted_by_reason(self):
        # bom_30003 has no points, bad_values_30002 a coordinate that is not a number
        read = cgxml_export.read_all([helpers.DATA_DIR])
        self.assertEqual(cgxml_export.unmapped_notes("imobile", read.imobile), [
            "1 imobil nu are coordonate în fișier și nu apare în rezultat.",
            "1 imobil are puncte din care nu s-a putut construi geometria și nu apare în "
            "rezultat."])
        self.assertEqual(cgxml_export.unmapped_notes("constructii", read.constructii), [])

    def test_features_the_writer_refuses_are_counted(self):
        # a firm naming its ID column FID: GeoPackage keeps that name for the row number, and
        # the same imobil in two files breaks its uniqueness
        shutil.copy(os.path.join(self.src, "livrare_10188.cgxml"),
                    os.path.join(self.src, "copie_10188.cgxml"))
        layout = layouts()
        layout["imobile"][0]["name"] = "fid"
        task = self.run_task("gpkg", recursive=False, layout=layout)
        self.assertEqual(self.layer(task, "imobile").featureCount(), 3)
        [note] = task.write_notes
        self.assertTrue(note.startswith(
            "1 element din 4 nu a putut fi scris în layerul 'Imobile'."), note)
        self.assertIn("UNIQUE", note)

    def test_a_rerun_without_buildings_leaves_no_buildings_shapefile(self):
        self.run_task("shp", recursive=False)
        self.assertTrue(os.path.exists(os.path.join(self.out, "rezultat_constructii.shp")))
        source = self.folder("fara_constructii", ("1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1")))
        task = self.run_task("shp", source=source)
        self.assertNotIn("constructii", task.outputs)
        self.assertEqual([n for n in os.listdir(self.out) if "constructii" in n], [])
        self.assertEqual(self.layer(task, "imobile").featureCount(), 1)

    def test_a_rerun_removes_the_spatial_indexes_of_the_previous_shapefiles(self):
        first = self.run_task("shp", recursive=False)
        for which in ("imobile", "constructii"):
            layer = self.layer(first, which)
            # what Layer Properties > Source > Create Spatial Index does in QGIS
            self.assertTrue(layer.dataProvider().createSpatialIndex())
            del layer
        indexes = [os.path.join(self.out, f"rezultat_{which}.qix")
                   for which in ("imobile", "constructii")]
        self.assertTrue(all(os.path.exists(index) for index in indexes))
        # another source, without buildings
        source = self.folder("alta", ("1.cgxml", synthetic.Land(
            rings=[synthetic.square(505000, 405000, 20)], cadgenno="1")))
        task = self.run_task("shp", source=source)
        self.assertEqual([index for index in indexes if os.path.exists(index)], [])
        self.assertEqual(self.layer(task, "imobile").featureCount(), 1)

    def test_nothing_is_written_from_a_source_with_no_readable_file(self):
        previous = self.run_task("gpkg", recursive=False).outputs["imobile"]
        damaged = os.path.join(self.tmp, "descarcare.zip")
        with open(damaged, "wb") as fp:
            fp.write(b"PK\x03\x04 o descarcare intrerupta")
        broken = os.path.join(self.src, "broken")
        os.remove(os.path.join(broken, "bad_values_30002.cgxml"))
        messages = {}
        for source in (damaged, broken):
            task = self.task("gpkg", source)
            self.assertFalse(task.run())
            messages[source] = task.error_msg
            self.assertTrue(task.error_msg.startswith(
                "Niciun fișier nu a putut fi citit, deci nu s-a scris niciun rezultat:"),
                task.error_msg)
            self.assertEqual(QgsVectorLayer(previous, "", "ogr").featureCount(), 3)
        self.assertIn("descarcare.zip: arhiva ZIP este deteriorată sau incompletă",
                      messages[damaged])

    def test_a_conversion_canceled_while_writing_keeps_the_previous_file(self):
        for fmt in ("gpkg", "shp"):
            with self.subTest(fmt=fmt):
                shutil.rmtree(self.out, ignore_errors=True)
                previous = self.run_task(fmt, recursive=False).outputs["imobile"]
                task = self.task(fmt, self.many(f"mare_{fmt}"))
                self.cancel_while_writing(task)
                self.assertFalse(task.run())
                self.assertEqual(QgsVectorLayer(previous, "", "ogr").featureCount(), 3)
                self.assertFalse([n for n in os.listdir(self.out) if ".tmp" in n])

    @unittest.skipUnless(__import__("importlib").util.find_spec("ezdxf"), "ezdxf missing")
    def test_dxf_writing_shows_progress_and_stops_when_canceled(self):
        source = self.many("mare")
        task = self.task("dxf", source)
        seen = []
        task.progress_hook = seen.append
        self.assertTrue(task.run(), task.error_msg)
        self.assertTrue(any(80 < p < 100 for p in seen), seen)
        path = task.outputs["file"]
        with open(path, "rb") as fp:
            written = fp.read()
        task = self.task("dxf", self.many("alta", 5))
        self.cancel_while_writing(task)
        self.assertFalse(task.run())
        with open(path, "rb") as fp:
            self.assertEqual(fp.read(), written)
        self.assertEqual(os.listdir(self.out), ["rezultat.dxf"])

    def test_a_label_in_a_long_thin_parcel_is_placed_with_bounded_work(self):
        from unittest import mock
        from cadex.services import dxf_writer
        # a strip 600 m long and 4 m wide with a point every 5 m along its long sides, like
        # the parcels along a road or a canal
        ring = ([(500000 + x, 400000) for x in range(0, 601, 5)]
                + [(500000 + x, 400004) for x in range(600, -1, -5)])
        distance = dxf_writer._signed_distance
        calls = []
        with mock.patch.object(dxf_writer, "_signed_distance",
                               lambda *args: calls.append(1) or distance(*args)):
            x, y, _height, _rotation = dxf_writer.label_layout(["ID 10188", "IE 50124"], [ring])
        # each call measures all 242 edges
        self.assertLessEqual(len(calls), 2500)
        # 2 m is the farthest any point lies from the edges
        self.assertGreater(distance(x, y, [ring]), 1.8)

    def test_a_label_is_placed_inside_a_long_u_shaped_strip(self):
        import math
        from cadex.services import dxf_writer
        # a strip 7 km long and 1 m wide with a 2 m arm at each end, lying along the x axis
        # and turned by 37 degrees
        flat = [(0, 0), (7000, 0), (7000, 3), (6999, 3), (6999, 1), (1, 1), (1, 3), (0, 3)]
        turn = math.radians(37)
        turned = [(x * math.cos(turn) - y * math.sin(turn),
                   x * math.sin(turn) + y * math.cos(turn)) for x, y in flat]
        for name, ring in (("flat", flat), ("turned", turned)):
            with self.subTest(name):
                ring = [(500000 + x, 400000 + y) for x, y in ring]
                x, y, _height, _rotation = dxf_writer.label_layout(["ID 1"], [ring])
                self.assertGreater(dxf_writer._signed_distance(x, y, [ring]), 0)


class ConversionCountTests(ConversionTest):
    """What a conversion counts when a source cannot be read."""

    def test_a_damaged_archive_is_not_counted_as_a_cgxml_file_found(self):
        damaged = os.path.join(self.tmp, "descarcare.zip")
        with open(damaged, "wb") as fp:
            fp.write(b"PK\x03\x04 o descarcare intrerupta")
        task = self.run_task("xlsx", source=[self.src, damaged], recursive=False)
        _names, rows = xlsx.read_rows(task.outputs["file"], "Sumar")
        facts = {row[0]: row[1] for row in rows if row and row[0]}
        self.assertEqual((facts["Fișiere CGXML găsite"], facts["Fișiere necitite"]), (4, 1))


class ConversionRerunTest(ConversionTest):
    """Helpers for a run onto the files of a previous result."""

    def contents(self):
        """{file name: bytes} of the output folder."""
        found = {}
        for name in os.listdir(self.out):
            with open(os.path.join(self.out, name), "rb") as fp:
                found[name] = fp.read()
        return found

    def sources(self):
        """(two imobile with a construcție each, the same imobile without construcții), both
        other than the synthetic files."""
        with_buildings = os.path.join(self.tmp, "cu_constructii")
        without_buildings = os.path.join(self.tmp, "fara_constructii")
        for n in (1, 2):
            square = synthetic.square(600000 + 50 * n, 450000, 20)
            synthetic.write(with_buildings, f"{n}.cgxml", synthetic.Land(
                rings=[square], cadgenno=str(n), buildings=[synthetic.Building(
                    1, [synthetic.square(600005 + 50 * n, 450005, 5)])]))
            synthetic.write(without_buildings, f"{n}.cgxml", synthetic.Land(
                rings=[square], cadgenno=str(n)))
        return with_buildings, without_buildings

    def conversion(self, source, fmt="shp"):
        return cgxml_export.ConversionTask(
            input_paths=source, recursive=True, output_dir=self.out, base_name="rezultat",
            fmt=fmt, layouts=layouts(), layer_names=NAMES, label_config={})


@unittest.skipUnless(os.name == "nt", "Excel's way of holding a file is a Windows lock")
class ConversionReplaceTests(ConversionRerunTest):
    """A rerun whose previous result cannot be replaced completely."""

    def rerun(self, source, held):
        """Convert source to Shapefile while another program holds the file held of the
        output folder the way Excel does."""
        from test_pure_services import hold_like_excel
        task = self.conversion(source)
        release = hold_like_excel(os.path.join(self.out, held))
        try:
            self.assertFalse(task.run())
        finally:
            release()
        return task

    def test_a_rerun_that_cannot_replace_a_file_keeps_the_previous_shapefiles_whole(self):
        self.run_task("shp", recursive=False)
        previous = self.contents()
        with_buildings, without_buildings = self.sources()
        for source, held in ((with_buildings, "rezultat_imobile.dbf"),
                             (with_buildings, "rezultat_constructii.dbf"),
                             (without_buildings, "rezultat_constructii.dbf")):
            with self.subTest(source=os.path.basename(source), held=held):
                task = self.rerun(source, held)
                self.assertTrue(task.error_msg.startswith(
                    f"Fișierul '{held}' nu poate fi scris: este deschis în alt program"),
                    task.error_msg)
                self.assertEqual(self.contents(), previous)
        task = self.run_task("shp", source=with_buildings)
        self.assertEqual(self.layer(task, "imobile").featureCount(), 2)
        self.assertEqual(self.layer(task, "constructii").featureCount(), 2)


class ConversionRollbackTests(ConversionRerunTest):
    """A rerun that fails while its files take the place of the previous result."""

    def test_a_new_file_that_cannot_be_moved_in_leaves_the_previous_result_whole(self):
        import errno
        from unittest import mock
        self.run_task("shp", recursive=False)
        previous = self.contents()
        with_buildings, without_buildings = self.sources()
        replace = os.replace

        def failing(name):
            """os.replace, except that moving a new file onto name fails."""
            def move(source, target):
                if ".tmp" in os.path.basename(source) and os.path.basename(target) == name:
                    raise PermissionError(errno.EACCES, "Access is denied", source, None,
                                          target)
                return replace(source, target)
            return move
        for source, name in ((with_buildings, "rezultat_imobile.dbf"),
                             (with_buildings, "rezultat_constructii.dbf"),
                             (without_buildings, "rezultat_imobile.dbf")):
            with self.subTest(source=os.path.basename(source), failing=name):
                task = self.conversion(source)
                with mock.patch.object(os, "replace", failing(name)):
                    self.assertFalse(task.run())
                self.assertTrue(task.error_msg.startswith(
                    f"Fișierul '{name}' nu poate fi scris: este deschis în alt program"),
                    task.error_msg)
                self.assertEqual(self.contents(), previous)
        task = self.run_task("shp", source=with_buildings)
        self.assertEqual(self.layer(task, "imobile").featureCount(), 2)
        self.assertEqual(self.layer(task, "constructii").featureCount(), 2)

    def test_a_write_protected_file_of_the_previous_result_stops_the_rerun(self):
        import importlib.util
        import stat
        # a spatial index another program made beside the Shapefile, and a drawing
        cases = [("shp", "rezultat_imobile.qix")]
        if importlib.util.find_spec("ezdxf"):
            cases.append(("dxf", "rezultat.dxf"))
        with_buildings, _without = self.sources()
        for fmt_key, name in cases:
            with self.subTest(fmt=fmt_key):
                self.run_task(fmt_key, recursive=False)
                protected = os.path.join(self.out, name)
                if not os.path.exists(protected):
                    with open(protected, "wb") as fp:
                        fp.write(b"index")
                os.chmod(protected, stat.S_IREAD)
                try:
                    if os.access(protected, os.W_OK):
                        self.skipTest("this account can write a write-protected file")
                    previous = self.contents()
                    task = self.conversion(with_buildings, fmt_key)
                    self.assertFalse(task.run())
                    self.assertEqual(task.error_msg, (
                        f"Fișierul '{name}' nu poate fi scris: este deschis în alt program, "
                        "de exemplu Excel, sau este protejat la scriere. Închideți-l și "
                        "încercați din nou."))
                    self.assertEqual(self.contents(), previous)
                finally:
                    for entry in os.listdir(self.out):
                        os.chmod(os.path.join(self.out, entry), stat.S_IREAD | stat.S_IWRITE)


class SirutaConversionTests(ConversionTest):

    def test_the_next_conversion_has_the_names_once_the_database_can_be_read_again(self):
        import sqlite3
        from unittest import mock
        from cadex.services import siruta
        self.addCleanup(setattr, siruta, "_problem", "")
        self.addCleanup(setattr, siruta, "_tables", None)
        siruta._tables = None
        # as when the plugin folder is on a network share that cannot be reached for a moment
        refused = sqlite3.OperationalError("unable to open database file")
        with mock.patch.object(siruta.sqlite3, "connect", side_effect=refused):
            self.assertEqual({r["uat"] for r in self.run_task("xlsx").imobile}, {""})
        names = {r["uat"] for r in self.run_task("xlsx").imobile}
        self.assertNotEqual(names - {""}, set())
        self.assertEqual(siruta.problem(), "")


def tearDownModule():
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
