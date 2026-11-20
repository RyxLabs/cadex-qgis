# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Diminuare Suprafețe and Raport Intravilan / Extravilan windows with a fake iface, on the
offscreen platform."""

import os
import tempfile
import time
import unittest

import helpers

helpers.start_qgis(gui=True)

from qgis.core import QgsProject  # noqa: E402  # type: ignore
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402  # type: ignore

from cadex.dialogs.diminuare_dialog import BAD_TINT, DiminuareDialog  # noqa: E402
from cadex.dialogs.intravilan_dialog import IntravilanDialog  # noqa: E402
from cadex.services import settings_service, xlsx  # noqa: E402


square = helpers.square_in_romania


class WindowTest(unittest.TestCase):

    def setUp(self):
        settings_service.reset_all()
        self.iface = helpers.fake_iface()
        self.project = QgsProject.instance()
        self.project.clear()
        self.tmp = tempfile.mkdtemp(prefix="cadex_reports_")

    def tearDown(self):
        self.project.clear()
        helpers.remove_folder(self.tmp)

    settle = staticmethod(helpers.settle)


class DiminuareWindowTest(WindowTest):

    def table(self, text, name="parcele.csv"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(text)
        return path

    def window(self, path, contour=None):
        dialog = DiminuareDialog(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        # the error and result windows are modal
        dialog.failures, dialog.results = [], []
        dialog.fail = dialog.failures.append
        dialog.show_result = lambda headline, **details: dialog.results.append(
            dict(details, headline=headline))
        dialog.file_row.set_path(path)
        dialog._load_file()
        if contour is not None:
            dialog.contour_spin.setValue(contour)
        return dialog

    def save(self, dialog, name="rezultat.xlsx"):
        """Press Salvează Excel with name chosen in the file window; returns the path."""
        from unittest import mock
        from cadex.dialogs import diminuare_dialog
        path = os.path.join(self.tmp, name)
        with mock.patch.object(diminuare_dialog.QFileDialog, "getSaveFileName",
                               return_value=(path, "")):
            dialog.save_btn.click()
        return path

    @staticmethod
    def sheet(path, name):
        return xlsx.read_rows(path, name)[1]

    summary = staticmethod(helpers.summary_facts)

    @staticmethod
    def shown(dialog, column):
        return [dialog.table.item(row, column).text() for row in range(dialog.table.rowCount())]


class DiminuareTotalRowWindowTests(DiminuareWindowTest):
    """Which rows of a parcel table the window leaves out as totals, and what it shows."""

    OWNERS = ("Nr;Proprietar;Suprafata act;IE;Obs\n1;POPESCU ION;5000;;\n"
              "2;SC TOTAL AGRO SRL;20000;;\n3;IONESCU MARIA;5000;;\n"
              "4;DINU ANA;10000;;din total 5000 mp arabil\n")

    def test_a_parcel_with_the_word_total_in_its_owner_or_remark_is_reduced(self):
        dialog = self.window(self.table(self.OWNERS), 36000)
        result = dialog._result[0]
        self.assertAlmostEqual(result.factor, 0.9)
        self.assertEqual(self.shown(dialog, 3), ["4.500", "18.000", "4.500", "9.000"])
        self.assertEqual(result.left_out, [])
        # the two rows are named, so the user sees a total row that was taken for a parcel
        self.assertIn("2 rânduri cu cuvântul 'total' sau 'sumă' au rămas parcele", dialog.status.text())
        self.assertIn(": 3, 5.", dialog.status.text())
        for row in range(4):
            self.assertNotEqual(dialog.table.item(row, 1).background().color().name(),
                                BAD_TINT.lower(), row)

    def test_a_parcel_row_containing_total_is_reported_as_a_warning(self):
        from unittest import mock
        dialog = self.window(self.table(self.OWNERS), 36000)
        with mock.patch.object(dialog.status, "set_status") as shown:
            dialog._calculate()
        text, level = shown.call_args[0]
        self.assertIn("au rămas parcele", text)
        self.assertEqual(level, "warning")

    def test_a_row_named_total_is_left_out_whatever_its_area(self):
        # the total also counts a parcel that the table does not hold
        dialog = self.window(self.table(
            "Proprietar;Suprafata act\nPOPESCU ION;1000\nIONESCU MARIA;1500\nTOTAL;3200\n"), 2250)
        result = dialog._result[0]
        self.assertAlmostEqual(result.factor, 0.9)
        self.assertEqual(result.left_out, [2])
        self.assertEqual(self.shown(dialog, 3), ["900", "1.350", ""])
        self.assertEqual(dialog.status.text(),
                         "1 rând de total sau subtotal a fost lăsat deoparte: 4.")
        self.assertEqual(dialog.table.item(2, 1).background().color().name(), BAD_TINT.lower())

    def test_a_total_above_its_parcels_is_left_out(self):
        dialog = self.window(self.table(
            "Proprietar;Suprafata act\nTOTAL GENERAL:;2500\nPOPESCU ION;1000\n"
            "IONESCU MARIA;1500\n"), 2250)
        result = dialog._result[0]
        self.assertAlmostEqual(result.factor, 0.9)
        self.assertEqual(self.shown(dialog, 3), ["", "900", "1.350"])

    # the total rows of this table hold the word inside a longer text
    REASONS = ("Nr;Proprietar;Suprafata act;IE\n1;A;10000;\n2;B;20000;\n3;C;15000;\n"
               "Total conform acte;;45000;\n4;D;700;\nSubtotal parcele noi;;800;\n")

    def test_a_total_label_inside_a_longer_text_is_left_out_with_the_reason(self):
        dialog = self.window(self.table(self.REASONS), 43830)
        result = dialog._result[0]
        self.assertEqual(result.left_out, [3, 5])
        self.assertEqual(result.total_deed, 45700)
        text = dialog.status.text()
        self.assertIn("1 rând de total sau subtotal a fost lăsat deoparte, având suprafața egală "
                      "cu suma rândurilor de deasupra: 5.", text)
        self.assertIn("1 rând de total sau subtotal a fost lăsat deoparte, având doar denumirea "
                      "și numere, fără datele unei parcele: 7.", text)
        self.assertEqual(dialog.table.item(3, 1).background().color().name(), BAD_TINT.lower())

    def test_the_saved_sumar_names_the_total_rows_and_why(self):
        dialog = self.window(self.table(self.REASONS + "TOTAL GENERAL;;46500;\n"), 43830)
        self.assertIn("1 rând de total sau subtotal a fost lăsat deoparte: 8.",
                      dialog.status.text())
        facts = self.summary(self.save(dialog))
        self.assertEqual(facts["Rânduri de total, lăsate deoparte"],
                         "5 (suma rândurilor de deasupra), 7 (doar denumirea și numere), "
                         "8 (denumire de total)")
        self.assertEqual(dialog.failures, [])

    def test_the_rows_left_out_for_their_sum_follow_the_area_column(self):
        dialog = self.window(self.table(
            "Proprietar;Suprafata act;Suprafata veche\nA;5000;4000\nB;7000;9000\n"
            "Total conform acte;12000;20000\n"), 10800)
        dialog.area_combo.setCurrentIndex(dialog.area_combo.findData("Suprafata act"))
        self.assertEqual(dialog._result[0].left_out, [2])
        # in the other column the row's number is not the sum of the two above it
        dialog.area_combo.setCurrentIndex(dialog.area_combo.findData("Suprafata veche"))
        self.assertEqual(dialog._result[0].left_out, [])
        self.assertIn("1 rând cu cuvântul 'total' sau 'sumă' a rămas parcelă", dialog.status.text())


class DiminuareCfIeWindowTests(DiminuareWindowTest):

    def test_words_and_marks_for_a_missing_cf_ie_leave_the_parcel_among_the_reduced(self):
        dialog = self.window(self.table(
            "Nr;Suprafata act;IE\n1;1000;N/A\n2;1000;nu are\n3;1000;-\n4;1000;?\n"
            "5;1000;lipsă\n6;1000;50124\n"), 5500)
        result = dialog._result[0]
        self.assertEqual(result.fixed_rows, [5])
        self.assertEqual(self.shown(dialog, 2), ["", "", "", "", "", "50124"])
        self.assertEqual(self.shown(dialog, 3)[0], "900")


class DiminuareSurplusWindowTests(DiminuareWindowTest):
    """A contour larger than the sum of the deed areas."""

    TABLE = "Nr;Suprafata act;IE\n1;1000,5;\n2;2000;50124\n3;3000;\n"

    def test_no_area_changes_and_the_window_states_the_surplus(self):
        dialog = self.window(self.table(self.TABLE), 6500.5)
        result = dialog._result[0]
        self.assertEqual((result.factor, result.surplus), (1.0, 500.0))
        self.assertEqual(self.shown(dialog, 3), ["1.000,5", "2.000", "3.000"])
        self.assertEqual(dialog.status.text(), (
            "Conturul depășește suma suprafețelor din acte cu 500 mp, deci suprafețele rămân "
            "cele din acte."))
        self.assertIn("Factor = <b>1</b>", dialog.summary.text())
        self.assertTrue(dialog.save_btn.isEnabled())

    def test_the_saved_table_keeps_the_areas_and_states_the_surplus(self):
        dialog = self.window(self.table(self.TABLE), 6500.5)
        path = self.save(dialog)
        summary = self.summary(path)
        self.assertEqual((summary["C - A"], summary["Factor"], summary["Diminuare"]),
                         (500.0, 1.0, 0.0))
        rows = self.sheet(path, "Diminuare")
        self.assertEqual(rows[0][-2:], ["Suprafață diminuată (mp)", "Diferență (mp)"])
        self.assertEqual([row[-2:] for row in rows[1:]],
                         [[1000.5, 0.0], [2000.0, 0.0], [3000.0, 0.0]])
        counts = dialog.results[0]["counts"]
        self.assertEqual(counts[0], ("Conturul depășește suma din acte cu", "500 mp", None))
        self.assertEqual(counts[1][:2], ("Parcele fără CF/IE, neschimbate", 2))

    def test_a_contour_equal_to_the_deeds_is_an_ordinary_result(self):
        dialog = self.window(self.table(self.TABLE), 6000.5)
        result = dialog._result[0]
        self.assertEqual(result.surplus, 0.0)
        self.assertNotIn("depășește", dialog.status.text())


class DiminuareRoundingWindowTests(DiminuareWindowTest):
    """Areas rounded to the metre from deed areas with decimals, under a contour that cuts
    less than a metre from each parcel."""

    TABLE = "Nr;Suprafata act;IE\n1;1000,4;\n2;1500,3;50124\n3;2499,3;\n"
    SUM = ("La rotunjire, nicio parcelă nu primește o suprafață mai mare decât cea din acte, "
           "iar parcelele cu CF/IE au mai multe zecimale decât rotunjirea aleasă, deci suma "
           "nouă este 4.999,3 mp, nu 5.000 mp.")

    def window(self, path, contour=None):
        dialog = super().window(path, contour)
        dialog.round_combo.setCurrentIndex(dialog.round_combo.findData(0))
        return dialog

    def note(self, path):
        """The note under the calculation of the Sumar sheet."""
        return [row[0] for row in self.sheet(path, "Sumar") if row and "rotunjit" in str(row[0])]

    def test_no_parcel_is_rounded_above_its_deed_area(self):
        for contour in (5000.0, 4999.6):
            dialog = self.window(self.table(self.TABLE), contour)
            self.assertEqual(self.shown(dialog, 3), ["1.000", "1.500,3", "2.499"], contour)
            # the sum the areas have, not the contour
            self.assertEqual(dialog.status.text(), self.SUM, contour)

    def test_the_saved_sumar_states_the_sum_the_areas_have(self):
        dialog = self.window(self.table(self.TABLE), 4999.6)
        path = self.save(dialog)
        self.assertEqual(self.summary(path)["Suma nouă"], 4999.3)
        self.assertEqual(self.note(path), [
            "Suprafața conturului, rotunjită la aceeași precizie ca suprafețele noi, este "
            "5.000 mp. " + self.SUM])
        self.assertEqual([row[-2:] for row in self.sheet(path, "Diminuare")[1:]],
                         [[1000.0, -0.4], [1500.3, 0.0], [2499.0, -0.3]])

    def test_a_sum_equal_to_the_rounded_contour_is_described_as_such(self):
        dialog = self.window(self.table("Nr;Suprafata act\n1;1000,4\n2;2499,6\n"), 3150)
        self.assertEqual(dialog.status.text(), "")
        path = self.save(dialog)
        self.assertEqual(self.summary(path)["Suma nouă"], 3150)
        self.assertEqual(self.note(path), [
            "Suprafețele noi sunt rotunjite astfel încât suma lor, împreună cu parcelele cu "
            "CF/IE, să fie egală cu suprafața conturului rotunjită la aceeași precizie."])


class DiminuareSheetWindowTests(DiminuareWindowTest):

    def test_the_window_opens_a_workbook_on_the_first_sheet_excel_shows(self):
        from test_xlsx import ONE_CELL, PARCELE, SHARED, STYLES, WORKSHEET, write_package
        path = os.path.join(self.tmp, "parcele.xlsx")
        write_package(path, [
            ("Config", "worksheets/sheet1.xml", ONE_CELL.format("lista"), "hidden", WORKSHEET),
            ("Parcele", "worksheets/sheet2.xml", PARCELE, None, WORKSHEET)], SHARED, STYLES)
        dialog = self.window(path, 2700)
        self.assertEqual(dialog.sheet_combo.currentText(), "Parcele")
        self.assertEqual([dialog.sheet_combo.itemText(i) for i in range(2)],
                         ["Config", "Parcele"])
        self.assertEqual(dialog.area_combo.currentData(), "Suprafata act")
        self.assertEqual(len(dialog._rows), 3)
        # the hidden sheet is read when chosen, and stays chosen after a reload
        dialog.sheet_combo.setCurrentText("Config")
        dialog.sheet_combo.activated.emit(0)
        self.assertEqual((dialog.sheet_combo.currentText(), dialog._header), ("Config", ["lista"]))
        # the saved facts name the sheet the table came from
        dialog.sheet_combo.setCurrentText("Parcele")
        dialog.sheet_combo.activated.emit(1)
        self.assertEqual(self.summary(self.save(dialog))["Fișier sursă"],
                         "parcele.xlsx, foaia Parcele")


class DiminuareContourWindowTests(DiminuareWindowTest):
    """The contour area of the calculation: taken from a layer, or typed."""

    PARCELS = "Nr;Suprafata act\n1;600\n2;500\n"

    def contour_layer(self, width):
        layer = helpers.polygon_layer("Contur tarla 7", [(
            f"POLYGON((450000 350000, 450100 350000, 450100 {350000 + width}, "
            f"450000 {350000 + width}, 450000 350000))", [1])])
        self.project.addMapLayer(layer)
        return layer

    def test_an_area_taken_from_the_geometry_is_rounded_once(self):
        # 1000.4951 mp: 1000 to the metre, though the field shows it as 1000,50
        layer = self.contour_layer(10.004951)
        dialog = self.window(self.table(self.PARCELS))
        dialog.layer_combo.setLayer(layer)
        dialog.selected_only.setChecked(False)
        dialog._take_area()
        self.assertEqual(dialog.contour_spin.value(), 1000.5)
        result = dialog._result[0]
        self.assertAlmostEqual(result.contour, 1000.4951, places=6)
        self.assertEqual(result.new_areas, [545, 455])
        self.assertIn("C = <b>1.000,4951 mp</b>", dialog.summary.text())
        facts = self.summary(self.save(dialog))
        self.assertAlmostEqual(facts["C"], 1000.4951, places=4)
        self.assertEqual(facts["Contur"], "Contur tarla 7, tot layerul (1 element)")

    def test_an_area_taken_from_the_geometry_replaces_a_typed_area_that_shows_the_same_two_decimals(self):
        layer = self.contour_layer(10.004951)
        dialog = self.window(self.table(self.PARCELS), 1000.5)
        self.assertEqual(dialog._result[0].new_areas, [546, 455])
        dialog.layer_combo.setLayer(layer)
        dialog.selected_only.setChecked(False)
        dialog._take_area()
        self.assertEqual(dialog._result[0].new_areas, [545, 455])

    def test_a_typed_area_replaces_the_one_taken_from_the_geometry(self):
        layer = self.contour_layer(10.004951)
        dialog = self.window(self.table(self.PARCELS))
        dialog.layer_combo.setLayer(layer)
        dialog.selected_only.setChecked(False)
        dialog._take_area()
        dialog.contour_spin.setValue(990)
        result = dialog._result[0]
        self.assertEqual((result.contour, result.new_areas), (990.0, [540, 450]))
        self.assertEqual(self.summary(self.save(dialog))["Contur"], "introdus manual")

    def test_typing_an_area_gives_one_result_when_the_typing_stops(self):
        from qgis.PyQt.QtTest import QTest  # type: ignore
        from cadex.dialogs.diminuare_dialog import TYPING_PAUSE
        dialog = self.window(self.table(self.PARCELS), 1045)
        dialog.show()
        before = dialog._result[0]
        dialog.contour_spin.setFocus()
        dialog.contour_spin.selectAll()
        results = []
        for key in "990":
            QTest.keyClicks(dialog.contour_spin, key)
            QApplication.processEvents()
            results.append(dialog._result[0])
        # the result shown while the digits are typed is the one from before
        self.assertEqual([r is before for r in results], [True, True, True])
        self.settle(TYPING_PAUSE / 1000.0 + 0.3)
        self.assertEqual((dialog._result[0].contour, dialog._result[0].new_areas),
                         (990.0, [540, 450]))


class DiminuareLongTableWindowTests(DiminuareWindowTest):

    def test_a_changed_input_shows_its_result_at_once_on_a_long_table(self):
        rows = "".join(f"{i};POPESCU ION {i};{1000 + i % 7};\n" for i in range(1, 2001))
        dialog = self.window(self.table("Nr;Proprietar;Suprafata act;IE\n" + rows), 1900000)
        dialog.show()
        self.settle(0.3)
        self.assertEqual(dialog.table.rowCount(), 2000)
        begin = time.monotonic()
        dialog.contour_spin.setValue(1900001)
        QApplication.processEvents()
        dialog.round_combo.setCurrentIndex(dialog.round_combo.findData(2))
        QApplication.processEvents()
        elapsed = time.monotonic() - begin
        self.assertEqual(dialog.table.rowCount(), 2000)
        self.assertEqual(dialog._result[0].contour, 1900001)
        # two refills of 2,000 rows; replacing the items of a shown table one by one takes about
        # 5 ms each, 20 s for both
        self.assertLess(elapsed, 5.0)

    def test_a_text_longer_than_a_cell_holds_is_reported_when_saving(self):
        dialog = self.window(self.table(
            "Nr;Suprafata act;Obs\n1;600;" + "x" * 40000 + "\n2;500;scurt\n"), 990)
        path = self.save(dialog)
        self.assertEqual(dialog.failures, [])
        self.assertIn("1 celulă din fișierul Excel a fost scurtată la 32.767 de caractere, cât "
                      "cuprinde o celulă Excel.", dialog.results[0]["notes"])
        self.assertEqual(len(self.sheet(path, "Diminuare")[1][2]), 32767)


class DiminuareRememberedColumnTests(DiminuareWindowTest):
    """The area and CF/IE columns of one table at the next table: a column picked by hand
    is picked again in its project; a column preselected by its name is not kept."""

    AREA_ONLY = "Nr;Suprafata\n1;6000\n2;4000\n"
    # the deed area has its own column here and Suprafata is the measured one
    TWO_AREAS = "Nr;Suprafata;Sup_act\n1;5990;6000\n2;3995;4000\n"
    ONE_NUMBER = "Nr;Suprafata act;CF\n1;6000;\n2;4000;50123\n"
    # IE holds the identifiers; CF is an old paper number that most parcels have
    TWO_NUMBERS = "Nr;Suprafata act;IE;CF\n1;6000;;1204\n2;4000;60111;877\n3;5000;;912\n"
    BOTH = "Nr;Suprafata;Sup_act;IE;CF\n1;5990;6000;;1204\n2;3995;4000;60111;\n3;4990;5000;;\n"

    def saved_project(self, name):
        """A new project saved under that name; returns its file."""
        self.project.clear()
        self.project.addMapLayer(helpers.polygon_layer(name, [(square(0, 0, 10), [1])]))
        path = os.path.join(self.tmp, f"{name}.qgz")
        self.assertTrue(self.project.write(path))
        return path

    def closed(self, dialog):
        """Press Închide, which keeps the choices of a window that has a result."""
        self.assertIsNotNone(dialog._result, dialog.status.text())
        dialog.done(0)

    @staticmethod
    def pick(combo, data):
        """Pick the entry with that data as a click in the list does."""
        index = combo.findData(data)
        combo.setCurrentIndex(index)
        combo.activated.emit(index)

    def test_an_area_column_preselected_in_one_table_does_not_decide_the_next_table(self):
        self.saved_project("uat_a")
        one = self.window(self.table(self.AREA_ONLY, "a.csv"), 9000)
        self.assertEqual(one.area_combo.currentData(), "Suprafata")
        self.closed(one)
        self.saved_project("uat_b")
        two = self.window(self.table(self.TWO_AREAS, "b.csv"), 9000)
        self.assertEqual(two.area_combo.currentData(), "Sup_act")

    def test_a_cf_ie_column_preselected_in_one_table_does_not_decide_the_next_table(self):
        self.saved_project("uat_a")
        one = self.window(self.table(self.ONE_NUMBER, "a.csv"), 9000)
        self.assertEqual(one.fixed_combo.currentData(), "CF")
        self.closed(one)
        self.saved_project("uat_b")
        two = self.window(self.table(self.TWO_NUMBERS, "b.csv"), 14000)
        self.assertEqual(two.fixed_combo.currentData(), "IE")
        # the parcel with an IE keeps its area and the other two are reduced
        self.assertEqual(two._result[0].fixed_rows, [1])

    def test_columns_picked_by_hand_are_picked_again_in_their_project_only(self):
        first = self.saved_project("uat_a")
        table = self.table(self.BOTH)
        one = self.window(table, 14000)
        self.assertEqual((one.area_combo.currentData(), one.fixed_combo.currentData()),
                         ("Sup_act", "IE"))
        self.pick(one.area_combo, "Suprafata")
        self.pick(one.fixed_combo, "CF")
        self.closed(one)
        later = self.window(table, 14000)
        self.assertEqual((later.area_combo.currentData(), later.fixed_combo.currentData()),
                         ("Suprafata", "CF"))
        self.saved_project("uat_b")
        other = self.window(table, 14000)
        self.assertEqual((other.area_combo.currentData(), other.fixed_combo.currentData()),
                         ("Sup_act", "IE"))
        self.project.clear()
        self.assertTrue(self.project.read(first))
        again = self.window(table, 14000)
        self.assertEqual((again.area_combo.currentData(), again.fixed_combo.currentData()),
                         ("Suprafata", "CF"))

    def test_the_choice_of_no_cf_ie_column_is_made_again_in_its_project(self):
        self.saved_project("uat_a")
        table = self.table(self.ONE_NUMBER)
        one = self.window(table, 9000)
        self.pick(one.fixed_combo, "")
        self.closed(one)
        self.assertEqual(self.window(table, 9000).fixed_combo.currentData(), "")
        self.saved_project("uat_b")
        self.assertEqual(self.window(table, 9000).fixed_combo.currentData(), "CF")


class IntravilanWindowTests(WindowTest):
    """What the Raport Intravilan window shows when a report ends."""

    def strip(self, x, width, y):
        """A strip one metre tall, width metres long, from x metres east of the limit."""
        return (f"POLYGON(({450000 + x} {350000 + y}, {450000 + x + width} {350000 + y}, "
                f"{450000 + x + width} {350001 + y}, {450000 + x} {350001 + y}, "
                f"{450000 + x} {350000 + y}))")

    def report(self, imobile, excel=""):
        """Run the window for the imobile rows and an intravilan east of x = 450000; returns
        what the result window was given."""
        layer = helpers.polygon_layer("Imobile sector 1", imobile,
                                      fields="cod:string,proprietari:string(50000)")
        contour = helpers.polygon_layer("limita intravilan", [(square(0, -100, 1000), ["T"])],
                                        fields="nume:string")
        self.project.addMapLayers([layer, contour])
        dialog = IntravilanDialog(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        dialog.imobile.set_checked_ids([layer.id()])
        dialog.intravilan.set_checked_ids([contour.id()])
        dialog.gpkg_row.set_path("")
        dialog.excel_row.set_path(excel)
        dialog.add_to_project.setChecked(True)
        shown, failures = [], []
        dialog.show_result = lambda headline, **details: shown.append(details)
        dialog.fail = failures.append
        dialog.confirm = lambda *args, **more: True
        dialog._run()
        self.assertTrue(helpers.wait(lambda: dialog._task is None, 60))
        self.assertEqual(failures, [])
        self.assertEqual(len(shown), 1)
        return shown[0]

    def test_the_areas_shown_are_rounded_once_from_the_areas_as_measured(self):
        # 100.4 mp each: three inside, one outside, one with half on each side
        result = self.report([
            (self.strip(10, 100.4, 0), ["A", None]), (self.strip(10, 100.4, 5), ["B", None]),
            (self.strip(10, 100.4, 10), ["C", None]), (self.strip(-200, 100.4, 15), ["D", None]),
            (self.strip(-50.2, 100.4, 20), ["E", None])])
        areas = {entry[0]: entry[1] for entry in result["counts"] if entry}
        # 301.2 + 50.2 mp and 100.4 + 50.2 mp, where the rows add up to 350 and 150
        self.assertEqual(areas["Suprafață în intravilan"], "351 mp")
        self.assertEqual(areas["Suprafață în extravilan"], "151 mp")

    def test_a_text_longer_than_a_cell_holds_is_a_note_of_the_result(self):
        excel = os.path.join(self.tmp, "raport.xlsx")
        result = self.report([(self.strip(10, 100, 0), ["A", "x" * 40000])], excel)
        self.assertIn("2 celule din fișierul Excel au fost scurtate la 32.767 de caractere, cât "
                      "cuprinde o celulă Excel.", result["notes"])
        self.assertTrue(result["warning"])
        self.assertEqual(result["file_path"], excel)

    def questions(self, gpkg, excel=""):
        """The questions shown before a run that would write gpkg and excel; each of them
        is answered with no."""
        layer = helpers.polygon_layer("Imobile sector 1", [(self.strip(10, 100, 0), ["A"])],
                                      fields="cod:string")
        contour = helpers.polygon_layer("limita intravilan", [(square(0, -100, 1000), ["T"])],
                                        fields="nume:string")
        self.project.addMapLayers([layer, contour])
        dialog = IntravilanDialog(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        dialog.imobile.set_checked_ids([layer.id()])
        dialog.intravilan.set_checked_ids([contour.id()])
        dialog.gpkg_row.set_path(gpkg)
        dialog.excel_row.set_path(excel)
        asked = []
        dialog.confirm = lambda title, message, *args, **more: asked.append(message) or False
        dialog._run()
        self.assertIsNone(dialog._task)
        return asked

    def test_the_question_about_a_geopackage_with_other_layers_names_it_with_its_folder(self):
        # the reports of two projects often have the same file name
        gpkg = os.path.join(self.tmp, "sector 3", "raport.gpkg")
        os.makedirs(os.path.dirname(gpkg))
        helpers.gpkg_layer(helpers.polygon_layer("Limite", [(square(0, 0, 50), [1])]), gpkg)
        self.assertEqual(self.questions(gpkg), [
            f"Fișierul '{gpkg}' există deja. Raportul se scrie în el ca layerul "
            "'raport_intravilan', în locul celui cu același nume, dacă există; celelalte layere "
            "din fișier rămân neschimbate. Continuați?"])
        excel = os.path.join(self.tmp, "sector 3", "raport.xlsx")
        open(excel, "wb").close()
        self.assertEqual(self.questions(gpkg, excel), [
            f"Fișierele '{excel}' și '{gpkg}' există deja. Raportul Excel se înlocuiește, iar "
            "în GeoPackage raportul se scrie ca layerul 'raport_intravilan', în locul celui cu "
            "același nume, dacă există; celelalte layere din fișier rămân neschimbate. "
            "Continuați?"])


def tearDownModule():
    settings_service.reset_all()
    QgsProject.instance().clear()
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
