# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Column layouts, Diminuare arithmetic, Romanian number formatting, DXF label geometry and
output files. No QgsApplication."""

import json
import os
import shutil
import tempfile
import unittest

import helpers  # noqa: F401  sys.path bootstrap

from cadex.services import (
    cgxml_columns, diminuare_service, dxf_writer, fmt, outputs, siruta, xlsx,
)


class ColumnLayoutTests(unittest.TestCase):

    def test_default_names_fit_a_shapefile(self):
        for which in ("imobile", "constructii"):
            for column in cgxml_columns.SPECS[which]:
                self.assertLessEqual(len(column.name), cgxml_columns.SHP_NAME_LIMIT, column.name)
            self.assertEqual(cgxml_columns.layout_problems(
                cgxml_columns.default_layout(which), True), [])

    def test_labels_are_unique_as_they_head_the_excel_columns(self):
        for which in ("imobile", "constructii"):
            labels = [column.label for column in cgxml_columns.SPECS[which]]
            self.assertEqual(len(labels), len(set(labels)), which)

    def test_stored_layout_is_repaired(self):
        stored = json.dumps([
            {"key": "ie", "name": "NR_IE", "on": True},
            {"key": "gone", "name": "x", "on": True},
            {"key": "ie", "name": "dup", "on": False},
            {"key": "id", "name": "", "on": False},
        ])
        layout = cgxml_columns.layout_from_json("imobile", stored)
        keys = [c["key"] for c in layout]
        self.assertEqual(keys[:2], ["ie", "id"])           # stored order kept
        self.assertNotIn("gone", keys)                     # unknown dropped
        self.assertEqual(keys.count("ie"), 1)              # duplicate dropped
        self.assertEqual(layout[0]["name"], "NR_IE")       # rename kept
        self.assertEqual(layout[1]["name"], "id")          # empty name falls back
        self.assertEqual(len(layout), len(cgxml_columns.IMOBILE))  # the rest appended

    def test_garbage_is_the_default(self):
        for text in ("", "not json", "{}", "[1, 2]", None):
            layout = cgxml_columns.layout_from_json("constructii", text)
            self.assertEqual([c["key"] for c in layout],
                             [c.key for c in cgxml_columns.CONSTRUCTII])

    def test_round_trip(self):
        layout = cgxml_columns.default_layout("imobile")
        layout[0]["name"] = "ID_IMOBIL"
        again = cgxml_columns.layout_from_json(
            "imobile", cgxml_columns.layout_to_json(layout))
        self.assertEqual(again, layout)

    def test_problems(self):
        def layout(*names):
            return [{"key": f"k{i}", "name": n, "on": True} for i, n in enumerate(names)]
        self.assertTrue(cgxml_columns.layout_problems([], False))
        self.assertTrue(cgxml_columns.layout_problems(layout("a", ""), False))
        self.assertTrue(cgxml_columns.layout_problems(layout("suprafață"), False))
        self.assertTrue(cgxml_columns.layout_problems(layout("1col"), False))
        self.assertTrue(cgxml_columns.layout_problems(layout("Nume", "nume"), False))
        # distinct in a GeoPackage, the same once a Shapefile cuts them to 10 characters
        long_names = layout("suprafata_masurata", "suprafata_acte")
        self.assertEqual(cgxml_columns.layout_problems(long_names, False), [])
        self.assertTrue(cgxml_columns.layout_problems(long_names, True))

    def test_unchecked_columns_are_left_out(self):
        layout = [{"key": "id", "name": "id", "on": True},
                  {"key": "ie", "name": "ie", "on": False}]
        self.assertEqual(cgxml_columns.output_names(layout, False), [("id", "id")])


class DiminuareTests(unittest.TestCase):

    def test_parse_number(self):
        cases = {
            12: 12.0, 12.5: 12.5, "1.234,56": 1234.56, "1,5": 1.5, "1234.5": 1234.5,
            "2 500": 2500.0, "500 mp": 500.0, "1,234.5": 1234.5, "12.000": 12000.0,
            "abc": None, "-": None, None: None, "": None, True: None,
        }
        for value, expected in cases.items():
            self.assertEqual(diminuare_service.parse_number(value), expected, value)

    def test_a_column_takes_its_decimal_separator_from_the_other_cells(self):
        # "125.375" alone reads as in a Romanian sheet; next to "1250.5" the dot is decimal
        self.assertEqual(diminuare_service.parse_column(["125.375", "1250.5", None]),
                         [125.375, 1250.5, None])
        self.assertEqual(diminuare_service.parse_column(["1.500", "2,5"]), [1500.0, 2.5])
        self.assertEqual(diminuare_service.parse_column(["1,500", "2.25"]), [1500.0, 2.25])
        self.assertEqual(diminuare_service.parse_column(["12.000", 300.0]), [12000.0, 300.0])
        # cells that disagree are read one by one
        self.assertEqual(diminuare_service.parse_column(["1,5", "1250.5"]), [1.5, 1250.5])

    def test_title_rows_above_the_header_are_skipped(self):
        tmp = tempfile.mkdtemp(prefix="cadex_dim_")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "parcele.csv")
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("Tabel parcele tarla 12;;\n;;\nNr;Suprafata act;IE\n1;1000;\n2;2000;50124\n")
        _names, header, rows, numbers, header_row, _hidden = diminuare_service.read_sheet(path)
        self.assertEqual(header, ["Nr", "Suprafata act", "IE"])
        self.assertEqual(header_row, 3)
        self.assertEqual(numbers, [4, 5])
        self.assertEqual(len(rows), 2)

    def test_the_new_total_is_reported(self):
        result = diminuare_service.compute([1000, 2000], [False, False], 2500.4, 0)
        self.assertEqual(result.total_new, 2500)
        # a CF/IE parcel with decimals cannot reach a whole-metre contour
        result = diminuare_service.compute([300.5, 400, 400], [True, False, False], 1000, 0)
        self.assertEqual(result.total_new, 1000.5)

    def test_cadastral_number_column(self):
        self.assertTrue(diminuare_service.has_value("50124"))
        self.assertTrue(diminuare_service.has_value(50124))
        # an .xlsx gives every number back as float
        self.assertTrue(diminuare_service.has_value(50124.0))
        for empty in (None, "", " ", "-", "NULL", "0", 0, 0.0, float("nan")):
            self.assertFalse(diminuare_service.has_value(empty), empty)

    def test_cf_ie_is_written_back_as_in_the_table(self):
        tmp = tempfile.mkdtemp(prefix="cadex_dim_")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "diminuare.xlsx")
        result = diminuare_service.compute([1000.0, 2000.0], [True, False], 2500, 0)
        diminuare_service.write_excel(path, ["Nr", "Suprafata act", "IE"],
                                      [[1.0, 1000.0, 50124.0], [2.0, 2000.0, None]],
                                      result, [], 1, 2)
        _names, rows = xlsx.read_rows(path, "Diminuare")
        values = [value for row in rows for value in row]
        self.assertIn("50124", values)
        self.assertNotIn("50124.0", values)
        _names, summary = xlsx.read_rows(path, "Sumar")
        reduction = next(r for r in summary if r and r[0] == "Diminuare")
        # 2000 mp become 1500: a number, not the text "25.0000%"
        self.assertAlmostEqual(reduction[1], 0.25)

    def test_new_areas_add_up_to_the_contour(self):
        areas = [5000, 3000, 2500, 1500, 800]
        fixed = [False, True, False, False, False]
        for decimals, contour in ((0, 12000.0), (2, 12345.67)):
            result = diminuare_service.compute(areas, fixed, contour, decimals)
            self.assertEqual(result.error, "")
            self.assertAlmostEqual(sum(result.new_areas), contour, places=6)
            self.assertEqual(result.new_areas[1], 3000)  # the CF/IE parcel keeps its area

    def test_formula(self):
        result = diminuare_service.compute([6000, 4000], [False, False], 9000.0, 2)
        self.assertAlmostEqual(result.factor, 0.9)
        self.assertAlmostEqual(result.reduction_pct, 10.0)
        self.assertEqual(result.new_areas, [5400.0, 3600.0])

    def test_contour_larger_than_the_deeds_increases(self):
        result = diminuare_service.compute([1000, 1000, 1000], [False] * 3, 3001, 0)
        self.assertEqual(sum(result.new_areas), 3001)
        self.assertLess(result.reduction_pct, 0)

    def test_unreadable_rows_stay_out(self):
        result = diminuare_service.compute([1000, None, 1000], [False] * 3, 1500, 0)
        self.assertEqual(result.new_areas, [750, None, 750])
        self.assertEqual(result.bad_rows, [1])

    def test_refusals(self):
        self.assertIn("0", diminuare_service.compute([0, 0], [False, False], 10).error)
        self.assertIn("CF/IE", diminuare_service.compute([1000], [True], 1000).error)
        self.assertIn("mai mică", diminuare_service.compute([1000, 500], [True, False], 900).error)

    def test_distribute(self):
        self.assertEqual(diminuare_service.distribute([1.4, 1.4, 1.2], 5, 0), [2.0, 2.0, 1.0])
        self.assertEqual(diminuare_service.distribute([1.6, 1.6, 1.8], 4, 0), [1.0, 1.0, 2.0])
        self.assertEqual(sum(diminuare_service.distribute([0.333] * 3, 1, 2)), 1.0)


class SirutaTests(unittest.TestCase):

    def test_the_database_opens_from_any_folder_name(self):
        tmp = tempfile.mkdtemp(prefix="cadex_siruta_")
        saved = siruta.DB_PATH, siruta._tables
        try:
            # a QGIS profile can have any name
            for name in ("profil #2", "lucru 100%", "Ștefan"):
                folder = os.path.join(tmp, name)
                os.makedirs(folder)
                siruta.DB_PATH = shutil.copy(saved[0], folder)
                siruta._tables = None
                self.assertEqual(siruta.county_name("179141"), "București", name)
        finally:
            siruta.DB_PATH, siruta._tables = saved
            shutil.rmtree(tmp, ignore_errors=True)


class SirutaAccessTests(unittest.TestCase):
    """The SIRUTA names with the plugin folder on a network share or out of reach."""

    def setUp(self):
        path = siruta.DB_PATH

        def reload():
            siruta.DB_PATH = path
            siruta._tables, siruta._problem = None, ""
        self.addCleanup(reload)
        siruta._tables = None

    @unittest.skipUnless(os.name == "nt", "network paths in the form \\\\server\\share")
    def test_names_are_found_with_the_plugin_folder_on_a_network_share(self):
        # as with a QGIS profile in an AppData folder redirected to a network share
        drive, rest = os.path.splitdrive(os.path.realpath(siruta.DB_PATH))
        shared = "\\\\localhost\\" + drive[0] + "$" + rest
        if not os.path.isfile(shared):
            self.skipTest("the administrative share of the drive is not reachable")
        siruta.DB_PATH = shared
        self.assertEqual(siruta.county_name("179141"), "București")
        self.assertEqual(siruta.problem(), "")

    def test_a_database_that_cannot_be_opened_leaves_the_names_empty(self):
        import sqlite3
        from unittest import mock
        refused = sqlite3.OperationalError("unable to open database file")
        with mock.patch.object(siruta.sqlite3, "connect", side_effect=refused):
            self.assertEqual(siruta.county_name("179141"), "")
            self.assertEqual(siruta.uat_name("179132"), "")
        self.assertEqual(siruta.problem(),
                         "Baza de date SIRUTA a pluginului nu a putut fi citită, deci "
                         "denumirile de UAT, județ și localitate lipsesc din rezultat.")


class FormatTests(unittest.TestCase):

    def test_numbers(self):
        self.assertEqual(fmt.number(12000.0), "12.000")
        self.assertEqual(fmt.number(2.5), "2,5")
        self.assertEqual(fmt.number(1234567.1), "1.234.567,1")
        self.assertEqual(fmt.number(12.345, 2, thousands=False), "12,35")
        self.assertEqual(fmt.number(None), "")
        self.assertEqual(fmt.area(2628), "2.628 mp")
        self.assertEqual(fmt.percent(8.16326), "8,16%")

    def test_romanian_plural(self):
        words = ("imobil", "imobile")
        self.assertEqual(fmt.count(1, *words), "1 imobil")
        self.assertEqual(fmt.count(19, *words), "19 imobile")
        self.assertEqual(fmt.count(20, *words), "20 de imobile")
        self.assertEqual(fmt.count(101, *words), "101 imobile")
        self.assertEqual(fmt.count(1000, *words), "1.000 de imobile")
        self.assertEqual(fmt.count(0, *words), "0 imobile")

    def test_verb_agrees_with_the_count(self):
        sentences = ("{0} nu a putut fi citit.", "{0} nu au putut fi citite.")
        self.assertEqual(fmt.plural(1, *sentences).format(fmt.count(1, "fișier", "fișiere")),
                         "1 fișier nu a putut fi citit.")
        self.assertEqual(fmt.plural(21, *sentences).format(fmt.count(21, "fișier", "fișiere")),
                         "21 de fișiere nu au putut fi citite.")


class DxfLabelTests(unittest.TestCase):

    def test_label_is_placed_inside_a_u_shape(self):
        # the centroid of a U falls in the gap, the pole of inaccessibility must not
        u_shape = [[(0, 0), (30, 0), (30, 30), (20, 30), (20, 10), (10, 10), (10, 30), (0, 30)]]
        x, y = dxf_writer.pole_of_inaccessibility(u_shape)
        self.assertTrue(dxf_writer._signed_distance(x, y, u_shape) > 0)

    def test_label_is_not_placed_in_a_hole(self):
        donut = [[(0, 0), (100, 0), (100, 100), (0, 100)],
                 [(20, 20), (80, 20), (80, 80), (20, 80)]]
        x, y = dxf_writer.pole_of_inaccessibility(donut)
        self.assertTrue(dxf_writer._signed_distance(x, y, donut) > 0)

    def test_long_parcel_label_is_rotated_along_it(self):
        strip = [[(0, 0), (100, 50), (95, 60), (-5, 10)]]
        _x, _y, _height, angle = dxf_writer.label_layout(["ID 1"], strip)
        self.assertAlmostEqual(angle, 26.57, delta=2)
        square = [[(0, 0), (10, 0), (10, 10), (0, 10)]]
        self.assertEqual(dxf_writer.label_layout(["ID 1"], square)[3], 0.0)

    def test_text_height_is_bounded(self):
        tiny = [[(0, 0), (1, 0), (1, 1), (0, 1)]]
        huge = [[(0, 0), (1000, 0), (1000, 1000), (0, 1000)]]
        self.assertEqual(dxf_writer.label_layout(["ID 12345"], tiny)[2], dxf_writer.MIN_TEXT_HEIGHT)
        self.assertEqual(dxf_writer.label_layout(["ID 1"], huge)[2], dxf_writer.MAX_TEXT_HEIGHT)

    def test_cad_layer_name(self):
        self.assertEqual(dxf_writer.cad_layer_name("Construcții sector 2"), "CONSTRUCTII_SECTOR_2")
        self.assertEqual(dxf_writer.cad_layer_name("***"), "LAYER")


def hold_like_excel(path):
    """Open path the way Excel holds a workbook: others may read it, nobody may write it.
    Returns a function that releases it."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE)
    generic_read_write, share_read, open_existing = 0xC0000000, 0x1, 3
    handle = kernel32.CreateFileW(path, generic_read_write, share_read, None, open_existing,
                                  0, None)
    return lambda: kernel32.CloseHandle(handle)


class OutputTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_outputs_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name, content=b"x"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fp:
            fp.write(content)
        return path

    def test_new_and_free_files_can_be_written(self):
        free = self.path("raport.xlsx")
        self.assertEqual(outputs.blocked([free, os.path.join(self.tmp, "nou.xlsx"), ""]), [])

    def test_a_folder_in_place_of_the_file_cannot_be_written(self):
        folder = os.path.join(self.tmp, "raport.xlsx")
        os.makedirs(folder)
        [(path, message)] = outputs.blocked([folder])
        self.assertEqual(path, folder)
        self.assertTrue(message.startswith("Fișierul 'raport.xlsx' nu poate fi scris"), message)

    @unittest.skipUnless(os.name == "nt", "Excel's way of holding a file is a Windows lock")
    def test_a_workbook_open_in_excel_cannot_be_written(self):
        workbook = self.path("raport.xlsx")
        release = hold_like_excel(workbook)
        try:
            [(_path, message)] = outputs.blocked([workbook])
        finally:
            release()
        self.assertIn("este deschis în alt program, de exemplu Excel", message)
        self.assertEqual(outputs.blocked([workbook]), [])

    @unittest.skipUnless(os.name == "nt", "Excel's way of holding a file is a Windows lock")
    def test_a_shapefile_is_blocked_by_any_of_its_files(self):
        shp = self.path("imobile.shp")
        dbf = self.path("imobile.dbf")
        release = hold_like_excel(dbf)
        try:
            [(path, message)] = outputs.blocked([shp])
        finally:
            release()
        self.assertEqual(path, shp)
        self.assertIn("'imobile.dbf'", message)

    def test_parts_of_a_shapefile(self):
        self.assertEqual(outputs.parts("a/raport.xlsx"), ["a/raport.xlsx"])
        self.assertIn("a/imobile.dbf", outputs.parts("a/imobile.SHP"))
        self.assertIn("a/imobile.qml", outputs.parts("a/imobile.shp"))

    def test_messages(self):
        self.assertIn("folderul lui nu există",
                      outputs.write_error("x/raport.xlsx", FileNotFoundError(2, "lipsă")))
        self.assertIn("deschis în alt program", outputs.write_error("raport.xlsx"))


