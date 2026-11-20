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
        # the column of the cut text has the width of the limit
        self.assertEqual(imobile.fields().field("note").length(), 254)

    def test_a_column_empty_or_short_at_conversion_takes_a_value_typed_in_afterwards(self):
        # a file without street, tarla, title, old numbers or notes
        source = os.path.join(self.tmp, "necompletat")
        synthetic.write(source, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1",
            buildings=[synthetic.Building(1, [synthetic.square(500002, 400002, 4)])]))
        every_column = {which: [dict(column, on=True) for column in layout]
                        for which, layout in layouts().items()}
        task = self.run_task("shp", source=source, layout=every_column)
        free_text = "ă" * 127  # 254 bytes, the most a Shapefile text holds
        typed = {
            "adresa": free_text, "desc_adr": free_text, "propr": free_text,
            "alte_pers": free_text, "note": free_text,
            "judet": "Bistrița-Năsăud", "uat": "Drobeta-Turnu Severin",
            "localitate": "Schela Cladovei", "nr_postal": "10A", "tip_teren": "Extravilan",
            "nr_tarla": "45/1", "nr_parcele": "1234/5, 1234/6, 1240", "categ_fol": "CC, A, V",
            "nr_titlu": "12345/67 din 12.03.1995", "imprej": "Nu", "cad_vechi": "1234/5",
            "cf_vechi": "560/N"}
        self.assertEqual(helpers.typed_in(task.outputs["imobile"], typed), typed)
        typed = {
            "propr": free_text, "alte_pers": free_text, "note": free_text, "nr_constr": "C12",
            "ie": "100234-C12", "tip_teren": "Extravilan", "destinatie": "CIE",
            "dest_text": "Construcții industriale și edilitare", "are_acte": "Nu"}
        self.assertEqual(helpers.typed_in(task.outputs["constructii"], typed), typed)

    def test_a_text_column_holds_its_longest_value_whole(self):
        # longer than a nr. poștal or a nr. tarla usually is
        number, plot = "12-14-16-18-20-22-24-26-28-30 bis", "T" * 120
        source = os.path.join(self.tmp, "lung")
        synthetic.write(source, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1", street="Mare",
            postal_number=number, plot=plot))
        task = self.run_task("shp", source=source)
        [feature] = self.layer(task, "imobile").getFeatures()
        self.assertEqual((feature["nr_postal"], feature["nr_tarla"]), (number, plot))
        self.assertEqual(task.truncated, 0)


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

    def test_dxf_polylines_and_labels(self):
        labels = {"imobile": [{"key": "id", "prefix": "ID ", "suffix": "", "on": True},
                              {"key": "ie", "prefix": "IE ", "suffix": "", "on": True},
                              {"key": "sup_mas", "prefix": "", "suffix": " mp", "on": True}],
                  "constructii": [{"key": "nr_constr", "on": True}]}
        task = self.run_task("dxf", labels=labels)
        outlines, texts = drawing(task.outputs["file"])
        # 3 imobile, one of them with a hole, plus the construcție
        self.assertEqual(sorted(layer for layer, _points, _color in outlines),
                         ["CONSTRUCTII", "IMOBILE", "IMOBILE", "IMOBILE", "IMOBILE"])
        self.assertEqual({layer for layer, _text in texts}, {"TEXT_IMOBILE", "TEXT_CONSTRUCTII"})
        for text in ("ID 10188", "IE 100001", "9900 mp"):
            self.assertIn(("TEXT_IMOBILE", text), texts)
        self.assertIn(("TEXT_CONSTRUCTII", "C1"), texts)


def drawing(path, placed=False):
    """(outlines as [(layer, closed ring as points, "#rrggbb")], texts as [(layer, text)]) of
    a DXF read through GDAL; with placed, each text is (layer, text, x, y, height)."""
    import contextlib
    import re
    from osgeo import ogr
    outlines, texts = [], []
    # GDAL 3.7 and later warn about a call made without choosing how errors are reported
    manager = getattr(ogr, "ExceptionMgr", contextlib.nullcontext)
    with manager():
        source = ogr.Open(path)
        for feature in source.GetLayer(0):
            geometry = feature.GetGeometryRef()
            layer = feature.GetField("Layer")
            if geometry.GetGeometryName() == "POINT" and placed:
                size = re.search(r"s:([0-9.]+)g", feature.GetStyleString() or "")
                texts.append((layer, feature.GetField("Text"), geometry.GetX(), geometry.GetY(),
                              float(size.group(1))))
            elif geometry.GetGeometryName() == "POINT":
                texts.append((layer, feature.GetField("Text")))
            else:
                color = re.search(r"c:(#[0-9a-fA-F]{6})", feature.GetStyleString() or "")
                outlines.append((layer, [(x, y) for x, y, *_z in geometry.GetPoints()],
                                 color.group(1).lower() if color else None))
        source = None
    return outlines, texts


