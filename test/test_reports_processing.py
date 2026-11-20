# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Diminuare Suprafețe and Raport Intravilan / Extravilan through processing.run, and the
Raport Intravilan task on hand-built layers with known areas."""

import math
import os
import tempfile
import time
import unittest
from collections import Counter
from random import Random

import helpers

helpers.start_qgis(gui=False)
helpers.start_processing()

from qgis.core import QgsProcessingException, QgsProject  # noqa: E402  # type: ignore
import processing  # noqa: E402  # type: ignore

from cadex.qt_compat import is_null  # noqa: E402
from cadex.services import intravilan_service, overlay, xlsx  # noqa: E402
from cadex.services.layer_sources import LayerSnapshot  # noqa: E402

ORIGIN = helpers.ORIGIN
square = helpers.square_in_romania


class ProcessingTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_reports_alg_")
        self.said = helpers.feedback()

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    def run_algorithm(self, name, parameters):
        return processing.run(f"cadex:{name}", parameters, feedback=self.said)

    def csv(self, text, name="parcele.csv"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(text)
        return path

    def reduce(self, table, contour, **more):
        parameters = {"TABLE": table, "AREA_FIELD": "suprafata_act", "CONTOUR_AREA": contour,
                      "OUTPUT": "TEMPORARY_OUTPUT"}
        parameters.update(more)
        return self.run_algorithm("areareduction", parameters)

    summary = staticmethod(helpers.summary_facts)


class DiminuareTotalRowProcessingTests(ProcessingTest):

    def test_a_parcel_with_the_word_total_in_its_owner_or_remark_is_reduced(self):
        result = self.reduce(self.csv(
            "nr,proprietar,suprafata_act,obs\n1,POPESCU ION,5000,\n2,SC TOTAL AGRO SRL,20000,\n"
            "3,IONESCU MARIA,5000,\n4,DINU ANA,10000,din total 5000 mp arabil\n"), 36000)
        self.assertAlmostEqual(result["FACTOR"], 0.9)
        areas = {f["nr"]: f["sup_diminuata"] for f in result["OUTPUT"].getFeatures()}
        self.assertEqual(areas, {"1": 4500, "2": 18000, "3": 4500, "4": 9000})
        self.assertEqual([m for m in self.said.warnings if "rând de total" in m], [])
        # the two rows are named as warnings: a total row taken for a parcel doubles the sum
        kept = [m for m in self.said.warnings if "rămâne parcelă" in m]
        self.assertEqual(len(kept), 2, self.said.warnings)
        self.assertIn("Elementul cu fid 2 conține cuvântul 'total' sau 'sumă'", kept[0])
        self.assertEqual([m for m in self.said.infos if "rămâne parcelă" in m], [])

    def test_a_row_named_total_is_left_out_whatever_its_area(self):
        # the total also counts a parcel that the table does not hold
        result = self.reduce(self.csv(
            "proprietar,suprafata_act\nPOPESCU ION,1000\nIONESCU MARIA,1500\nTOTAL,3200\n"), 2250)
        self.assertAlmostEqual(result["FACTOR"], 0.9)
        areas = {f["proprietar"]: f["sup_diminuata"] for f in result["OUTPUT"].getFeatures()}
        self.assertEqual((areas["POPESCU ION"], areas["IONESCU MARIA"]), (900, 1350))
        self.assertTrue(is_null(areas["TOTAL"]))
        self.assertEqual([m for m in self.said.warnings if "total" in m], [
            "Elementul cu fid 3 este un rând de total sau subtotal și nu intră în diminuare."])

    def test_total_rows_are_left_out_with_the_reason_of_each(self):
        excel = os.path.join(self.tmp, "diminuare.xlsx")
        result = self.reduce(self.csv(
            "nr,proprietar,suprafata_act\n1,A,10000\n2,B,20000\n3,C,15000\n"
            "Total conform acte,,45000\n4,D,700\nSubtotal parcele noi,,800\n"
            "TOTAL GENERAL,,46500\n"), 43830, OUTPUT_XLSX=excel)
        areas = {f["nr"]: f["sup_diminuata"] for f in result["OUTPUT"].getFeatures()}
        self.assertTrue(is_null(areas["Total conform acte"]))
        self.assertTrue(is_null(areas["Subtotal parcele noi"]))
        self.assertTrue(is_null(areas["TOTAL GENERAL"]))
        self.assertEqual(areas["4"], 671)
        about_totals = [m for m in self.said.warnings if "rând de total" in m]
        self.assertEqual(len(about_totals), 3, self.said.warnings)
        self.assertIn("fid 4 este un rând de total sau subtotal, cu suprafața egală cu suma "
                      "rândurilor de deasupra", about_totals[0])
        self.assertIn("fid 6 este un rând de total sau subtotal, doar cu denumirea și numere",
                      about_totals[1])
        self.assertIn("fid 7 este un rând de total sau subtotal și nu intră în diminuare",
                      about_totals[2])
        self.assertEqual(self.summary(excel)["Rânduri de total, lăsate deoparte (fid)"],
                         "4 (suma rândurilor de deasupra), 6 (doar denumirea și numere), "
                         "7 (denumire de total)")


class DiminuareSurplusProcessingTests(ProcessingTest):
    """A contour larger than the sum of the deed areas."""

    def test_no_area_changes_and_the_outputs_state_the_surplus(self):
        excel = os.path.join(self.tmp, "diminuare.xlsx")
        result = self.reduce(self.csv("nr,suprafata_act,ie\n1,1000.5,\n2,2000,50124\n3,3000,\n"),
                             6500.5, FIXED_FIELD="ie", OUTPUT_XLSX=excel)
        self.assertEqual((result["FACTOR"], result["REDUCTION_PCT"], result["SURPLUS"]),
                         (1.0, 0.0, 500.0))
        rows = {f["nr"]: (f["sup_diminuata"], f["diferenta"])
                for f in result["OUTPUT"].getFeatures()}
        self.assertEqual(rows, {"1": (1000.5, 0.0), "2": (2000.0, 0.0), "3": (3000.0, 0.0)})
        self.assertIn("Conturul depășește suma suprafețelor din acte cu 500 mp, deci "
                      "suprafețele rămân cele din acte.", self.said.infos)
        self.assertEqual(self.summary(excel)["C - A"], 500.0)

    def test_a_smaller_contour_has_no_surplus(self):
        result = self.reduce(self.csv("nr,suprafata_act\n1,6000\n2,4000\n"), 9000)
        self.assertEqual(result["SURPLUS"], 0.0)
        self.assertAlmostEqual(result["FACTOR"], 0.9)


class DiminuareRoundingProcessingTests(ProcessingTest):
    """Areas rounded to the metre from deed areas with decimals, under a contour that cuts
    less than a metre from each parcel."""

    def test_no_parcel_is_rounded_above_its_deed_area(self):
        excel = os.path.join(self.tmp, "diminuare.xlsx")
        result = self.reduce(
            self.csv("nr,suprafata_act,ie\n1,1000.4,\n2,1500.3,50124\n3,2499.3,\n"),
            4999.6, FIXED_FIELD="ie", OUTPUT_XLSX=excel)
        areas = {f["nr"]: f["sup_diminuata"] for f in result["OUTPUT"].getFeatures()}
        self.assertEqual(areas, {"1": 1000, "2": 1500.3, "3": 2499})
        # the messages and the Sumar state the sum the areas have, not the contour
        stated = ("La rotunjire, nicio parcelă nu primește o suprafață mai mare decât cea din "
                  "acte, iar parcelele cu CF/IE au mai multe zecimale decât rotunjirea aleasă, "
                  "deci suma nouă este 4.999,3 mp, nu 5.000 mp.")
        self.assertIn(stated, self.said.infos)
        self.assertEqual(self.summary(excel)["Suma nouă"], 4999.3)
        note = [row[0] for row in xlsx.read_rows(excel, "Sumar")[1]
                if row and "rotunjit" in str(row[0])]
        self.assertEqual(note, [
            "Suprafața conturului, rotunjită la aceeași precizie ca suprafețele noi, este "
            "5.000 mp. " + stated])

    def test_a_sum_equal_to_the_rounded_contour_has_no_message(self):
        self.reduce(self.csv("nr,suprafata_act\n1,1000.4\n2,2499.6\n"), 3150)
        self.assertEqual([m for m in self.said.infos if "suma nouă" in m], [])


class DiminuareColumnProcessingTests(ProcessingTest):
    """A column name the table does not have, as kept in a model or a script."""

    def test_a_column_the_table_lacks_stops_the_run_and_names_the_columns_it_has(self):
        table = self.csv("nr,suprafata,ie\n1,6000,\n2,4000,\n")
        with self.assertRaises(QgsProcessingException) as raised:
            self.reduce(table, 9000)
        self.assertIn("Tabelul 'parcele' nu are coloana 'suprafata_act'. Coloanele lui sunt: "
                      "nr, suprafata, ie.", str(raised.exception))
        self.assertNotIn("Traceback", str(raised.exception))
        with self.assertRaises(QgsProcessingException) as raised:
            self.reduce(table, 9000, AREA_FIELD="suprafata", FIXED_FIELD="cf")
        self.assertIn("nu are coloana 'cf'", str(raised.exception))

    def test_a_table_whose_column_names_are_not_on_its_first_row_is_reported_as_such(self):
        path = os.path.join(self.tmp, "parcele.xlsx")
        wb = xlsx.Workbook()
        sheet = wb.add_sheet("Parcele")
        rows = {1: ["Tabel parcele tarla 12"], 3: ["nr", "suprafata_act"], 4: [1, 6000],
                5: [2, 4000]}
        for number, values in rows.items():
            for column, value in enumerate(values, start=1):
                sheet.set(number, column, value)
        wb.save(path)
        with self.assertRaises(QgsProcessingException) as raised:
            self.reduce(path, 9000)
        self.assertIn("nu are coloana 'suprafata_act'", str(raised.exception))
        self.assertIn("Numele coloanelor nu au fost găsite pe primul rând al tabelului.",
                      str(raised.exception))


def sheet_rows(rows, hidden=()):
    """The XML of a sheet holding rows of numbers and texts; the Excel rows numbered in hidden
    are hidden."""
    out = ["<sheetData>"]
    for number, values in enumerate(rows, start=1):
        concealed = ' hidden="1"' if number in hidden else ""
        out.append(f'<row r="{number}"{concealed}>')
        for column, value in enumerate(values):
            ref = f"{chr(65 + column)}{number}"
            if isinstance(value, str):
                out.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
            else:
                out.append(f'<c r="{ref}"><v>{value}</v></c>')
        out.append("</row>")
    return "".join(out) + "</sheetData>"


class DiminuareSheetProcessingTests(ProcessingTest):
    """A workbook of several sheets given as the table: OGR reads its first sheet."""

    PARCELS = [["nr", "suprafata_act"], [1, 1000], [2, 2000], [3, 3000]]

    def workbook(self, sheets):
        from test_xlsx import WORKSHEET, write_package
        path = os.path.join(self.tmp, "parcele.xlsx")
        write_package(path, [(name, f"worksheets/sheet{number}.xml", body, state, WORKSHEET)
                             for number, (name, body, state) in enumerate(sheets, start=1)])
        return path

    def test_hidden_rows_are_taken_from_the_first_sheet_even_when_excel_hides_that_sheet(self):
        path = self.workbook([
            ("Parcele", sheet_rows(self.PARCELS, hidden=[3]), "hidden"),
            ("Note", sheet_rows([["text"], ["a"], ["b"], ["c"]], hidden=[2, 4]), None)])
        result = self.reduce(path, 3600)
        self.assertEqual({f["nr"]: f["sup_diminuata"] for f in result["OUTPUT"].getFeatures()},
                         {1: 900, 3: 2700})
        self.assertIn("1 rând ascuns în Excel a fost lăsat deoparte: 3.", self.said.warnings)

    def test_a_missing_column_names_the_sheet_that_was_read(self):
        path = self.workbook([("Config", sheet_rows([["lista"], ["a"]]), "hidden"),
                              ("Parcele", sheet_rows(self.PARCELS), None)])
        with self.assertRaises(QgsProcessingException) as raised:
            self.reduce(path, 5400)
        self.assertIn("nu are coloana 'suprafata_act'", str(raised.exception))
        self.assertIn("S-a citit foaia 'Config'; fișierul are și: 'Parcele'.",
                      str(raised.exception))
        # the sheet named in the source is the one read
        result = self.reduce(f"{path}|layername=Parcele", 5400)
        self.assertAlmostEqual(result["FACTOR"], 0.9)


class DiminuareReportProcessingTests(ProcessingTest):

    def test_a_contour_layer_gives_the_areas_the_window_gives(self):
        # 1000.4951 mp is 1000 to the metre; the window's field shows it as 1000,50
        contour = helpers.polygon_layer("Contur tarla 7", [(
            "POLYGON((450000 350000, 450100 350000, 450100 350010.004951, "
            "450000 350010.004951, 450000 350000))", [1])])
        result = self.run_algorithm("areareduction", {
            "TABLE": self.csv("nr,suprafata_act\n1,600\n2,500\n"),
            "AREA_FIELD": "suprafata_act", "CONTOUR": contour, "OUTPUT": "TEMPORARY_OUTPUT"})
        areas = {f["nr"]: f["sup_diminuata"] for f in result["OUTPUT"].getFeatures()}
        self.assertEqual(areas, {"1": 545, "2": 455})

    def test_a_text_longer_than_a_cell_holds_is_a_warning(self):
        excel = os.path.join(self.tmp, "diminuare.xlsx")
        self.reduce(self.csv("nr,suprafata_act,obs\n1,600," + "x" * 40000 + "\n2,500,scurt\n"),
                    990, OUTPUT_XLSX=excel)
        self.assertIn("1 celulă din fișierul Excel a fost scurtată la 32.767 de caractere, cât "
                      "cuprinde o celulă Excel.", self.said.warnings)
        self.assertEqual(self.said.errors, [])


def ring(points):
    return "(" + ", ".join(f"{x} {y}" for x, y in list(points) + [points[0]]) + ")"


def blob(radius, vertices, seed, centre=ORIGIN, jitter=0.02):
    """A polygon of that many vertices around centre, its outline up to jitter (a share of
    the radius) off a circle, with coordinates rounded to the millimetre."""
    random = Random(seed)
    points = []
    for i in range(vertices):
        angle = 2 * math.pi * i / vertices
        r = radius * (1 + random.uniform(-jitter, jitter))
        points.append((round(centre[0] + r * math.cos(angle), 3),
                       round(centre[1] + r * math.sin(angle), 3)))
    return "POLYGON(" + ring(points) + ")"


def quadrilaterals(count, spread, seed, centre=ORIGIN):
    """count irregular four-sided imobile of 100 to 900 mp, their centres within spread metres
    of centre on each axis, coordinates rounded to the millimetre."""
    random = Random(seed)
    rows = []
    for i in range(count):
        cx = centre[0] + random.uniform(-spread, spread)
        cy = centre[1] + random.uniform(-spread, spread)
        points = [(cx + random.uniform(-15, -5), cy + random.uniform(-15, -5)),
                  (cx + random.uniform(5, 15), cy + random.uniform(-15, -5)),
                  (cx + random.uniform(5, 15), cy + random.uniform(5, 15)),
                  (cx + random.uniform(-15, -5), cy + random.uniform(5, 15))]
        rows.append(("POLYGON(" + ring([(round(x, 3), round(y, 3)) for x, y in points]) + ")",
                     [str(i)]))
    return rows


class IntravilanTaskTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_reports_intra_")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def layer(self, name, rows, fields="cod:string", geometry="Polygon"):
        return helpers.polygon_layer(name, rows, fields=fields, geometry=geometry)

    def report(self, imobile, intravilan, **options):
        """Run the report for the imobile rows [(wkt, [cod])] and the intravilan layers, or
        the WKT of one contour."""
        if isinstance(imobile, list):
            imobile = self.layer("Imobile", imobile)
        if isinstance(intravilan, str):
            intravilan = [self.layer("Intravilan", [(intravilan, ["T"])])]
        settings = dict(
            imobile=[LayerSnapshot(imobile)], intravilan=[LayerSnapshot(z) for z in intravilan],
            excel_fields=[f.name() for f in imobile.fields()], id_field="cod", owner_field=None,
            tolerance=0.5)
        settings.update(options)
        task = intravilan_service.IntravilanTask(**settings)
        self.assertTrue(task.run(), task.error_msg)
        return task

    @staticmethod
    def by_code(task):
        return {row["attrs"]["cod"]: row for row in task.rows}

    @staticmethod
    def statuses(task):
        return dict(Counter(row["situatie"] for row in task.rows))

    @staticmethod
    def areas(row):
        return row["sup_totala"], row["sup_intra"], row["sup_extra"], row["pct_intra"]


class IntravilanToleranceTests(IntravilanTaskTest):
    """Whether an imobil at the limit is wholly inside, wholly outside or partial, with and
    without a tolerance."""

    def test_with_no_tolerance_an_imobil_inside_the_contour_is_wholly_in_intravilan(self):
        task = self.report(quadrilaterals(400, 800, seed=3), blob(2000, 720, seed=4),
                           tolerance=0.0)
        self.assertEqual(self.statuses(task), {intravilan_service.INTEGRAL_INTRA: 400})
        self.assertEqual({(row["sup_extra"], row["pct_intra"]) for row in task.rows},
                         {(0, 100.0)})

    def test_with_no_tolerance_an_imobil_outside_the_contour_is_wholly_in_extravilan(self):
        far = (ORIGIN[0] + 5000, ORIGIN[1])
        task = self.report(quadrilaterals(100, 800, seed=5, centre=far),
                           blob(2000, 720, seed=4), tolerance=0.0)
        self.assertEqual(self.statuses(task), {intravilan_service.INTEGRAL_EXTRA: 100})

    def test_with_no_tolerance_a_strip_of_a_millimetre_outside_makes_an_imobil_partial(self):
        # 10 m by 10 m, its western millimetre outside the contour: 0.01 mp
        row = self.report([(helpers.square(449999.999, 350000, 10), ["A"])],
                          square(0, -100, 500), tolerance=0.0).rows[0]
        self.assertEqual(row["situatie"], intravilan_service.PARTIAL)

    def test_a_contour_edge_beside_an_imobil_edge_does_not_put_the_imobil_outside(self):
        # the imobil lies inside the contour, whose outline has a point a third of a
        # millimetre from the imobil's western edge, which has no point there
        from test_overlay import AROUND, IMOBIL
        for tolerance in (0.5, 0.0):
            row = self.report([(IMOBIL, ["A"])], AROUND, tolerance=tolerance).rows[0]
            self.assertEqual(row["situatie"], intravilan_service.INTEGRAL_INTRA, tolerance)
            self.assertEqual(self.areas(row), (1000, 1000, 0, 100.0), tolerance)

    def test_an_imobil_beside_such_a_contour_edge_stays_outside(self):
        # the neighbour west of that edge: the rest of the ground around the imobil
        from qgis.core import QgsGeometry  # type: ignore
        from test_overlay import AROUND, IMOBIL
        outside = overlay.difference(QgsGeometry.fromWkt(AROUND), QgsGeometry.fromWkt(IMOBIL))
        row = self.report([(outside.asWkt(), ["B"])], IMOBIL, tolerance=0.5).rows[0]
        self.assertEqual(row["situatie"], intravilan_service.INTEGRAL_EXTRA)
        self.assertEqual(row["sup_intra"], 0)


class IntravilanContourTests(IntravilanTaskTest):
    """A contour drawn as several polygons, in one layer or in several."""

    def test_a_contour_cut_in_pieces_gives_the_areas_of_the_whole_one(self):
        imobile = self.layer("Imobile", quadrilaterals(300, 1100, seed=7))
        whole = self.report(imobile, blob(1000, 360, seed=8))
        from qgis.core import QgsGeometry  # type: ignore
        outline = QgsGeometry.fromWkt(blob(1000, 360, seed=8))
        pieces = []
        for ix in range(-2, 2):
            for iy in range(-2, 2):
                # tiles of 600 m that overlap their neighbours by 100 m
                tile = QgsGeometry.fromWkt(square(ix * 500 - 50, iy * 500 - 50, 600))
                piece = overlay.intersection(outline, tile)
                if piece.area() > 0:
                    pieces.append((piece.asWkt(), [f"{ix},{iy}"]))
        half = len(pieces) // 2
        cut = self.report(imobile, [
            self.layer("Intravilan nord", pieces[:half], geometry="MultiPolygon"),
            self.layer("Intravilan sud", pieces[half:], geometry="MultiPolygon")])
        self.assertEqual({code: self.areas(row) + (row["situatie"],)
                          for code, row in self.by_code(cut).items()},
                         {code: self.areas(row) + (row["situatie"],)
                          for code, row in self.by_code(whole).items()})
        self.assertGreater(self.statuses(whole)[intravilan_service.PARTIAL], 5)

    def test_two_large_contour_features_are_computed_in_seconds(self):
        # the intravilan, and its extensions around it as one feature of many parts
        patches = ", ".join(
            blob(60, 40, seed=i, centre=(ORIGIN[0] + 1750 * math.cos(i / 4.0),
                                         ORIGIN[1] + 1750 * math.sin(i / 4.0)))[len("POLYGON"):]
            for i in range(25))
        contour = self.layer("Intravilan", [
            (blob(1500, 20000, seed=9, jitter=0.001), ["existent"]),
            (f"MULTIPOLYGON({patches})", ["extinderi"])], geometry="MultiPolygon")
        begin = time.monotonic()
        task = self.report(quadrilaterals(300, 1900, seed=10), [contour])
        elapsed = time.monotonic() - begin
        self.assertEqual(len(task.rows), 300)
        self.assertGreater(self.statuses(task)[intravilan_service.INTEGRAL_INTRA], 50)
        # merging the two features takes about a sixth of a second, so merging them again for
        # each of the 300 imobile would take far longer than this bound
        self.assertLess(elapsed, 15.0)


class IntravilanRoundingTests(IntravilanTaskTest):
    """Whole square metres in the rows and in the totals."""

    CONTOUR = square(0, 0, 1000)

    def strip(self, x, width, code, y=0):
        return (f"POLYGON(({ORIGIN[0] + x} {ORIGIN[1] + y}, {ORIGIN[0] + x + width} "
                f"{ORIGIN[1] + y}, {ORIGIN[0] + x + width} {ORIGIN[1] + y + 1}, "
                f"{ORIGIN[0] + x} {ORIGIN[1] + y + 1}, {ORIGIN[0] + x} {ORIGIN[1] + y}))",
                [code])

    def test_an_imobil_within_the_tolerance_has_its_whole_area_on_one_side(self):
        rows = self.by_code(self.report([
            # 100.6 mp with 0.4 mp west of the limit, and 99.9 mp with 0.45 mp east of it
            self.strip(-0.4, 100.6, "intra"), self.strip(-99.45, 99.9, "extra", y=5),
            # 100.6 mp with 50.3 mp on each side
            self.strip(-50.3, 100.6, "partial", y=10)], self.CONTOUR))
        self.assertEqual(self.areas(rows["intra"]), (101, 101, 0, 100.0))
        self.assertEqual(rows["intra"]["situatie"], intravilan_service.INTEGRAL_INTRA)
        self.assertEqual(self.areas(rows["extra"]), (100, 0, 100, 0.0))
        self.assertEqual(rows["extra"]["situatie"], intravilan_service.INTEGRAL_EXTRA)
        self.assertEqual(self.areas(rows["partial"]), (101, 51, 50, 50.0))

    def test_the_totals_are_rounded_once_from_the_areas_as_measured(self):
        excel = os.path.join(self.tmp, "raport.xlsx")
        # three imobile of 100.4 mp inside, one of 100.4 mp outside, one of 100.4 mp across
        task = self.report([
            self.strip(10, 100.4, "A"), self.strip(10, 100.4, "B", y=5),
            self.strip(10, 100.4, "C", y=10), self.strip(-200, 100.4, "D", y=15),
            self.strip(-50.2, 100.4, "E", y=20)], self.CONTOUR, excel_path=excel)
        self.assertEqual([row["sup_totala"] for row in task.rows], [100] * 5)
        # 502 mp in all: 301.2 inside, 100.4 outside, 50.2 and 50.2
        self.assertEqual(task.totals, {"sup_totala": 502, "sup_intra": 351, "sup_extra": 151})
        summary = {row[0]: row for row in xlsx.read_rows(excel, "Sumar")[1] if row and row[0]}
        self.assertEqual(summary["Toate imobilele"][3], 502)
        self.assertEqual(summary[intravilan_service.INTEGRAL_INTRA][3], 301)
        self.assertEqual(summary["Suprafața tuturor imobilelor"][1], 502)
        self.assertEqual((summary["În intravilan"][1], summary["În extravilan"][1]), (351, 151))
        self.assertEqual(summary["Partea din intravilan a imobilelor parțiale"][1], 50)


class IntravilanExcelTests(IntravilanTaskTest):

    def test_a_text_longer_than_a_cell_holds_is_counted(self):
        imobile = self.layer("Imobile", [(square(10, 10, 10), ["A", "x" * 40000]),
                                         (square(30, 10, 10), ["B", "scurt"])],
                             fields="cod:string,proprietari:string(50000)")
        task = self.report(imobile, square(0, 0, 100),
                           excel_path=os.path.join(self.tmp, "raport.xlsx"))
        # once in the sheet of all imobile and once in the sheet of its status
        self.assertEqual(task.cut_cells, 2)
        self.assertIsNone(task.report_error)


class IntravilanProcessingTests(ProcessingTest):

    def test_with_no_tolerance_the_imobile_inside_the_contour_are_counted_as_integral(self):
        imobile = helpers.polygon_layer("Imobile", quadrilaterals(200, 800, seed=3),
                                        fields="cod:string")
        contour = helpers.polygon_layer("Intravilan", [(blob(2000, 720, seed=4), ["T"])],
                                        fields="nume:string")
        result = self.run_algorithm("intravilanreport", {
            "IMOBILE": imobile, "INTRAVILAN": contour, "TOLERANCE": 0.0,
            "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertEqual((result["INTRAVILAN_COUNT"], result["PARTIAL_COUNT"],
                          result["EXTRAVILAN_COUNT"]), (200, 0, 0))

    def test_a_text_longer_than_a_cell_holds_is_a_warning(self):
        imobile = helpers.polygon_layer(
            "Imobile", [(square(10, 10, 10), ["A", "x" * 40000])],
            fields="cod:string,proprietari:string(50000)")
        contour = helpers.polygon_layer("Intravilan", [(square(0, 0, 100), ["T"])],
                                        fields="nume:string")
        self.run_algorithm("intravilanreport", {
            "IMOBILE": imobile, "INTRAVILAN": contour, "OUTPUT": "TEMPORARY_OUTPUT",
            "OUTPUT_XLSX": os.path.join(self.tmp, "raport.xlsx")})
        self.assertIn("2 celule din fișierul Excel au fost scurtate la 32.767 de caractere, cât "
                      "cuprinde o celulă Excel.", self.said.warnings)


def tearDownModule():
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