class DiminuareRulesTests(unittest.TestCase):
    """Diminuare Suprafețe on tables as surveyors keep them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_dimrules_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def csv(self, text):
        path = os.path.join(self.tmp, "parcele.csv")
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(text)
        return path

    def test_a_half_square_metre_is_rounded_up_as_by_hand(self):
        result = diminuare_service.compute([1000, 2000], [False, False], 2500.5, 0)
        self.assertEqual(result.total_new, 2501)
        # 1000.5 mp with a CF/IE parcel of 501 mp leaves 499.5 mp, so the sum is 1001 mp
        result = diminuare_service.compute([501, 600], [True, False], 1000.5, 0)
        self.assertEqual(result.new_areas, [501, 500])
        self.assertEqual(result.total_new, 1001)

    def test_a_negative_area_is_unreadable(self):
        result = diminuare_service.compute([-100, 1000, 1000], [False] * 3, 1500, 0)
        self.assertEqual(result.bad_rows, [0])
        self.assertEqual(result.new_areas, [None, 750, 750])

    def test_a_contour_that_leaves_nothing_for_the_other_parcels_is_refused(self):
        for contour in (1000, 1000.4):
            result = diminuare_service.compute([1000, 500], [True, False], contour, 0)
            self.assertIn("nu le rămâne nicio suprafață", result.error, contour)

    def test_each_refusal_names_its_cause(self):
        compute = diminuare_service.compute
        self.assertEqual(compute([None, None], [False, False], 100).error,
                         "Coloana aleasă nu conține nicio suprafață numerică.")
        self.assertEqual(compute([0.4, 0.5], [False, False], 0.8, 2).error,
                         "Parcelele fără CF/IE au împreună 0,9 mp, prea puțin pentru diminuare.")
        self.assertEqual(compute([1000, 0, 0], [True, False, False], 1200, 0).error,
                         "Parcelele fără CF/IE au împreună 0 mp, prea puțin pentru diminuare.")
        self.assertIn("Toate parcelele au CF/IE", compute([1000, None], [True, False], 900).error)

    def test_no_and_zero_in_the_cf_ie_column_mean_no_cf_ie(self):
        for empty in ("NU", "nu", "Nu", "NO", "FALSE", "FĂRĂ", "Fara", "0,0", "0.0", "0,00",
                      False):
            self.assertFalse(diminuare_service.has_value(empty), empty)
        for filled in ("50124", "CF 50124", "DA", 50124.0):
            self.assertTrue(diminuare_service.has_value(filled), filled)

    def test_a_total_row_under_the_parcels_is_left_out(self):
        path = self.csv("Nr;Proprietar;Suprafata act;IE\n1;A;10000;\n2;B;20000;\n3;C;15000;\n"
                        "TOTAL;;45000;\nSubtotal tarla 12;;30000;\n")
        table = diminuare_service.read_sheet(path)
        left_out = diminuare_service.total_rows(table.rows)
        self.assertEqual([table.numbers[i] for i in left_out], [5, 6])
        areas = diminuare_service.parse_column([row[2] for row in table.rows])
        result = diminuare_service.compute(areas, [False] * 5, 43200, 0, left_out)
        self.assertEqual(result.new_areas, [9600, 19200, 14400, None, None])
        self.assertEqual(result.total_deed, 45000)
        self.assertEqual(result.bad_rows, [])
        # a word that only starts with "total" is data
        self.assertEqual(diminuare_service.total_rows([["Suprafata totala", 1]]), [])

    def test_columns_with_the_same_name_are_named_with_their_letter(self):
        path = self.csv("Nr;Proprietar;Suprafata;CF;Suprafata\n1;A;1000;;900\n")
        self.assertEqual(diminuare_service.read_sheet(path).header,
                         ["Nr", "Proprietar", "Suprafata (C)", "CF", "Suprafata (E)"])

    def test_rows_hidden_in_excel_are_left_out_and_numbered(self):
        import zipfile
        path = os.path.join(self.tmp, "parcele.xlsx")
        wb = xlsx.Workbook()
        sheet = wb.add_sheet("Parcele")
        for row, values in enumerate([["Nr", "Suprafata act"], [1, 1000], [2, 2000], [3, 3000]],
                                     start=1):
            for col, value in enumerate(values, start=1):
                sheet.set(row, col, value)
        wb.save(path)
        # a filter hides row 3, the way Excel saves it
        with zipfile.ZipFile(path) as zf:
            parts = {name: zf.read(name) for name in zf.namelist()}
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b'<row r="3"', b'<row r="3" hidden="1"')
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in parts.items():
                zf.writestr(name, data)
        table = diminuare_service.read_sheet(path)
        self.assertEqual(table.numbers, [2, 4])
        self.assertEqual(table.hidden, [3])

    def test_a_cf_ie_number_reads_as_written(self):
        self.assertEqual(diminuare_service.cell_text(50124.0), "50124")
        self.assertEqual(diminuare_service.cell_text("50124"), "50124")
        self.assertEqual(diminuare_service.cell_text(None), "")


if __name__ == "__main__":
    unittest.main()