class DxfDrawingTests(unittest.TestCase):
    """The DXF drawing as a CAD program reads it: closed outlines on the layer of their kind,
    labels as written, metres as the unit."""

    SQUARE = [(500000.0, 400000.0), (500040.0, 400000.0), (500040.0, 400040.0),
              (500000.0, 400040.0)]
    HOLE = [(500010.0, 400010.0), (500020.0, 400010.0), (500020.0, 400020.0),
            (500010.0, 400020.0)]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_dxf_")
        self.path = os.path.join(self.tmp, "desen.dxf")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def write(self, texts=("ID 501",), stop=None):
        from cadex.services import dxf_writer
        records = [{"rings": [[[(x + 100 * n, y) for x, y in self.SQUARE]]], "text": text}
                   for n, text in enumerate(texts)]
        records[0]["rings"][0].append(self.HOLE)
        return dxf_writer.write(
            self.path, [("IMOBILE", (6, "#FF00FF"), records, lambda r: [r["text"]]),
                        ("CONSTRUCTII", (4, "#00B8E6"), [{"rings": [[self.HOLE]]}],
                         lambda r: [])], *([stop] if stop else []))

    def test_each_ring_is_a_closed_outline_of_its_points_in_the_color_of_its_kind(self):
        self.assertTrue(self.write())
        outlines, _texts = drawing(self.path)
        self.assertEqual([(layer, points[:-1]) for layer, points, _color in outlines],
                         [("IMOBILE", self.SQUARE), ("IMOBILE", self.HOLE),
                          ("CONSTRUCTII", self.HOLE)])
        for _layer, points, _color in outlines:
            self.assertEqual(points[0], points[-1])
        self.assertEqual(outlines[0][2], "#ff00ff")
        self.assertIsNotNone(outlines[2][2])
        self.assertNotEqual(outlines[2][2], outlines[0][2])

    def test_each_outline_gives_its_vertex_count_before_its_closed_flag(self):
        # AutoCAD reads the closed flag (70) only after the vertex count (90); GDAL reads
        # either order, so this is checked on the text of the file
        self.assertTrue(self.write())
        with open(self.path, encoding="latin-1") as fp:
            lines = [line.strip() for line in fp]
        pairs = list(zip(lines[0::2], lines[1::2]))
        starts = [i for i, pair in enumerate(pairs) if pair == ("0", "LWPOLYLINE")]
        self.assertEqual(len(starts), 3)
        for start in starts:
            end = next(i for i in range(start + 1, len(pairs)) if pairs[i][0] == "0")
            codes = [code for code, _value in pairs[start:end]]
            values = dict(pairs[start:end])
            self.assertLess(codes.index("90"), codes.index("70"))
            self.assertEqual(int(values["70"]) & 1, 1)
            self.assertEqual(int(values["90"]), codes.count("10"))

    def test_the_drawing_is_in_metres_and_is_all_there_is_in_its_folder(self):
        self.assertTrue(self.write())
        with open(self.path, encoding="latin-1") as fp:
            lines = [line.strip() for line in fp]
        units = lines.index("$INSUNITS")
        # 6 is the metre
        self.assertEqual(lines[units + 1:units + 3], ["70", "6"])
        self.assertEqual(os.listdir(self.tmp), ["desen.dxf"])

    def test_labels_keep_the_characters_of_their_text(self):
        texts = ['POPESCU "NELU" ION', "D:\\livrare\\P1", "Brașov, Țara Bârsei, î â ă",
                 "50% din 1/2 & <a>; b, c (d): e", "d'Artagnan ^J %%u", "x" * 300]
        self.assertTrue(self.write(texts))
        _outlines, written = drawing(self.path)
        self.assertEqual(written, [("TEXT_IMOBILE", text) for text in texts])

    def test_a_label_is_one_line_and_its_braces_are_parentheses(self):
        self.assertTrue(self.write(["rând 1\r\nrând 2\tși {acolade}", "\n", "😀 la final"]))
        _outlines, written = drawing(self.path)
        self.assertEqual(written, [("TEXT_IMOBILE", "rând 1 rând 2 și (acolade)"),
                                   ("TEXT_IMOBILE", "? la final")])

    def test_a_canceled_drawing_leaves_no_file(self):
        self.assertFalse(self.write(stop=lambda: True))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_a_drawing_that_cannot_be_created_raises_the_reason_with_its_path(self):
        from cadex.services import dxf_writer
        missing = os.path.join(self.tmp, "nu_exista", "desen.dxf")
        with self.assertRaises(FileNotFoundError) as raised:
            dxf_writer.write(missing, [])
        self.assertEqual(raised.exception.filename, missing)

    def test_two_kinds_with_one_layer_name_share_the_layer(self):
        from cadex.services import dxf_writer
        records = [{"rings": [[self.SQUARE]]}]
        self.assertTrue(dxf_writer.write(self.path, [
            ("CLADIRI", (6, "#FF00FF"), records, lambda r: ["ID 1"]),
            ("CLADIRI", (4, "#00B8E6"), [{"rings": [[self.HOLE]]}], lambda r: ["C1"])]))
        with open(self.path, encoding="latin-1") as fp:
            lines = [line.strip() for line in fp]
        # one definition of each layer in the drawing's layer table
        table = lines[lines.index("AcDbLayerTableRecord"):lines.index("ENTITIES")]
        self.assertEqual((table.count("CLADIRI"), table.count("TEXT_CLADIRI")), (1, 1))
        outlines, texts = drawing(self.path)
        self.assertEqual([layer for layer, _points, _color in outlines], ["CLADIRI", "CLADIRI"])
        self.assertEqual(texts, [("TEXT_CLADIRI", "ID 1"), ("TEXT_CLADIRI", "C1")])

    def test_the_drawing_is_written_by_a_gdal_that_reports_errors_as_return_values(self):
        import warnings
        from unittest import mock
        from osgeo import gdal, ogr
        # GDAL before 3.7 has no ExceptionMgr
        with mock.patch.object(gdal, "ExceptionMgr", None), \
                mock.patch.object(ogr, "ExceptionMgr", None), warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            self.assertTrue(self.write())
            missing = os.path.join(self.tmp, "nu_exista", "desen.dxf")
            from cadex.services import dxf_writer
            with self.assertRaises(FileNotFoundError):
                dxf_writer.write(missing, [])
        outlines, texts = drawing(self.path)
        self.assertEqual((len(outlines), texts), (3, [("TEXT_IMOBILE", "ID 501")]))

    def test_when_gdal_cannot_find_its_dxf_header_file_the_outlines_keep_their_color(self):
        from unittest import mock
        from osgeo import gdal
        with mock.patch.object(gdal, "FindFile", lambda *args: None):
            self.assertTrue(self.write())
        outlines, texts = drawing(self.path)
        self.assertEqual([(layer, len(points)) for layer, points, _color in outlines],
                         [("IMOBILE", 5), ("IMOBILE", 5), ("CONSTRUCTII", 5)])
        self.assertEqual(outlines[0][2], "#ff00ff")
        self.assertEqual(texts, [("TEXT_IMOBILE", "ID 501")])

    def test_layer_names_in_other_alphabets_give_a_drawing_with_ascii_layer_names(self):
        cgxml_export.write_dxf(
            self.path, {"imobile": "Łąka", "constructii": "Кадастр"}, {},
            [{"fisier": "1.cgxml", "rings": [[self.SQUARE]]}],
            [{"fisier": "1.cgxml", "rings": [[self.HOLE]]}])
        outlines, _texts = drawing(self.path)
        layers = [layer for layer, _points, _color in outlines]
        self.assertEqual(len(layers), 2)
        self.assertTrue(all(layer.isascii() for layer in layers), layers)
        self.assertNotEqual(layers[0], layers[1])


