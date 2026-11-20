# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The two Comparator algorithms through processing.run: what counts as the same record and
the same value, and what a run takes for the parameters left empty."""

import os
import tempfile
import unittest

import helpers
import synthetic
from synthetic import Land

helpers.start_qgis(gui=False)
helpers.start_processing()

from qgis.core import QgsProcessingException, QgsProject  # noqa: E402  # type: ignore
from qgis.PyQt.QtCore import QDate, QDateTime, QTime  # noqa: E402  # type: ignore
import processing  # noqa: E402  # type: ignore

from cadex.services import xlsx  # noqa: E402

X, Y = helpers.ORIGIN
layer = helpers.row_layer
converted = helpers.converted_layer


def run(name, parameters, feedback=None):
    return processing.run(f"cadex:{name}", parameters, feedback=feedback or helpers.feedback())


def keyed(old, new, feedback=None, **parameters):
    """comparelayers of two layers on their id column."""
    return run("comparelayers", dict({"OLD": old, "OLD_KEY": "id", "NEW": new, "NEW_KEY": "id",
                                      "OUTPUT_CHANGED": "TEMPORARY_OUTPUT"}, **parameters),
               feedback)


def changed(result, field="modificari"):
    return {feature["id"]: feature[field] for feature in result["OUTPUT_CHANGED"].getFeatures()}


class LayerValueTests(unittest.TestCase):
    """What the layer comparison takes for the same value."""

    def tearDown(self):
        QgsProject.instance().clear()

    def test_a_long_number_that_differs_in_its_last_digit_is_a_change(self):
        # a personal code and a company code kept in whole-number columns
        old = layer("v1", "id:integer,cnp:long,cui:long",
                    [[1, 1850101123456, 12345678], [2, 2900202123456, 4012345678],
                     [3, 1700303123456, 123456]])
        new = layer("v2", "id:integer,cnp:long,cui:long",
                    [[1, 1850101123457, 12345678], [2, 2900202123456, 4012345679],
                     [3, 1700303123456, 123456]])
        result = keyed(old, new)
        self.assertEqual((result["CHANGED"], result["UNCHANGED"]), (2, 1))
        self.assertEqual(changed(result), {1: "cnp", 2: "cui"})

    def test_a_number_is_the_same_whole_real_or_written_as_text(self):
        old = layer("v1", "id:integer,nr:integer,sup:double,cod:long",
                    [[1, 12, 1250.5, 1850101123456], [2, 7, 400.0, 12345678]])
        new = layer("v2", "id:integer,nr:double,sup:string,cod:string",
                    [[1, 12.0, "1250,5", "1850101123456"], [2, 7.0, "400", "12345679"]])
        result = keyed(old, new)
        self.assertEqual(changed(result), {2: "cod"})

    def test_a_date_is_the_same_in_a_date_column_and_written_as_text(self):
        day, moment = QDate(2024, 3, 1), QDateTime(QDate(2024, 3, 1), QTime(10, 30, 0))
        old = layer("gpkg", "id:integer,data:date,moment:datetime,ora:time",
                    [[1, day, moment, QTime(8, 15, 0)], [2, day, moment, QTime(8, 15, 0)],
                     [3, day, QDateTime(day, QTime(0, 0, 0)), None]])
        # a Shapefile keeps a date and time as text, and people write dates the Romanian way
        new = layer("shp", "id:integer,data:string,moment:string,ora:string",
                    [[1, "01.03.2024", "2024/03/01 10:30:00.000", "08:15"],
                     [2, "2024-03-02", "2024-03-01T10:30:00", "08:15:00"],
                     [3, "2024/03/01", "2024-03-01", None]])
        result = keyed(old, new)
        self.assertEqual(changed(result), {2: "data"})

    def test_a_date_and_a_date_with_time_are_the_same_at_midnight(self):
        day = QDate(2024, 3, 1)
        old = layer("v1", "id:integer,data:date", [[1, day], [2, day]])
        new = layer("v2", "id:integer,data:datetime",
                    [[1, QDateTime(day, QTime(0, 0, 0))], [2, QDateTime(day, QTime(9, 0, 0))]])
        self.assertEqual(changed(keyed(old, new)), {2: "data"})

    def test_s_and_t_with_a_cedilla_are_the_letters_with_a_comma_below(self):
        old = layer("v1", "id:integer,propr:string",
                    [[1, "ŞTEFAN ŢUŢEA"], [2, "Ştefan"], [3, "Ştefan"]])
        new = layer("v2", "id:integer,propr:string",
                    [[1, "ȘTEFAN ȚUȚEA"], [2, "ȘTEFAN"], [3, "Stefan"]])
        # letter case and a missing diacritic are differences unless the option is on
        self.assertEqual(changed(keyed(old, new)), {2: "propr", 3: "propr"})
        self.assertEqual(keyed(old, new, FOLD_TEXT=True)["CHANGED"], 0)


class LayerKeyTests(unittest.TestCase):
    """Which records of two layers are the same record."""

    def tearDown(self):
        QgsProject.instance().clear()

    def test_keys_that_differ_only_in_letter_case_are_the_same_record(self):
        old = layer("v1", "id:string,propr:string",
                    [["13a", "A"], ["0014", "B"], ["Lot Ş1", "C"], ["15", "D"]])
        new = layer("v2", "id:string,propr:string",
                    [["13A", "A2"], ["14", "B"], ["LOT Ș1", "C"], [" 15 ", "D"]])
        result = keyed(old, new, OUTPUT_ADDED="TEMPORARY_OUTPUT",
                       OUTPUT_DELETED="TEMPORARY_OUTPUT")
        self.assertEqual(changed(result), {"13A": "propr"})
        self.assertEqual(result["UNCHANGED"], 2)
        # a leading zero is part of the key
        self.assertEqual([f["id"] for f in result["OUTPUT_ADDED"].getFeatures()], ["14"])
        self.assertEqual([f["id"] for f in result["OUTPUT_DELETED"].getFeatures()], ["0014"])

    def test_two_spellings_of_a_key_in_one_layer_are_a_repeated_key(self):
        old = layer("v1", "id:string,propr:string", [["a1", "A"], ["A1", "B"]])
        new = layer("v2", "id:string,propr:string", [["A1", "A"]])
        feedback = helpers.feedback()
        result = run("comparelayers", {"OLD": old, "OLD_KEY": "id", "NEW": new,
                                       "NEW_KEY": "id"}, feedback)
        self.assertEqual((result["UNCHANGED"], result["ADDED"], result["DELETED"]), (1, 0, 0))
        self.assertTrue(any("a1 apare de 2 ori" in warning for warning in feedback.warnings),
                        feedback.warnings)

    def test_a_nan_or_infinite_key_counts_as_no_key(self):
        old = layer("v1", "id:double,propr:string",
                    [[1.0, "A"], [float("nan"), "B"], [float("inf"), "C"]])
        new = layer("v2", "id:double,propr:string", [[1.0, "A2"]])
        feedback = helpers.feedback()
        result = run("comparelayers", {"OLD": old, "OLD_KEY": "id", "NEW": new,
                                       "NEW_KEY": "id"}, feedback)
        self.assertEqual((result["CHANGED"], result["DELETED"]), (1, 0))
        self.assertIn("2 rânduri fără valoare în coloana cheie au fost ignorate.",
                      feedback.warnings)


class PointAndLineTests(unittest.TestCase):
    """The geometry of layers that hold no polygons."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_dist_")
        self.addCleanup(helpers.remove_folder, self.tmp)
        self.addCleanup(QgsProject.instance().clear)

    def points(self, name, moved):
        """Three boundary points; the second lies moved metres to the east."""
        return layer(name, "id:integer,cod:string", [
            (f"POINT({X} {Y})", [1, "a"]), (f"POINT({X + 10 + moved} {Y})", [2, "b"]),
            (f"POINT({X + 20} {Y})", [3, "c"])], geometry="Point")

    def test_a_moved_point_is_a_change_with_the_distance_it_moved(self):
        report = os.path.join(self.tmp, "puncte.xlsx")
        feedback = helpers.feedback()
        result = keyed(self.points("v1", 0), self.points("v2", 0.05), OUTPUT_XLSX=report,
                       feedback=feedback)
        self.assertEqual((result["CHANGED"], result["UNCHANGED"]), (1, 2))
        feature = next(result["OUTPUT_CHANGED"].getFeatures())
        self.assertEqual((feature["id"], feature["modificari"], feature["tip_modif"]),
                         (2, "geometrie", "Doar limita"))
        self.assertAlmostEqual(feature["dist_mutata"], 0.05, places=3)
        facts = helpers.summary_facts(report)
        self.assertEqual(facts["Geometrie comparată"],
                         "Da, după distanță, cu toleranța 1 mm (puncte și linii)")
        _names, rows = xlsx.read_rows(report, "Modificate")
        self.assertEqual(rows[0][2:], ["Coloana", "Valoare veche", "Valoare nouă",
                                       "Distanță (m)"])
        self.assertEqual(rows[1][2], "Geometrie (distanța, m)")
        self.assertAlmostEqual(rows[1][5], 0.05, places=3)
        self.assertTrue(any("geometria s-a comparat după distanță" in text
                            for text in feedback.infos), feedback.infos)

    def test_a_point_within_a_millimetre_of_its_place_has_not_moved(self):
        result = keyed(self.points("v1", 0), self.points("v2", 0.0004))
        self.assertEqual((result["CHANGED"], result["UNCHANGED"]), (0, 3))

    def test_a_moved_line_is_a_change(self):
        def lines(name, north):
            return layer(name, "id:integer,cod:string", [
                (f"LINESTRING({X} {Y}, {X + 100} {Y})", [1, "a"]),
                (f"LINESTRING({X} {Y + 10 + north}, {X + 100} {Y + 10 + north})", [2, "b"])],
                geometry="LineString")
        result = keyed(lines("v1", 0), lines("v2", 0.5))
        self.assertEqual(changed(result, "dist_mutata"), {2: 0.5})

    def test_a_point_that_lost_its_place_is_a_change(self):
        new = layer("v2", "id:integer,cod:string", [
            (f"POINT({X} {Y})", [1, "a"]), (None, [2, "b"]), (f"POINT({X + 20} {Y})", [3, "c"])],
            geometry="Point")
        result = keyed(self.points("v1", 0), new)
        self.assertEqual(changed(result), {2: "geometrie"})

    def test_polygons_are_compared_by_the_area_that_moved(self):
        old = layer("v1", "id:integer", [[1], [2]])
        new = layer("v2", "id:integer", [[1], (helpers.square_in_romania(30.5, 0, 10), [2])])
        report = os.path.join(self.tmp, "poligoane.xlsx")
        result = keyed(old, new, OUTPUT_XLSX=report)
        self.assertEqual(changed(result, "sup_mutata"), {2: 10.0})
        self.assertEqual(helpers.summary_facts(report)["Geometrie comparată"], "Da, toleranță 0,1 mp")


