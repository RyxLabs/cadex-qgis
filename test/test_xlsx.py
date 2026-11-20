# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Excel writer and reader. Files are read back by CADex's reader and, when installed,
by openpyxl. No QgsApplication."""

import importlib.util
import os
import shutil
import tempfile
import unittest
import zipfile

import helpers  # noqa: F401  sys.path bootstrap

from cadex.services import diminuare_service, outputs, xlsx, xlsx_report

HAS_OPENPYXL = importlib.util.find_spec("openpyxl") is not None


def load_with_openpyxl(path):
    # openpyxl 3.1.2, the version in QGIS 3.40, leaves a workbook it was given by name open
    import openpyxl
    with open(path, "rb") as fp:
        return openpyxl.load_workbook(fp)


class WriterTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_xlsx_")
        self.path = os.path.join(self.tmp, "raport.xlsx")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self):
        wb = xlsx_report.new_workbook()
        rows = [["10188", 2628.0, "POPESCU ION", None],
                ["0123", 1.5, "text cu <tag> & \"ghilimele\"", "rând 1\nrând 2"],
                ["x", -3, "  spații  ", "ș ț ă î â Ş Ţ"]]
        xlsx_report.write_table(wb, "Imobile / sector 1", ["id", "sup", "propr", "note"],
                                rows, row_colors=[xlsx_report.GOOD, None, None],
                                text_columns=["id"])
        summary = xlsx_report.Summary(wb, "Titlu")
        summary.section("Informații")
        summary.facts([("Data", "24.09.2026"), ("Fișiere", 3)])
        summary.table(["Categorie", "Nr."], [["A", 1200], ["B", 3.5]])
        summary.note("o notă")
        xlsx_report.save(wb, self.path)

    def test_round_trip_through_our_reader(self):
        self.build()
        self.assertEqual(xlsx.sheet_names(self.path), ["Sumar", "Imobile - sector 1"])
        _names, rows = xlsx.read_rows(self.path, "Imobile - sector 1")
        self.assertEqual(rows[0], ["id", "sup", "propr", "note"])
        self.assertEqual(rows[1], ["10188", 2628.0, "POPESCU ION", None])
        self.assertEqual(rows[2][0], "0123")  # stored as text, the leading zero stays
        self.assertEqual(rows[2][2], 'text cu <tag> & "ghilimele"')
        self.assertEqual(rows[2][3], "rând 1\nrând 2")
        self.assertEqual(rows[3][2], "  spații  ")
        self.assertEqual(rows[3][3], "ș ț ă î â Ş Ţ")

    @unittest.skipUnless(HAS_OPENPYXL, "openpyxl missing")
    def test_openpyxl_reads_structure_and_styles(self):
        self.build()
        wb = load_with_openpyxl(self.path)
        self.assertEqual(wb.sheetnames, ["Sumar", "Imobile - sector 1"])
        ws = wb["Imobile - sector 1"]
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertEqual(ws.auto_filter.ref, "A1:D4")
        header = ws["A1"]
        self.assertTrue(header.font.b)
        self.assertEqual(header.fill.fgColor.rgb, "FF" + xlsx_report.ACCENT)
        self.assertEqual(ws["A2"].fill.fgColor.rgb, "FF" + xlsx_report.GOOD)
        self.assertEqual(ws["A3"].number_format, "@")
        self.assertEqual(ws["B2"].value, 2628)
        summary = wb["Sumar"]
        self.assertIn("A1:D1", [str(r) for r in summary.merged_cells.ranges])
        self.assertFalse(summary.sheet_view.showGridLines)
        self.assertEqual(summary["B8"].value, 1200)
        self.assertEqual(summary["B8"].number_format, "#,##0")

    def test_percentages_are_numbers_and_text_numbers_read_romanian(self):
        wb = xlsx_report.new_workbook()
        summary = xlsx_report.Summary(wb, "Titlu")
        summary.table(["Situație", "Procent"], [["Intravilan", xlsx_report.percent(55.555, 1)]])
        # old and new values of one column can be text in one row and numbers in the next
        xlsx_report.write_table(wb, "Modificate", ["Ce", "Valoare"],
                                [["sup", 1155.0], ["sup", 1186.5], ["nr", "0123"]],
                                text_columns=["Valoare"])
        xlsx_report.save(wb, self.path)
        _names, rows = xlsx.read_rows(self.path, "Modificate")
        self.assertEqual([r[1] for r in rows[1:]], ["1155", "1186,5", "0123"])
        _names, rows = xlsx.read_rows(self.path, "Sumar")
        # a number Excel shows as 55,6% or 55.6% after the computer's settings
        self.assertAlmostEqual(rows[3][1], 0.55555)
        if HAS_OPENPYXL:
            self.assertEqual(load_with_openpyxl(self.path)["Sumar"]["B4"].number_format,
                             "0.0%")

    def test_package_parts(self):
        self.build()
        with zipfile.ZipFile(self.path) as zf:
            names = set(zf.namelist())
            workbook = zf.read("xl/workbook.xml").decode("utf-8")
        self.assertTrue({"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                         "xl/_rels/workbook.xml.rels", "xl/styles.xml",
                         "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"} <= names)
        # a filter without its hidden defined name makes Excel "repair" the file
        self.assertIn("_xlnm._FilterDatabase", workbook)
        self.assertIn("'Imobile - sector 1'!$A$1:$D$4", workbook)

    def test_sheet_names_are_made_legal_and_unique(self):
        wb = xlsx.Workbook()
        self.assertEqual(wb.add_sheet("a/b:c?").name, "a-b-c-")
        self.assertEqual(wb.add_sheet("x" * 40).name, "x" * 31)
        self.assertEqual(wb.add_sheet("A-B-C-").name, "A-B-C- (2)")

    def test_empty_workbook_still_opens(self):
        xlsx.Workbook().save(self.path)
        self.assertEqual(xlsx.sheet_names(self.path), ["Foaie"])

    def test_column_letters(self):
        for index, letters in ((1, "A"), (26, "Z"), (27, "AA"), (702, "ZZ"), (703, "AAA")):
            self.assertEqual(xlsx.column_letter(index), letters)
            self.assertEqual(xlsx.column_index(letters), index)


@unittest.skipUnless(HAS_OPENPYXL, "openpyxl missing to write the fixtures")
class ReaderTests(unittest.TestCase):
    """Files written by Excel-like producers: shared strings, formulas with cached values, gaps."""

    def setUp(self):
        import openpyxl
        self.tmp = tempfile.mkdtemp(prefix="cadex_read_")
        self.path = os.path.join(self.tmp, "parcele.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Parcele"
        ws.append(["Nr", "Suprafata act", "IE"])
        ws.append([1, 1500, None])
        ws.append([])
        ws.append([2, "1.234,5", "50124"])
        ws["D6"] = "departe"
        wb.create_sheet("Altceva").append(["x"])
        wb.save(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gaps_and_row_numbers(self):
        names, rows = xlsx.read_rows(self.path)
        self.assertEqual(names, ["Parcele", "Altceva"])
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[2], [None, None, None, None])
        self.assertEqual(rows[5][3], "departe")
        table = diminuare_service.read_sheet(self.path)
        self.assertEqual(table.sheet, "Parcele")
        self.assertEqual(table.header_number, 1)
        # the single value in column D gets its own column, with the generated header "Coloana 4"
        self.assertEqual(table.header, ["Nr", "Suprafata act", "IE", "Coloana 4"])
        # the blank row 3 is skipped but the numbers still match what Excel shows
        self.assertEqual(table.numbers, [2, 4, 6])
        self.assertEqual(table.rows[1], [2.0, "1.234,5", "50124", None])

    def test_other_sheet(self):
        _names, rows = xlsx.read_rows(self.path, "Altceva")
        self.assertEqual(rows, [["x"]])

    def test_old_xls_is_refused_clearly(self):
        fake = os.path.join(self.tmp, "vechi.xlsx")
        with open(fake, "wb") as fp:
            fp.write(b"\xd0\xcf\x11\xe0 not a zip")
        with self.assertRaises(zipfile.BadZipFile):
            xlsx.read_rows(fake)


class CsvTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_csv_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, data: bytes):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fp:
            fp.write(data)
        return path

    def test_romanian_excel_csv(self):
        # Excel with Romanian regional settings: ";" between columns, "," for decimals, code
        # page 1250, which only has the cedilla ţ
        path = self.write("ro.csv",
                          "Nr;Suprafaţă act;Proprietar\n1;1500,5;ŞTEFAN\n".encode("cp1250"))
        _names, rows = xlsx.read_rows(path)
        self.assertEqual(rows[0], ["Nr", "Suprafaţă act", "Proprietar"])
        self.assertEqual(rows[1], ["1", "1500,5", "ŞTEFAN"])
        self.assertEqual(diminuare_service.parse_number(rows[1][1]), 1500.5)

    def test_utf8_bom_comma_csv(self):
        path = self.write("en.csv", "﻿Nr,Area\n1,1500.5\n".encode("utf-8"))
        _names, rows = xlsx.read_rows(path)
        self.assertEqual(rows, [["Nr", "Area"], ["1", "1500.5"]])

    def test_empty_cells_are_none(self):
        path = self.write("gol.csv", b"a;b;c\n1;;3\n")
        self.assertEqual(xlsx.read_rows(path)[1][1], ["1", None, "3"])


def sheet_xml(path, number=1):
    with zipfile.ZipFile(path) as zf:
        return zf.read(f"xl/worksheets/sheet{number}.xml").decode("utf-8")


def cell_format(path, ref, number=1):
    """(number format code, wraps) of a cell, read from the package parts."""
    import re
    # parses only a file this test wrote
    import xml.etree.ElementTree as ET  # nosec B405
    ns = {"m": xlsx.MAIN_NS}
    match = re.search(rf'<c r="{ref}"(?: s="(\d+)")?', sheet_xml(path, number))
    with zipfile.ZipFile(path) as zf:
        styles = ET.fromstring(zf.read("xl/styles.xml"))  # nosec B314
    xf = styles.find("m:cellXfs", ns)[int(match.group(1) or 0)]
    codes = {int(f.get("numFmtId")): f.get("formatCode")
             for f in styles.iter(f"{{{xlsx.MAIN_NS}}}numFmt")}
    codes.update({number: code for code, number in xlsx.BUILTIN_FORMATS.items()})
    alignment = xf.find("m:alignment", ns)
    wraps = alignment is not None and alignment.get("wrapText") == "1"
    return codes.get(int(xf.get("numFmtId"))), wraps


def row_height(path, row, number=1):
    import re
    match = re.search(rf'<row r="{row}"(?: ht="([\d.]+)")?', sheet_xml(path, number))
    return float(match.group(1)) if match and match.group(1) else None


class ServicesWorkbookSaveTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_save_")
        self.path = os.path.join(self.tmp, "raport.xlsx")
        wb = xlsx.Workbook()
        wb.add_sheet("Raportul de ieri")
        wb.save(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_save_that_fails_midway_keeps_the_previous_workbook(self):
        wb = xlsx.Workbook()
        sheet = wb.add_sheet("Raportul de azi")
        sheet.set(1, 1, "x")

        def disk_full():
            raise OSError(28, "No space left on device")
        sheet.xml = disk_full
        with self.assertRaises(OSError) as raised:
            wb.save(self.path)
        # the message names the workbook the user chose, not a temporary file
        self.assertEqual(raised.exception.filename, self.path)
        self.assertEqual(xlsx.sheet_names(self.path), ["Raportul de ieri"])
        self.assertEqual(os.listdir(self.tmp), ["raport.xlsx"])

    def test_a_save_replaces_the_previous_workbook_and_leaves_no_other_file(self):
        wb = xlsx.Workbook()
        wb.add_sheet("Raportul de azi")
        wb.save(self.path)
        self.assertEqual(xlsx.sheet_names(self.path), ["Raportul de azi"])
        self.assertEqual(os.listdir(self.tmp), ["raport.xlsx"])

    @unittest.skipUnless(os.name == "nt", "Excel's way of holding a file is a Windows lock")
    def test_a_workbook_open_in_excel_is_refused_by_name_and_kept(self):
        from test_pure_services import hold_like_excel
        release = hold_like_excel(self.path)
        try:
            wb = xlsx.Workbook()
            wb.add_sheet("Raportul de azi")
            with self.assertRaises(PermissionError) as raised:
                wb.save(self.path)
        finally:
            release()
        self.assertEqual(raised.exception.filename, self.path)
        self.assertEqual(xlsx.sheet_names(self.path), ["Raportul de ieri"])
        self.assertEqual(os.listdir(self.tmp), ["raport.xlsx"])

    def test_a_missing_folder_is_reported_for_the_workbook(self):
        path = os.path.join(self.tmp, "lipsa", "raport.xlsx")
        with self.assertRaises(FileNotFoundError) as raised:
            xlsx.Workbook().save(path)
        self.assertEqual(raised.exception.filename, path)


class ServicesReaderTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_hidden_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rows_hidden_in_excel_are_reported_by_number(self):
        path = os.path.join(self.tmp, "parcele.xlsx")
        wb = xlsx.Workbook()
        sheet = wb.add_sheet("Parcele")
        for row, values in enumerate([["Nr", "Tarla"], [1, 12], [2, 15], [3, 12]], start=1):
            for col, value in enumerate(values, start=1):
                sheet.set(row, col, value)
        wb.save(path)
        # a filter on tarla 12 hides row 3, the way Excel saves it
        with zipfile.ZipFile(path) as zf:
            parts = {name: zf.read(name) for name in zf.namelist()}
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b'<row r="3"', b'<row r="3" hidden="1"')
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in parts.items():
                zf.writestr(name, data)
        names, rows, hidden = xlsx.read_table(path)
        self.assertEqual(names, ["Parcele"])
        # every row is still read, so row numbers match Excel's
        self.assertEqual([r[0] for r in rows], ["Nr", 1.0, 2.0, 3.0])
        self.assertEqual(hidden, [3])
        self.assertEqual(xlsx.read_rows(path), (names, rows))

    def test_a_csv_has_no_hidden_rows(self):
        path = os.path.join(self.tmp, "parcele.csv")
        with open(path, "wb") as fp:
            fp.write(b"Nr;Tarla\n1;12\n")
        self.assertEqual(xlsx.read_table(path), (["CSV"], [["Nr", "Tarla"], ["1", "12"]], []))

    def test_a_romanian_csv_with_a_comma_in_its_header_keeps_its_columns(self):
        path = os.path.join(self.tmp, "parcele.csv")
        with open(path, "wb") as fp:
            fp.write("Nr;Suprafata, mp;IE\n1;1001,5;\n2;1002,5;50124\n".encode("cp1250"))
        _names, rows = xlsx.read_rows(path)
        self.assertEqual(rows, [["Nr", "Suprafata, mp", "IE"], ["1", "1001,5", None],
                                ["2", "1002,5", "50124"]])


class ServicesSummaryTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_sumar_")
        self.path = os.path.join(self.tmp, "raport.xlsx")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_long_facts_and_notes_are_shown_whole(self):
        columns = ", ".join(f"Coloana numărul {i}" for i in range(1, 14))
        note = " ".join(["Imobilele fără coordonate în exportul eTerra nu se compară."] * 9)
        wb = xlsx_report.new_workbook()
        summary = xlsx_report.Summary(wb, "Comparator")
        summary.facts([("Data", "24.09.2026"), ("Coloane comparate", columns)])
        summary.note(note)
        xlsx_report.save(wb, self.path)
        self.assertGreater(len(columns), 240)
        self.assertGreater(len(note), 500)
        # facts at rows 3 and 4, the note at row 6
        self.assertTrue(cell_format(self.path, "B4")[1])
        self.assertIsNone(row_height(self.path, 3))
        self.assertGreaterEqual(row_height(self.path, 4), 3 * 15)
        self.assertGreaterEqual(row_height(self.path, 6), 3 * 12)

    def test_the_summary_prints_one_page_wide(self):
        wb = xlsx_report.new_workbook()
        xlsx_report.Summary(wb, "Titlu").note("o notă")
        xlsx_report.write_table(wb, "Date", ["a"], [[1]])
        xlsx_report.save(wb, self.path)
        summary = sheet_xml(self.path, 1)
        # the schema orders sheetPr first and pageSetup right after pageMargins
        self.assertRegex(summary, r'<worksheet [^>]*><sheetPr><pageSetUpPr fitToPage="1"/>')
        self.assertRegex(summary, r'<pageMargins [^>]*/><pageSetup paperSize="9" '
                                  r'orientation="portrait" fitToWidth="1" fitToHeight="0"/>')
        self.assertNotIn("pageSetup", sheet_xml(self.path, 2))
        if HAS_OPENPYXL:
            ws = load_with_openpyxl(self.path)["Sumar"]
            self.assertTrue(ws.sheet_properties.pageSetUpPr.fitToPage)
            self.assertEqual((ws.page_setup.fitToWidth, ws.page_setup.fitToHeight), (1, 0))


class ServicesCellValueTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_dates_")
        self.path = os.path.join(self.tmp, "raport.xlsx")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dates_are_excel_dates_that_sort_by_time(self):
        from datetime import date, datetime
        wb = xlsx_report.new_workbook()
        xlsx_report.write_table(wb, "Date", ["Data", "Măsurat"], [
            [date(2025, 3, 1), datetime(2024, 1, 2, 14, 30)],
            [date(2024, 1, 2), None]])
        xlsx_report.save(wb, self.path)
        # stored as day numbers: 2 January 2024 is 45293 and comes before 1 March 2025
        stored = sheet_xml(self.path)
        self.assertRegex(stored, r'<c r="A2"[^>]*><v>45717</v>')
        self.assertRegex(stored, r'<c r="A3"[^>]*><v>45293</v>')
        _names, rows = xlsx.read_rows(self.path)
        self.assertEqual([r[0] for r in rows[1:]], [date(2025, 3, 1), date(2024, 1, 2)])
        self.assertEqual(rows[1][1], datetime(2024, 1, 2, 14, 30))
        self.assertEqual(cell_format(self.path, "A2")[0], "dd.mm.yyyy")
        self.assertEqual(cell_format(self.path, "B2")[0], "dd.mm.yyyy hh:mm")
        if HAS_OPENPYXL:
            ws = load_with_openpyxl(self.path)["Date"]
            self.assertEqual(ws["A3"].value, datetime(2024, 1, 2))

    def test_times_are_written_as_times_and_qt_dates_as_dates(self):
        from datetime import time
        self.assertEqual(xlsx_report.cell_value(time(14, 30, 5)), "14:30:05")
        try:
            from qgis.PyQt.QtCore import QDate, QTime  # type: ignore
        except ImportError:
            self.skipTest("Qt types need QGIS's Python")
        self.assertEqual(xlsx_report.cell_value(QTime(14, 30, 5)), "14:30:05")
        self.assertEqual(xlsx_report.cell_value(QDate(2024, 1, 2)).value, 45293)


def well_formed(path):
    """Parse every part of the workbook at path, as Excel does before it shows anything."""
    # parses only a file this test wrote
    import xml.etree.ElementTree as ET  # nosec B405
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            ET.fromstring(zf.read(name))  # nosec B314


class WriterContentTests(unittest.TestCase):
    """Cell texts, sheet names and sizes that an .xlsx file cannot hold as they are."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_content_")
        self.path = os.path.join(self.tmp, "raport.xlsx")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_characters_a_workbook_cannot_hold_become_spaces(self):
        wb = xlsx_report.new_workbook()
        xlsx_report.write_table(wb, "Date", ["proprietar\ufffe", "nota"], [
            ["POPESCU\uffffION", "a\ufffeb"], ["x\x00y\x1fz", "tab\tși\nrând"],
            ["jumătate\ud83d de pereche", "\U0001F600"]])
        xlsx_report.Summary(wb, "Titlu\uffff").facts([("fapt\ufffe", "valoare\uffff")])
        xlsx_report.save(wb, self.path)
        well_formed(self.path)
        _names, rows = xlsx.read_rows(self.path, "Date")
        self.assertEqual(rows, [["proprietar ", "nota"], ["POPESCU ION", "a b"],
                                ["x y z", "tab\tși\nrând"],
                                ["jumătate  de pereche", "\U0001F600"]])

    def test_a_sheet_name_keeps_only_what_excel_accepts(self):
        wb = xlsx.Workbook()
        self.assertEqual(wb.add_sheet("ctl\x01name").name, "ctl name")
        self.assertEqual(wb.add_sheet("a\ufffeb").name, "a b")
        self.assertEqual(wb.add_sheet("tab\tși\nrând").name, "tab și rând")
        wb.save(self.path)
        well_formed(self.path)
        self.assertEqual(xlsx.sheet_names(self.path), ["ctl name", "a b", "tab și rând"])

    def test_a_sheet_name_is_cut_to_the_31_units_excel_counts(self):
        wb = xlsx.Workbook()
        # a character outside the basic plane counts as two
        self.assertEqual(wb.add_sheet("\U0001F600" * 31).name, "\U0001F600" * 15)
        self.assertEqual(wb.add_sheet("x" * 30 + "\U0001F600").name, "x" * 30)
        self.assertEqual(wb.add_sheet("\U0001F600" * 31).name, "\U0001F600" * 13 + " (2)")
        self.assertEqual(wb.add_sheet("ș" * 40).name, "ș" * 31)

    def test_cells_cut_to_the_excel_limit_are_counted(self):
        wb = xlsx_report.new_workbook()
        self.assertEqual(wb.cut_cells, 0)
        xlsx_report.write_table(wb, "Date", ["a", "b"], [
            ["x" * 32767, "y" * 32768], ["scurt", "\U0001F600" * 20000]])
        self.assertEqual(wb.cut_cells, 2)
        xlsx_report.save(wb, self.path)
        _names, rows = xlsx.read_rows(self.path, "Date")
        self.assertEqual([len(rows[1][0]), len(rows[1][1])], [32767, 32767])
        # 32,767 units hold 16,383 characters of two units each
        self.assertEqual(rows[2], ["scurt", "\U0001F600" * 16383])

    def test_a_sheet_with_more_rows_than_excel_holds_is_refused_with_its_size(self):
        wb = xlsx.Workbook()
        sheet = wb.add_sheet("Toate")
        sheet.set(1, 1, "sus")
        sheet.set(1048576, 1, "ultimul rând")
        wb.save(self.path)
        sheet.set(1048577, 1, "peste limită")
        other = os.path.join(self.tmp, "mare.xlsx")
        with self.assertRaises(OSError) as raised:
            wb.save(other)
        self.assertEqual(outputs.write_error(other, raised.exception), (
            "Fișierul 'mare.xlsx' nu poate fi scris: foaia 'Toate' are 1.048.577 de rânduri, "
            "iar o foaie Excel cuprinde cel mult 1.048.576."))
        self.assertEqual(os.listdir(self.tmp), ["raport.xlsx"])

    def test_a_sheet_with_more_columns_than_excel_holds_is_refused_with_its_size(self):
        wb = xlsx.Workbook()
        sheet = wb.add_sheet("Lată")
        sheet.set(1, 16385, "peste limită")
        with self.assertRaises(OSError) as raised:
            wb.save(self.path)
        self.assertEqual(outputs.write_error(self.path, raised.exception), (
            "Fișierul 'raport.xlsx' nu poate fi scris: foaia 'Lată' are 16.385 de coloane, "
            "iar o foaie Excel cuprinde cel mult 16.384."))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_long_whole_numbers_show_every_digit(self):
        wb = xlsx_report.new_workbook()
        xlsx_report.write_table(wb, "Date", ["id"], [
            [12345678901], [123456789012], [123456789012345], [123456789012.0],
            [1234567890123456], [-12345678901234567890], [1234.5]])
        xlsx_report.save(wb, self.path)
        _names, rows = xlsx.read_rows(self.path, "Date")
        # Excel keeps 15 digits of a number, so a longer one is written as text
        self.assertEqual([r[0] for r in rows[1:]], [
            12345678901.0, 123456789012.0, 123456789012345.0, 123456789012.0,
            "1234567890123456", "-12345678901234567890", 1234.5])
        # the General format shows 12 digits or more as 1.23457E+11
        self.assertEqual([cell_format(self.path, f"A{row}")[0] for row in range(2, 9)],
                         ["General", "0", "0", "0", "General", "General", "General"])

    def test_a_repeated_cell_style_is_one_style(self):
        wb = xlsx_report.new_workbook()
        xlsx_report.write_table(wb, "Date", ["a", "b"], [[i, str(i)] for i in range(500)])
        xlsx_report.save(wb, self.path)
        with zipfile.ZipFile(self.path) as zf:
            styles = zf.read("xl/styles.xml").decode("utf-8")
        # the unstyled cell, the header, and a plain and a striped body row
        self.assertIn('<cellXfs count="4">', styles)