class ColumnCatalogTests(unittest.TestCase):

    def test_every_column_of_the_catalog_is_a_value_of_the_converted_records(self):
        read = cgxml_export.read_all([helpers.DATA_DIR])
        for which, records in (("imobile", read.imobile), ("constructii", read.constructii)):
            self.assertTrue(records, which)
            for record in records:
                missing = [column.key for column in cgxml_columns.SPECS[which]
                           if column.key not in record]
                self.assertEqual(missing, [], (which, record["fisier"]))


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

    def test_a_text_longer_than_an_excel_cell_is_cut_and_counted(self):
        owners = tuple(f"PROPRIETAR NUMARUL {n:05d}" for n in range(2000))
        source = self.folder("multi", ("1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1", owners=owners)))
        task = self.task("xlsx", source)
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(task.cut_cells, 1)
        _names, rows = xlsx.read_rows(task.outputs["file"], "Imobile")
        owners_cell = rows[1][rows[0].index("Proprietari și posesori")]
        self.assertEqual(len(owners_cell), xlsx.MAX_CELL_TEXT)

    def test_a_table_whose_texts_fit_has_no_cut_cell(self):
        task = self.task("xlsx", self.many("scurte", 3))
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(task.cut_cells, 0)

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

    def test_a_label_in_a_sliver_along_an_axis_is_placed_with_bounded_work(self):
        from unittest import mock
        from cadex.services import dxf_writer
        # 100 m long, its third point 1 mm off the base: a hundred thousand times longer
        # than wide
        ring = [(500000.0, 400000.0), (500100.0, 400000.0), (500050.0, 400000.001)]
        distance = dxf_writer._signed_distance
        calls = []
        with mock.patch.object(dxf_writer, "_signed_distance",
                               lambda *args: calls.append(1) or distance(*args)):
            x, y, _height, _rotation = dxf_writer.label_layout(["ID 10188"], [ring])
        self.assertLessEqual(len(calls), 3000)
        self.assertGreater(distance(x, y, [ring]), 0)