class EmptyParameterTests(unittest.TestCase):
    """What comparelayers takes for the parameters left empty."""

    FIELDS = "id:integer,ie:integer,propr:string"

    def tearDown(self):
        QgsProject.instance().clear()

    def plain(self, name, owner):
        """Three imobile; only the third has an ID, all have an IE."""
        return layer(name, self.FIELDS,
                     [[None, 70001, "A"], [None, 70002, owner], [3, 70003, "C"]])

    def test_with_no_key_chosen_the_layers_pair_on_the_column_with_fewest_empty_values(self):
        result = run("comparelayers", {"OLD": self.plain("v1", "B"), "NEW": self.plain("v2", "B2"),
                                       "OUTPUT_CHANGED": "TEMPORARY_OUTPUT"})
        self.assertEqual((result["CHANGED"], result["UNCHANGED"]), (1, 2))
        self.assertEqual([f["ie"] for f in result["OUTPUT_CHANGED"].getFeatures()], [70002])

    def test_a_key_chosen_for_one_layer_is_the_key_of_the_other_too(self):
        result = run("comparelayers", {"OLD": self.plain("v1", "B"), "OLD_KEY": "id",
                                       "NEW": self.plain("v2", "B2")}, helpers.feedback())
        # on the ID only the third imobil has a key
        self.assertEqual((result["CHANGED"], result["UNCHANGED"]), (0, 1))

    def test_the_constructions_of_two_exports_pair_by_the_ie_of_their_imobil(self):
        imobile = "id:integer,ie:integer,sector:integer,propr:string"
        buildings = "id:integer,ie_imobil:integer,nr_constr:string,destinatie:string"
        rows = [[None, 70001, None, "A"], [None, 70002, None, "B"], [900, 70003, None, "C"]]
        old = converted("Imobile ianuarie", "20260101", imobile, rows)
        new = converted("Imobile martie", "20260301", imobile, rows)
        houses = [[None, 70001, "C1", "CL"], [None, 70002, "C1", "CL"], [900, 70003, "C1", "CL"]]
        old_houses = converted("Construcții ianuarie", "20260101", buildings, houses)
        new_houses = converted("Construcții martie", "20260301", buildings,
                               [[None, 70001, "C1", "CA"]] + houses[1:])
        feedback = helpers.feedback()
        result = run("comparelayers", {"OLD": old, "NEW": new, "OLD_BUILDINGS": old_houses,
                                       "NEW_BUILDINGS": new_houses}, feedback)
        self.assertEqual((result["UNCHANGED"], result["BUILDINGS_CHANGED"],
                          result["BUILDINGS_UNCHANGED"]), (3, 1, 2))
        self.assertEqual(feedback.warnings, [])

    def test_the_boundary_alone_can_be_compared(self):
        old = layer("v1", "id:integer,propr:string", [[1, "A"], [2, "B"]])
        new = layer("v2", "id:integer,propr:string",
                    [[1, "A2"], (helpers.square_in_romania(30.5, 0, 10), [2, "B2"])])
        result = run("comparelayers", {"OLD": old, "NEW": new, "COMPARE_FIELDS": False,
                                       "OUTPUT_CHANGED": "TEMPORARY_OUTPUT"})
        self.assertEqual((result["CHANGED"], result["UNCHANGED"]), (1, 1))
        self.assertEqual(changed(result), {2: "geometrie"})
        with self.assertRaises(QgsProcessingException) as raised:
            run("comparelayers", {"OLD": old, "NEW": new, "COMPARE_FIELDS": False,
                                  "COMPARE_GEOMETRY": False})
        self.assertIn("Alegeți ce se compară: coloanele, geometria sau amândouă.",
                      str(raised.exception))