MAIN = xlsx.MAIN_NS
REL = xlsx.REL_NS
PKG = xlsx.PKG_REL_NS
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
WORKSHEET, CHARTSHEET = "worksheet", "chartsheet"


def write_package(path, sheets, shared="", styles="", date1904=False):
    """A workbook written part by part, laid out as Excel saves one. sheets holds, in tab
    order, (name, part path under xl/, the part's XML below its root, state or None, kind)."""
    types = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
             'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" '
             f'ContentType="{xlsx.SPREADSHEET_TYPE}.sheet.main+xml"/>'
             + "".join(f'<Override PartName="/xl/{part}" ContentType="{xlsx.SPREADSHEET_TYPE}.'
                       f'{kind}+xml"/>' for _name, part, _body, _state, kind in sheets)
             + "</Types>")
    tabs, relationships = [], []
    for number, (name, part, _body, state, kind) in enumerate(sheets, start=1):
        # relationship ids in an order of their own, as after sheets were moved in Excel
        rid = f"rId{len(sheets) + 5 - number}"
        hidden = f' state="{state}"' if state else ""
        tabs.append(f'<sheet name="{name}" sheetId="{number}"{hidden} r:id="{rid}"/>')
        relationships.append(f'<Relationship Id="{rid}" Type="{REL}/{kind}" Target="{part}"/>')
    relationships.append(f'<Relationship Id="rId90" Type="{REL}/styles" Target="styles.xml"/>')
    relationships.append(f'<Relationship Id="rId91" Type="{REL}/sharedStrings" '
                         'Target="sharedStrings.xml"/>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", HEAD + types)
        zf.writestr("_rels/.rels", (
            f'{HEAD}<Relationships xmlns="{PKG}"><Relationship Id="rId1" '
            f'Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'))
        zf.writestr("xl/workbook.xml", (
            f'{HEAD}<workbook xmlns="{MAIN}" xmlns:r="{REL}">'
            + ('<workbookPr date1904="1"/>' if date1904 else "<workbookPr/>")
            + '<bookViews><workbookView activeTab="1"/></bookViews>'
            f'<sheets>{"".join(tabs)}</sheets></workbook>'))
        zf.writestr("xl/_rels/workbook.xml.rels", (
            f'{HEAD}<Relationships xmlns="{PKG}">{"".join(relationships)}</Relationships>'))
        zf.writestr("xl/styles.xml", f'{HEAD}<styleSheet xmlns="{MAIN}">{styles}</styleSheet>')
        zf.writestr("xl/sharedStrings.xml", f'{HEAD}<sst xmlns="{MAIN}">{shared}</sst>')
        for _name, part, body, _state, kind in sheets:
            root = "worksheet" if kind == WORKSHEET else "chartsheet"
            zf.writestr(f"xl/{part}", f'{HEAD}<{root} xmlns="{MAIN}">{body}</{root}>')


# cell styles 0 to 8: General, a built-in date, a date with time, a time, two percentages,
# square metres with their unit, text, a date written out in Romanian
STYLES = (
    '<numFmts count="4">'
    '<numFmt numFmtId="164" formatCode="dd/mm/yyyy\\ hh:mm"/>'
    '<numFmt numFmtId="165" formatCode="0.0%"/>'
    '<numFmt numFmtId="166" formatCode="#,##0.00\\ &quot;mp&quot;"/>'
    '<numFmt numFmtId="167" formatCode="[$-418]d\\ mmmm\\ yyyy;@"/>'
    '</numFmts>'
    '<cellXfs count="9">'
    '<xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/><xf numFmtId="20"/>'
    '<xf numFmtId="9"/><xf numFmtId="165"/><xf numFmtId="166"/><xf numFmtId="49"/>'
    '<xf numFmtId="167"/>'
    '</cellXfs>')

# shared strings 0 to 8
SHARED = (
    "<si><t>Nr</t></si><si><t>Proprietar</t></si><si><t>Suprafata act</t></si>"
    "<si><t>Data act</t></si><si><t>Cota</t></si><si><t>Obs</t></si>"
    # a name with its second word in bold, as Excel stores text with several formats
    '<si><r><t xml:space="preserve">POPESCU </t></r><r><rPr><b/></rPr><t>ION</t></r></si>'
    # a reading guide for the text, which is not part of it
    '<si><t>IONESCU MARIA</t><rPh sb="0" eb="7"><t>GHID</t></rPh></si>'
    '<si><t xml:space="preserve"> titlu vechi\nnr. 123 </t></si>')

PARCELE = (
    "<sheetData>"
    '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
    '<c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c><c r="E1" t="s"><v>4</v></c>'
    '<c r="F1" t="s"><v>5</v></c></row>'
    '<row r="2"><c r="A2"><v>1</v></c><c r="B2" t="s"><v>6</v></c>'
    '<c r="C2" s="6"><v>1000.5</v></c><c r="D2" s="1"><v>45300</v></c>'
    '<c r="E2" s="4"><v>0.25</v></c><c r="F2" t="s"><v>8</v></c></row>'
    '<row r="3"><c r="A3"><v>2</v></c><c r="B3" t="s"><v>7</v></c>'
    '<c r="C3" s="6"><f>C2*2</f><v>2001</v></c><c r="D3" s="2"><v>45300.604166666664</v></c>'
    '<c r="E3" s="5"><v>0.125</v></c><c r="F3" t="str"><f>B2&amp;" / "&amp;A2</f>'
    "<v>POPESCU ION / 1</v></c></row>"
    '<row r="4"><c r="A4"><v>3</v></c><c r="B4" t="inlineStr"><is><t>DINU ANA</t></is></c>'
    '<c r="C4" t="e"><f>1/0</f><v>#DIV/0!</v></c><c r="D4" s="8"><v>45301</v></c>'
    '<c r="E4" s="3"><v>0.604166666666667</v></c><c r="F4" t="b"><v>1</v></c></row>'
    "</sheetData>")

ONE_CELL = '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{0}</t></is></c></row></sheetData>'


class ExcelWorkbookTests(unittest.TestCase):
    """A workbook laid out as Excel saves one: texts in the shared strings part, formulas with
    their last values, dates as formatted numbers, a hidden first sheet, and sheets stored in
    another order than their tabs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_excel_")
        self.path = os.path.join(self.tmp, "parcele.xlsx")
        write_package(self.path, [
            ("Config", "worksheets/sheet2.xml", ONE_CELL.format("lista"), "hidden", WORKSHEET),
            ("Parcele", "worksheets/sheet3.xml", PARCELE, None, WORKSHEET),
            ("Arhiva", "worksheets/sheet1.xml", ONE_CELL.format("vechi"), None, WORKSHEET),
        ], SHARED, STYLES)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sheet=None):
        return xlsx.read_rows(self.path, sheet)[1]

    def test_the_first_sheet_shown_in_excel_is_read_when_none_is_named(self):
        self.assertEqual(xlsx.sheet_names(self.path), ["Config", "Parcele", "Arhiva"])
        self.assertEqual(xlsx.default_sheet(self.path), "Parcele")
        self.assertEqual(self.rows()[0],
                         ["Nr", "Proprietar", "Suprafata act", "Data act", "Cota", "Obs"])
        table = diminuare_service.read_sheet(self.path)
        self.assertEqual((table.sheet, table.header[:2], len(table.rows)),
                         ("Parcele", ["Nr", "Proprietar"], 3))

    def test_a_hidden_sheet_is_read_when_it_is_named(self):
        self.assertEqual(self.rows("Config"), [["lista"]])
        self.assertEqual(diminuare_service.read_sheet(self.path, "Config").sheet, "Config")

    def test_each_tab_is_read_from_its_own_part(self):
        self.assertEqual(self.rows("Arhiva"), [["vechi"]])
        self.assertEqual(self.rows("Parcele")[1][0], 1.0)

    def test_text_is_read_whole_whatever_its_formats(self):
        rows = self.rows()
        self.assertEqual([r[1] for r in rows[1:]], ["POPESCU ION", "IONESCU MARIA", "DINU ANA"])
        self.assertEqual(rows[1][5], " titlu vechi\nnr. 123 ")

    def test_a_formula_gives_its_last_value(self):
        rows = self.rows()
        self.assertEqual(rows[2][2], 2001.0)
        self.assertEqual(rows[2][5], "POPESCU ION / 1")
        self.assertEqual(rows[3][2], "#DIV/0!")
        self.assertIs(rows[3][5], True)

    def test_numbers_shown_as_dates_are_dates(self):
        from datetime import date, datetime, time
        rows = self.rows()
        self.assertEqual(rows[1][3], date(2024, 1, 9))
        self.assertEqual(rows[2][3], datetime(2024, 1, 9, 14, 30))
        self.assertEqual(rows[3][3], date(2024, 1, 10))
        self.assertEqual(rows[3][4], time(14, 30))
        # "mp" in the number format is not a date code, so the value stays a number
        self.assertEqual(rows[1][2], 1000.5)
        self.assertIs(type(rows[1][2]), float)

    def test_dates_follow_the_date_system_of_the_workbook(self):
        from datetime import date
        write_package(self.path, [("Parcele", "worksheets/sheet1.xml", PARCELE, None, WORKSHEET)],
                      SHARED, STYLES, date1904=True)
        # a workbook begun on a Mac counts its days from 1904
        self.assertEqual(self.rows()[1][3], date(2028, 1, 10))

    def test_the_first_days_of_1900_are_read_as_excel_shows_them(self):
        from datetime import date
        body = ('<sheetData><row r="1"><c r="A1" s="1"><v>1</v></c><c r="B1" s="1"><v>59</v></c>'
                '<c r="C1" s="1"><v>61</v></c><c r="D1" s="1"><v>-5</v></c>'
                '<c r="E1" s="1"><v>99999999</v></c></row></sheetData>')
        write_package(self.path, [("Date", "worksheets/sheet1.xml", body, None, WORKSHEET)],
                      SHARED, STYLES)
        # a number no date stands for stays a number
        self.assertEqual(self.rows()[0], [date(1900, 1, 1), date(1900, 2, 28), date(1900, 3, 1),
                                          -5.0, 99999999.0])

    def test_percentages_keep_their_format(self):
        rows = self.rows()
        self.assertEqual((rows[1][4], rows[2][4]), (0.25, 0.125))
        self.assertEqual((rows[1][4].number_format, rows[2][4].number_format), ("0%", "0.0%"))

    def test_a_chart_sheet_is_passed_over_when_no_sheet_is_named(self):
        chart = '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        write_package(self.path, [
            ("Grafic", "chartsheets/sheet1.xml", chart, None, CHARTSHEET),
            ("Parcele", "worksheets/sheet1.xml", PARCELE, None, WORKSHEET)], SHARED, STYLES)
        self.assertEqual(xlsx.default_sheet(self.path), "Parcele")
        self.assertEqual(self.rows()[1][1], "POPESCU ION")

    def test_a_cell_far_to_the_right_that_holds_only_a_format_adds_no_columns(self):
        body = ('<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>2</v></c>'
                '<c r="XFD1" s="1"/></row>'
                '<row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>1000</v></c><c r="C2" s="4"/>'
                '<c r="D2" t="s"/><c r="E2" t="inlineStr"><is><t></t></is></c></row>'
                "</sheetData>")
        write_package(self.path, [("Parcele", "worksheets/sheet1.xml", body, None, WORKSHEET)],
                      SHARED, STYLES)
        self.assertEqual(self.rows(), [["Nr", "Suprafata act"], [1.0, 1000.0]])
        self.assertEqual(diminuare_service.read_sheet(self.path).header,
                         ["Nr", "Suprafata act"])

    def test_rows_far_below_that_hold_only_a_format_add_no_rows(self):
        body = ('<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>2</v></c>'
                '</row><row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>1000</v></c></row>'
                '<row r="3" s="1" customFormat="1"/>'
                '<row r="4" hidden="1"><c r="A4" s="1"/></row>'
                '<row r="5000"><c r="A5000" s="1"/></row></sheetData>')
        write_package(self.path, [("Parcele", "worksheets/sheet1.xml", body, None, WORKSHEET)],
                      SHARED, STYLES)
        _names, rows, hidden = xlsx.read_table(self.path)
        self.assertEqual(rows, [["Nr", "Suprafata act"], [1.0, 1000.0]])
        self.assertEqual(hidden, [4])
        table = diminuare_service.read_sheet(self.path)
        self.assertEqual((table.rows, table.numbers, table.hidden), ([[1.0, 1000.0]], [2], []))


class ParcelWorkbookTests(unittest.TestCase):
    """The table Diminuare Suprafețe saves, for a parcel table read from an Excel workbook."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_parcels_")
        self.source = os.path.join(self.tmp, "parcele.xlsx")
        self.path = os.path.join(self.tmp, "parcele_diminuat.xlsx")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dates_and_percentages_of_the_table_are_saved_as_such(self):
        from datetime import date, datetime
        write_package(self.source, [
            ("Parcele", "worksheets/sheet1.xml", PARCELE, None, WORKSHEET)], SHARED, STYLES)
        table = diminuare_service.read_sheet(self.source)
        areas = diminuare_service.parse_column([row[2] for row in table.rows])
        self.assertEqual(areas, [1000.5, 2001.0, None])
        result = diminuare_service.compute(areas, [False] * 3, 2700, 0)
        diminuare_service.write_excel(self.path, table.header, table.rows, result, [], 2, None)
        _names, rows = xlsx.read_rows(self.path, "Diminuare")
        self.assertEqual([row[3] for row in rows[1:]],
                         [date(2024, 1, 9), datetime(2024, 1, 9, 14, 30), date(2024, 1, 10)])
        # the Diminuare sheet is the second, after Sumar
        self.assertEqual(cell_format(self.path, "D2", 2)[0], "dd.mm.yyyy")
        self.assertEqual((rows[1][4], rows[1][4].number_format), (0.25, "0%"))
        self.assertEqual((rows[2][4], rows[2][4].number_format), (0.125, "0.0%"))
        self.assertEqual(rows[3][4], "14:30:00")

    def test_a_text_longer_than_a_cell_holds_is_counted(self):
        result = diminuare_service.compute([1000.0, 2000.0], [False, False], 2500, 0)
        cut = diminuare_service.write_excel(
            self.path, ["Nr", "Suprafata act", "Obs"],
            [[1.0, 1000.0, "x" * 40000], [2.0, 2000.0, "scurt"]], result, [], 1, None)
        self.assertEqual(cut, 1)
        _names, rows = xlsx.read_rows(self.path, "Diminuare")
        self.assertEqual(len(rows[1][2]), 32767)


