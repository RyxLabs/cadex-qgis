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
        table = diminuare_service.read_sheet(path)
        self.assertEqual(table.header, ["Nr", "Suprafata act", "IE"])
        self.assertEqual(table.header_number, 3)
        self.assertEqual(table.numbers, [4, 5])
        self.assertEqual(len(table.rows), 2)

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

    def test_a_contour_larger_than_the_deeds_leaves_the_areas_as_they_are(self):
        result = diminuare_service.compute([1000, 1000, 1000], [False] * 3, 3001, 0)
        self.assertEqual(result.error, "")
        self.assertEqual(result.new_areas, [1000, 1000, 1000])
        self.assertEqual((result.factor, result.reduction_pct), (1.0, 0.0))
        # what the contour holds beyond the deeds, as a number
        self.assertEqual(result.surplus, 1.0)
        self.assertEqual(result.total_new, 3000)
        # the areas stay as written, whatever the rounding and whoever has a CF/IE
        result = diminuare_service.compute([300.5, 400.25, None], [True, False, False],
                                           1200.75, 0)
        self.assertEqual(result.new_areas, [300.5, 400.25, None])
        self.assertEqual((result.factor, result.surplus), (1.0, 500.0))
        result = diminuare_service.compute([1000, 500], [True, True], 1600, 2)
        self.assertEqual((result.error, result.new_areas, result.surplus),
                         ("", [1000, 500], 100.0))

    def test_a_contour_equal_to_the_deeds_or_smaller_has_no_surplus(self):
        for contour in (3000, 2999.5, 1500):
            result = diminuare_service.compute([1000, 1000, 1000], [False] * 3, contour, 0)
            self.assertEqual(result.surplus, 0.0, contour)
            self.assertLessEqual(result.factor, 1.0, contour)

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

    def test_distribute_lifts_no_value_above_its_cap(self):
        distribute = diminuare_service.distribute
        # the unit of the first value goes to the next largest remainder
        self.assertEqual(distribute([1.4, 1.4, 1.2], 5, 0, caps=[1.5, 2, 2]), [1.0, 2.0, 2.0])
        # units that no value can take are left out of the sum
        self.assertEqual(distribute([1.4, 1.4, 1.2], 5, 0, caps=[1.4, 2, 1.2]), [1.0, 2.0, 1.0])
        self.assertEqual(distribute([1.4, 1.4, 1.2], 5, 0, caps=[1.4, 1.4, 1.2]), [1.0, 1.0, 1.0])
        # a value equal to its cap is reached
        self.assertEqual(distribute([0.999, 1.999], 3, 0, caps=[1, 2]), [1.0, 2.0])
        self.assertEqual(distribute([1000.449, 0.004], 1000.46, 2, caps=[1000.45, 0.004]),
                         [1000.45, 0.0])


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

    def test_an_imobil_label_is_placed_beside_the_building_on_it(self):
        parcel = [[(0, 0), (40, 0), (40, 20), (0, 20)]]
        building = [[(10, 5), (30, 5), (30, 15), (10, 15)]]
        x, y, _height, _angle = dxf_writer.label_layout(["ID 1"], parcel, [building])
        self.assertTrue(0 < x < 40 and 0 < y < 20, (x, y))
        self.assertFalse(10 < x < 30 and 5 < y < 15, (x, y))

    def test_a_label_stays_inside_its_parcel_when_a_building_reaches_past_the_limit(self):
        parcel = [[(0, 0), (20, 0), (20, 20), (0, 20)]]
        building = [[(10, -5), (30, -5), (30, 12), (10, 12)]]
        x, y, _height, _angle = dxf_writer.label_layout(["ID 1"], parcel, [building])
        self.assertTrue(0 < x < 20 and 0 < y < 20, (x, y))
        self.assertFalse(10 < x < 30 and -5 < y < 12, (x, y))

    def test_a_parcel_covered_whole_by_its_buildings_keeps_its_label_inside(self):
        parcel = [[(0, 0), (20, 0), (20, 20), (0, 20)]]
        x, y, _height, _angle = dxf_writer.label_layout(["ID 1"], parcel, [parcel])
        self.assertTrue(0 < x < 20 and 0 < y < 20, (x, y))

    def test_a_label_is_no_taller_than_the_max_height_given(self):
        huge = [[(0, 0), (1000, 0), (1000, 1000), (0, 1000)]]
        self.assertEqual(dxf_writer.label_layout(["C1"], huge, max_height=1.5)[2], 1.5)

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

    def test_rounding_lifts_no_parcel_above_its_deed_area(self):
        areas, fixed = [1000.4, 1500.3, 2499.3], [False, True, False]
        for contour in (5000.0, 4999.6):
            result = diminuare_service.compute(areas, fixed, contour, 0)
            self.assertEqual(result.error, "", contour)
            self.assertEqual(result.new_areas, [1000, 1500.3, 2499], contour)
            # the 1 mp neither parcel could take is missing from the sum
            self.assertEqual((result.total_new, result.unassigned), (4999.3, 1.0), contour)
        # to two decimals
        result = diminuare_service.compute([1000.455, 2000.004], [False, False], 3000.458, 2)
        self.assertEqual(result.new_areas, [1000.45, 2000.0])
        self.assertEqual((result.total_new, result.unassigned), (3000.45, 0.01))

    def test_a_square_metre_one_parcel_cannot_take_goes_to_the_next(self):
        # the first parcel has the largest remainder, and 11 mp is above its deed area
        result = diminuare_service.compute([10.9, 1000, 1000], [False] * 3, 2009.9, 0)
        self.assertEqual(result.new_areas, [10, 1000, 1000])
        self.assertEqual((result.total_new, result.unassigned), (2010, 0.0))

    def test_a_parcel_reduced_by_more_than_the_rounding_takes_its_unit(self):
        result = diminuare_service.compute([1000.4, 2499.6], [False, False], 3150, 0)
        self.assertEqual(result.new_areas, [900, 2250])
        self.assertEqual(result.unassigned, 0.0)

    def test_a_contour_typed_as_the_sum_of_the_deeds_is_no_surplus(self):
        # as floats, 1000.3 + 2000.1 is less than 3000.4, by 4e-13
        result = diminuare_service.compute([1000.3, 2000.1], [False, False], 3000.4, 0)
        self.assertEqual((result.surplus, result.factor), (0.0, 1.0))
        self.assertEqual((result.new_areas, result.total_new), ([1000, 2000], 3000))
        self.assertEqual(diminuare_service.sum_note(result), "")
        # and to two decimals the areas are the deed areas
        result = diminuare_service.compute([1000.3, 2000.1], [False, False], 3000.4, 2)
        self.assertEqual((result.surplus, result.new_areas), (0.0, [1000.3, 2000.1]))

    def test_a_sum_that_is_not_the_rounded_contour_is_stated_with_its_cause(self):
        compute, note = diminuare_service.compute, diminuare_service.sum_note
        rule = ("La rotunjire, nicio parcelă nu primește o suprafață mai mare decât cea din "
                "acte")
        result = compute([1000.4, 2499.6], [False, False], 3499.6, 0)
        self.assertEqual(result.new_areas, [1000, 2499])
        self.assertEqual(note(result), rule + ", deci suma nouă este 3.499 mp, nu 3.500 mp.")
        # with a CF/IE parcel that holds decimals, the two causes add up
        result = compute([1000.4, 1500.3, 2499.3], [False, True, False], 4999.6, 0)
        self.assertEqual(note(result), (
            rule + ", iar parcelele cu CF/IE au mai multe zecimale decât rotunjirea aleasă, "
            "deci suma nouă este 4.999,3 mp, nu 5.000 mp."))
        result = compute([300.5, 400, 400], [True, False, False], 1000, 0)
        self.assertEqual(note(result), (
            "Parcelele cu CF/IE au mai multe zecimale decât rotunjirea aleasă, deci suma nouă "
            "este 1.000,5 mp, nu 1.000 mp."))
        # no note for a sum equal to the rounded contour
        self.assertEqual(note(compute([1000, 2000], [False, False], 2500.4, 0)), "")
        self.assertEqual(note(compute([1000, 2000], [False, False], 2700, 2)), "")
        # a contour above the sum of the deed areas leaves the deed areas
        self.assertEqual(note(compute([1000.4, 2000], [False, False], 3500, 0)),
                         "Conturul depășește suma suprafețelor din acte cu 499,6 mp, deci "
                         "suprafețele rămân cele din acte.")

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
        self.assertEqual(compute([1000, 0, 0], [True, False, False], 900, 0).error,
                         "Parcelele fără CF/IE au împreună 0 mp, prea puțin pentru diminuare.")
        self.assertIn("Toate parcelele au CF/IE", compute([1000, None], [True, False], 900).error)

    def test_no_and_zero_in_the_cf_ie_column_mean_no_cf_ie(self):
        for empty in ("NU", "nu", "Nu", "NO", "FALSE", "FĂRĂ", "Fara", "0,0", "0.0", "0,00",
                      False):
            self.assertFalse(diminuare_service.has_value(empty), empty)
        for filled in ("50124", "CF 50124", "DA", 50124.0):
            self.assertTrue(diminuare_service.has_value(filled), filled)

    def test_words_and_marks_for_a_missing_cf_ie_mean_no_cf_ie(self):
        for empty in ("N/A", "n/a", "NU ARE", "nu are", "lipsa", "Lipsă", "LIPSĂ", "?", "—",
                      "–", "--", "- -", " ? "):
            self.assertFalse(diminuare_service.has_value(empty), empty)
        for filled in ("NA 1024", "CF?", "50124-C1", "X"):
            self.assertTrue(diminuare_service.has_value(filled), filled)

    def totals(self, text, area="Suprafata act"):
        """(the Excel row numbers of the total rows with the reason of each, the table, its
        parsed areas) of a parcel table given as CSV text."""
        table = diminuare_service.read_sheet(self.csv(text))
        areas = diminuare_service.parse_column(
            [row[table.header.index(area)] for row in table.rows])
        found = diminuare_service.total_rows(
            table.rows, areas, diminuare_service.total_candidates(table.rows))
        return {table.numbers[i]: reason for i, reason in found.items()}, table, areas

    def test_a_total_row_under_the_parcels_is_left_out(self):
        found, table, areas = self.totals(
            "Nr;Proprietar;Suprafata act;IE\n1;A;10000;\n2;B;20000;\n3;C;15000;\n"
            "TOTAL;;45000;\nSubtotal tarla 12;;30000;\n")
        self.assertEqual(found, dict.fromkeys((5, 6), diminuare_service.TOTAL_BY_WORD))
        left_out = diminuare_service.total_rows(
            table.rows, areas, diminuare_service.total_candidates(table.rows))
        result = diminuare_service.compute(areas, [False] * 5, 43200, 0, left_out)
        self.assertEqual(result.new_areas, [9600, 19200, 14400, None, None])
        self.assertEqual(result.total_deed, 45000)
        self.assertEqual(result.bad_rows, [])
        # a word that only starts with "total" is data
        rows = [["Suprafata totala", 1]]
        self.assertEqual(diminuare_service.total_rows(
            rows, [1.0], diminuare_service.total_candidates(rows)), {})

    def reduced(self, rows, area=1):
        """(the total rows with the reason of each, the factor) for rows as read_sheet() gives
        them, with a contour of nine tenths of the parcels, a parcel being a row with no cell
        containing "total" or "sum"."""
        areas = diminuare_service.parse_column([row[area] for row in rows])
        found = diminuare_service.total_rows(rows, areas, diminuare_service.total_candidates(rows))
        parcels = sum(a for row, a in zip(rows, areas) if not any(
            isinstance(v, str) and ("total" in v.lower() or "sum" in v.lower()) for v in row))
        result = diminuare_service.compute(areas, [False] * len(rows), parcels * 0.9, 0, found)
        self.assertEqual(result.error, "")
        return found, round(result.factor, 4)

    def test_a_row_named_total_is_a_total_row_whatever_its_area(self):
        word = diminuare_service.TOTAL_BY_WORD
        # the total also counts a row hidden in Excel, which the table read does not hold
        self.assertEqual(self.reduced(
            [["Popescu Ion", 1000.0], ["Ionescu Maria", 1500.0], ["TOTAL", 3200.0]]),
            ({2: word}, 0.9))
        # typed by hand, 2 mp off
        self.assertEqual(self.reduced(
            [["Popescu Ion", 1000.4], ["Ionescu Maria", 1500.4], ["Total", 2503.0]]),
            ({2: word}, 0.9))
        # above its parcels
        self.assertEqual(self.reduced(
            [["TOTAL", 2500.0], ["Popescu Ion", 1000.0], ["Ionescu Maria", 1500.0]]),
            ({0: word}, 0.9))
        # 200 mp off, with the unit written beside every area
        self.assertEqual(self.reduced(
            [[1, "Popescu Ion", 1000, "mp"], [2, "Ionescu Maria", 1500, "mp"],
             [3, "Vasile Dan", 700, "mp"], [None, "TOTAL", 3000, "mp"]], area=2),
            ({3: word}, 0.9))

    def test_the_name_of_a_total_row_can_say_what_it_adds_up(self):
        for name in ("TOTAL", "Total:", "total general", "TOTAL GENERAL:", "Subtotal tarla 12",
                     "Total T 12", "Total T12", "Subtotal sector 3", "Sumă", "SUMA:",
                     "Total (mp)", "Total general tarla 12/1", "Totaluri", "- TOTAL -",
                     "Total tarla nr. 12", "Total sector cadastral 3", "Total suprafață",
                     "TOTAL SUPRAFEȚE (ha)", "Total parcele"):
            found, factor = self.reduced([["Popescu Ion", 1000.0], ["Ionescu Maria", 1500.0],
                                          [name, 3200.0]])
            self.assertEqual((found, factor), ({2: diminuare_service.TOTAL_BY_WORD}, 0.9), name)

    def test_a_total_whose_other_cells_hold_dashes_is_read_from_a_workbook(self):
        import zipfile
        path = os.path.join(self.tmp, "parcele.xlsx")
        wb = xlsx.Workbook()
        sheet = wb.add_sheet("Parcele")
        rows = [["Nr", "Proprietar", "Tarla", "Suprafata", "CF"],
                [1, "Popescu Ion", "12", 1000, "50123"], [2, "Ionescu Maria", "12", 1500, "-"],
                [3, "Vasile Dan", "12", 700, "-"], ["-", "TOTAL", "-", 3200, "-"]]
        for row, values in enumerate(rows, start=1):
            for col, value in enumerate(values, start=1):
                sheet.set(row, col, value)
        wb.save(path)
        # the third parcel is hidden in Excel, and the total still counts it
        with zipfile.ZipFile(path) as zf:
            parts = {name: zf.read(name) for name in zf.namelist()}
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b'<row r="4"', b'<row r="4" hidden="1"')
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in parts.items():
                zf.writestr(name, data)
        table = diminuare_service.read_sheet(path)
        self.assertEqual((table.numbers, table.hidden), ([2, 3, 5], [4]))
        self.assertEqual(self.reduced(table.rows, area=3),
                         ({2: diminuare_service.TOTAL_BY_WORD}, 0.9))

    def test_a_parcel_with_the_word_total_in_its_owner_or_remark_stays_a_parcel(self):
        found, table, areas = self.totals(
            "Nr;Proprietar;Suprafata act;IE;Obs\n1;POPESCU ION;5000;;\n"
            "2;SC TOTAL AGRO SRL;20000;;\n3;IONESCU MARIA;5000;;\n"
            "4;DINU ANA;10000;;din total 5000 mp arabil\n5;ȘUMA GHEORGHE;2000;;\n"
            "6;SUMA ION;3000;;\n")
        self.assertEqual(found, {})
        result = diminuare_service.compute(areas, [False] * 6, 40500, 0, found)
        self.assertAlmostEqual(result.factor, 0.9)
        self.assertEqual(result.new_areas, [4500, 18000, 4500, 9000, 1800, 2700])
        # the rows that hold the word, for a note to the user; Șuma is a name, not sumă
        self.assertEqual([table.numbers[i] for i in diminuare_service.total_candidates(
            table.rows)], [3, 5, 7])

    def test_a_longer_text_with_the_word_is_a_total_when_its_area_is_the_sum_above(self):
        # no column a parcel fills is empty in these rows, so only their areas tell
        found, _table, _areas = self.totals(
            "Proprietar;Suprafata act\nPOPESCU ION;5000,5\nDINU ANA;20000\n"
            "Total conform acte;25000,5\nIONESCU MARIA;700\nPAVEL DAN;300,4\n"
            "Subtotal parcele noi;1000\nTotal general conform acte;26001\n")
        self.assertEqual(found, dict.fromkeys((4, 7, 8), diminuare_service.TOTAL_BY_SUM))

    def test_an_owner_named_total_with_another_area_than_the_sum_stays_a_parcel(self):
        found, _table, _areas = self.totals(
            "Proprietar;Suprafata act\nPOPESCU ION;5000\nDINU ANA;7000\nTOTAL AGRO SRL;20000\n")
        self.assertEqual(found, {})

    def test_a_parcel_as_large_as_the_one_above_is_not_its_total(self):
        found, _table, _areas = self.totals(
            "Proprietar;Suprafata act;Obs\nPOPESCU ION;5000;\nDINU ANA;5000;din total 10000\n")
        self.assertEqual(found, {})

    def test_a_longer_text_with_the_word_and_only_numbers_beside_it_is_a_total(self):
        # neither area is the sum of the rows above; the rows lack the number or the owner
        # that every parcel has, and a dash or a unit in a cell is no data
        found, _table, _areas = self.totals(
            "Nr;Proprietar;Suprafata act;IE;UM\n1;A;10000;;mp\n2;B;20000;;mp\n"
            "-;Total conform acte;29000;-;mp\n3;C;15000;50124;mp\n4;D;100;;mp\n"
            "Sumă parcele noi;;15000;;m2\n")
        self.assertEqual(found, {4: diminuare_service.TOTAL_BY_LABEL,
                                 7: diminuare_service.TOTAL_BY_LABEL})

    def test_total_rows_judges_the_candidates_it_is_given(self):
        table = diminuare_service.read_sheet(self.csv(
            "Proprietar;Suprafata act;Vechi\nA;5000;4000\nB;7000;9000\n"
            "Total conform acte;12000;20000\n"))
        candidates = diminuare_service.total_candidates(table.rows)
        # the same rows, judged again for each column chosen as the area
        self.assertEqual(diminuare_service.total_rows(
            table.rows, [5000.0, 7000.0, 12000.0], candidates),
            {2: diminuare_service.TOTAL_BY_SUM})
        self.assertEqual(diminuare_service.total_rows(
            table.rows, [4000.0, 9000.0, 20000.0], candidates), {})
        # without candidates no row is a total row
        self.assertEqual(diminuare_service.total_rows(
            table.rows, [5000.0, 7000.0, 12000.0], {}), {})

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

    def summary_values(self, areas, contour):
        """{name: value} of the Calcul table in the Sumar sheet, and the rows of the Diminuare
        sheet, for a saved result."""
        path = os.path.join(self.tmp, "diminuare.xlsx")
        result = diminuare_service.compute(areas, [False] * len(areas), contour, 0)
        cut = diminuare_service.write_excel(
            path, ["Nr", "Suprafata act"], [[float(i), a] for i, a in enumerate(areas, 1)],
            result, [], 1, None)
        self.assertEqual(cut, 0)
        return helpers.summary_facts(path), xlsx.read_rows(path, "Diminuare")[1]

    def test_the_saved_result_states_what_the_contour_holds_beyond_the_deeds(self):
        values, rows = self.summary_values([1000.5, 2000.0], 3500)
        self.assertEqual((values["C - A"], values["Factor"], values["Diminuare"]),
                         (499.5, 1.0, 0.0))
        # the areas as in the deeds, the difference 0
        self.assertEqual([row[2:] for row in rows[1:]], [[1000.5, 0.0], [2000.0, 0.0]])

    def test_a_reduction_has_no_surplus_row(self):
        values, rows = self.summary_values([1000.0, 2000.0], 2700)
        self.assertNotIn("C - A", values)
        self.assertAlmostEqual(values["Factor"], 0.9)
        self.assertEqual([row[2:] for row in rows[1:]], [[900.0, -100.0], [1800.0, -200.0]])

    def test_a_cf_ie_number_reads_as_written(self):
        self.assertEqual(diminuare_service.cell_text(50124.0), "50124")
        self.assertEqual(diminuare_service.cell_text("50124"), "50124")
        self.assertEqual(diminuare_service.cell_text(None), "")


if __name__ == "__main__":
    unittest.main()