class FileSourceTests(unittest.TestCase):
    """comparecgxml on a delivery and an eTerra export of two imobile: the deed area is only
    in eTerra, the sector only in the delivery, and the owner is typed in another case."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_files_")
        self.addCleanup(helpers.remove_folder, self.tmp)
        self.eterra, self.livrare = (os.path.join(self.tmp, n) for n in ("eterra", "livrare"))
        for number in (1, 2):
            ring = synthetic.square(500000 + 100 * number, 400000, 20)
            synthetic.write(self.eterra, f"{70000 + number}.cgxml", Land(
                rings=[ring], ie=str(70000 + number), legal_area=450.0, style="eterra"))
            synthetic.write(self.livrare, f"{500 + number}.cgxml", Land(
                rings=[ring], cadgenno=str(500 + number), ie=str(70000 + number) if number == 1 else "",
                sector="3"))

    def test_a_delivery_compared_with_itself_is_unchanged_in_both_modes(self):
        # the second imobil of the delivery has no IE
        for mode in (0, 1):
            result = run("comparecgxml", {"OLD": [self.livrare], "NEW": [self.livrare],
                                          "MODE": mode})
            self.assertEqual((result["MATCHED"], result["UNCHANGED"], result["CHANGED"],
                              result["ONLY_OLD"], result["ONLY_NEW"], result["IGNORED"]),
                             (2, 2, 0, 0, 0, 0), mode)

    def test_an_eterra_export_is_recognised_and_named_in_a_warning_in_both_modes(self):
        for mode in (0, 1):
            feedback = helpers.feedback()
            result = run("comparecgxml", {"OLD": [self.livrare], "NEW": [self.eterra],
                                          "MODE": mode}, feedback)
            self.assertEqual((result["MATCHED"], result["CHANGED"], result["UNCHANGED"]),
                             (1, 0, 1), mode)
            self.assertIn("Sursa nouă a fost recunoscută ca export eTerra, după sectorul "
                          "cadastral necompletat al imobilelor ei.", feedback.warnings, mode)
            self.assertEqual(any("Potrivirea după IE urmărește și imobilele din eTerra "
                                 "dezmembrate." in text for text in feedback.warnings),
                             mode == 0)


def tearDownModule():
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