class CsvLayoutTests(unittest.TestCase):
    """Parcel tables as text files, as Excel and other programs write them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_csvlayout_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, data):
        path = os.path.join(self.tmp, "parcele.csv")
        with open(path, "wb") as fp:
            fp.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return xlsx.read_rows(path)[1]

    def test_rows_may_end_before_an_empty_last_column(self):
        text = "Nr;Suprafata act;IE\r\n" + "".join(
            f"{i};{1000 + i},5" + (";50124" if i % 9 == 0 else "") + "\r\n" for i in range(1, 30))
        rows = self.rows(text)
        self.assertEqual(rows[0], ["Nr", "Suprafata act", "IE"])
        self.assertEqual(rows[1], ["1", "1001,5"])
        self.assertEqual(rows[9], ["9", "1009,5", "50124"])

    def test_a_title_with_a_comma_above_the_table_does_not_split_the_areas(self):
        text = ("Tabel parcele, tarla 12\r\nUAT, sector 3\r\nNr;Suprafata act;IE\r\n"
                + "".join(f"{i};{1000 + i},5;\r\n" for i in range(1, 30)))
        rows = self.rows(text)
        self.assertEqual(rows[2], ["Nr", "Suprafata act", "IE"])
        self.assertEqual(rows[3], ["1", "1001,5", None])
        table = diminuare_service.read_sheet(os.path.join(self.tmp, "parcele.csv"))
        self.assertEqual((table.header, table.header_number),
                         (["Nr", "Suprafata act", "IE"], 3))

    def test_a_single_column_of_areas_with_decimal_commas_stays_one_column(self):
        self.assertEqual(self.rows("Suprafata act\r\n1000,5\r\n2000,25\r\n"),
                         [["Suprafata act"], ["1000,5"], ["2000,25"]])

    def test_tabs_between_columns_and_decimal_commas(self):
        text = "Nr\tSuprafata act\tIE\r\n" + "".join(
            f"{i}\t{1000 + i},5\t\r\n" for i in range(1, 10))
        self.assertEqual(self.rows(text)[1], ["1", "1001,5", None])

    def test_commas_between_columns_and_quoted_decimal_commas(self):
        # Google Sheets with Romanian settings
        text = "Nr,Suprafata act,IE\r\n" + "".join(
            f'{i},"{1000 + i},5",\r\n' for i in range(1, 10))
        self.assertEqual(self.rows(text)[1], ["1", "1001,5", None])

    def test_commas_between_columns_of_whole_numbers(self):
        self.assertEqual(self.rows("nr,suprafata_act\n1,4000\n2,6000\n"),
                         [["nr", "suprafata_act"], ["1", "4000"], ["2", "6000"]])

    def test_a_quoted_cell_keeps_its_separator_and_its_line_break(self):
        text = ('Nr;Proprietar;Suprafata act\r\n1;"POPESCU; ION";1000,5\r\n'
                '2;"IONESCU\r\nMARIA";2000,25\r\n3;"zis ""Nelu""";3000\r\n')
        self.assertEqual(self.rows(text)[1:], [
            ["1", "POPESCU; ION", "1000,5"], ["2", "IONESCU\r\nMARIA", "2000,25"],
            ["3", 'zis "Nelu"', "3000"]])

    def test_semicolons_in_the_text_of_a_comma_separated_table(self):
        text = "Nr,Proprietar,Area\n" + "".join(
            f"{i},Popescu; Ion,{1000 + i}.5\n" for i in range(1, 10))
        self.assertEqual(self.rows(text)[1], ["1", "Popescu; Ion", "1001.5"])

    def test_lines_ended_by_a_carriage_return_alone(self):
        self.assertEqual(self.rows(b"Nr;Suprafata act;IE\r1;1000,5;\r2;2000,5;\r"),
                         [["Nr", "Suprafata act", "IE"], ["1", "1000,5", None],
                          ["2", "2000,5", None]])

    def test_text_saved_as_utf16(self):
        # Excel's "Unicode Text": tabs between columns, two bytes per letter
        text = "Nr\tSuprafață act\tProprietar\r\n1\t1500,5\tȘTEFAN ȚÎRLEA\r\n"
        for encoding in ("utf-16", "utf-16-be"):
            data = text.encode(encoding)
            if encoding == "utf-16-be":
                data = b"\xfe\xff" + data
            self.assertEqual(self.rows(data), [["Nr", "Suprafață act", "Proprietar"],
                                               ["1", "1500,5", "ȘTEFAN ȚÎRLEA"]], encoding)

    def test_a_file_without_text_has_no_rows(self):
        self.assertEqual(self.rows(b""), [])

    def test_commas_in_the_text_of_a_semicolon_separated_table_without_header(self):
        # each line holds more commas than semicolons, the same number on every line
        text = "".join(f"{i};Str. Mare, nr. {i}, bl. A, sc. B;{1000 + i},5\r\n"
                       for i in range(1, 10))
        self.assertEqual(self.rows(text)[0], ["1", "Str. Mare, nr. 1, bl. A, sc. B", "1001,5"])


if __name__ == "__main__":
    unittest.main()