class DxfLabelPlacementTests(unittest.TestCase):
    """Labels in the DXF of a conversion, read back through GDAL."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_dxf_labels_")
        self.addCleanup(helpers.remove_folder, self.tmp)

    def labels(self, imobile, constructii):
        """[(layer, text, x, y, height)] of the labels of a DXF written for the records."""
        path = os.path.join(self.tmp, "etichete.dxf")
        config = {"imobile": [{"key": "id", "on": True, "prefix": "ID ", "suffix": ""}],
                  "constructii": [{"key": "nr_constr", "on": True}]}
        cgxml_export.write_dxf(path, NAMES, config, imobile, constructii)
        _outlines, texts = drawing(path, placed=True)
        return texts

    def test_the_label_of_an_imobil_is_placed_off_its_building(self):
        x0, y0 = 500000.0, 400000.0
        parcel = [(x0, y0), (x0 + 40, y0), (x0 + 40, y0 + 20), (x0, y0 + 20)]
        house = [(x0 + 10, y0 + 5), (x0 + 30, y0 + 5), (x0 + 30, y0 + 15), (x0 + 10, y0 + 15)]
        labels = self.labels([{"fisier": "1.cgxml", "id": 1, "rings": [[parcel]]}],
                             [{"fisier": "1.cgxml", "nr_constr": "C1", "rings": [[house]]}])
        [(x, y)] = [(x, y) for layer, text, x, y, _h in labels if text == "ID 1"]
        self.assertFalse(x0 + 10 < x < x0 + 30 and y0 + 5 < y < y0 + 15, (x - x0, y - y0))
        self.assertTrue(x0 < x < x0 + 40 and y0 < y < y0 + 20, (x - x0, y - y0))

    def test_a_construction_label_is_smaller_than_an_imobil_label_in_the_same_space(self):
        x0, y0 = 500000.0, 400000.0
        big = [(x0, y0), (x0 + 100, y0), (x0 + 100, y0 + 100), (x0, y0 + 100)]
        hall = [(x0 + 200, y0), (x0 + 300, y0), (x0 + 300, y0 + 100), (x0 + 200, y0 + 100)]
        labels = self.labels([{"fisier": "1.cgxml", "id": 1, "rings": [[big]]}],
                             [{"fisier": "2.cgxml", "nr_constr": "C1", "rings": [[hall]]}])
        heights = {text: height for _layer, text, _x, _y, height in labels}
        self.assertEqual(heights["ID 1"], 2.5)
        self.assertEqual(heights["C1"], 1.5)


class ConversionCountTests(ConversionTest):
    """What a conversion counts when a source cannot be read."""

    def test_a_damaged_archive_is_not_counted_as_a_cgxml_file_found(self):
        damaged = os.path.join(self.tmp, "descarcare.zip")
        with open(damaged, "wb") as fp:
            fp.write(b"PK\x03\x04 o descarcare intrerupta")
        task = self.run_task("xlsx", source=[self.src, damaged], recursive=False)
        facts = helpers.summary_facts(task.outputs["file"])
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
        import stat
        # a spatial index another program made beside the Shapefile, and a drawing
        cases = [("shp", "rezultat_imobile.qix"), ("dxf", "rezultat.dxf")]
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
