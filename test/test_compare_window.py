# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Comparator window next to the Processing algorithms: the same layers and files give
the same result from both, and the result window shows how the sources were compared."""

import functools
import os
import tempfile
import unittest

import helpers
import synthetic
from synthetic import Land

helpers.start_qgis(gui=True)
helpers.start_processing()

from qgis.core import QgsProject  # noqa: E402  # type: ignore
import processing  # noqa: E402  # type: ignore

from cadex.dialogs.compare_dialog import CompareDialog  # noqa: E402
from cadex.services import cgxml_compare, settings_service  # noqa: E402

X, Y = helpers.ORIGIN
layer = functools.partial(helpers.row_layer, in_project=True)
converted = functools.partial(helpers.converted_layer, in_project=True)


class WindowTest(unittest.TestCase):

    def setUp(self):
        settings_service.reset_all()
        QgsProject.instance().clear()
        self.addCleanup(QgsProject.instance().clear)
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_win_")
        self.addCleanup(helpers.remove_folder, self.tmp)
        self.shown = []

    def window(self):
        dialog = CompareDialog(iface=helpers.fake_iface())
        self.addCleanup(dialog.deleteLater)
        dialog.show_result = lambda headline, **kw: self.shown.append((headline, kw))
        dialog.excel_row.edit.setText("")
        return dialog

    def run_window(self, dialog):
        """The task the Run button starts, run to its end."""
        tasks = []
        dialog.start_task = lambda task, message="": tasks.append(task)
        dialog._run()
        [task] = tasks
        self.assertTrue(task.run(), task.error_msg)
        return task

    @staticmethod
    def counts(task):
        return (len(task.added), len(task.deleted), len(task.changed_keys()),
                task.unchanged_count())

    @staticmethod
    def processing_counts(result, prefix=""):
        return tuple(result[prefix + name] for name in ("ADDED", "DELETED", "CHANGED",
                                                        "UNCHANGED"))


class LayerPlanTests(WindowTest):
    """Two layers left as the window preselects them, and the same two layers given to
    comparelayers with every other parameter left empty."""

    def both(self, old, new, **parameters):
        """(the window's task, the result of comparelayers)"""
        dialog = self.window()
        dialog.old_combo.setLayer(old)
        dialog.new_combo.setLayer(new)
        task = self.run_window(dialog)
        result = processing.run("cadex:comparelayers", dict({"OLD": old, "NEW": new},
                                                            **parameters),
                                feedback=helpers.feedback())
        return task, result

    def test_plain_layers_pair_on_the_same_column_in_the_window_and_in_processing(self):
        fields = "id:integer,ie:integer,propr:string"
        # only the third imobil has an ID, all have an IE
        old = layer("Parcele v1", fields,
                    [[None, 70001, "A"], [None, 70002, "B"], [3, 70003, "C"]])
        new = layer("Parcele v2", fields,
                    [[None, 70001, "A"], [None, 70002, "B2"], [3, 70003, "C"]])
        task, result = self.both(old, new)
        self.assertEqual((task.key_old, task.key_new), ("ie", "ie"))
        self.assertEqual(self.counts(task), (0, 0, 1, 2))
        self.assertEqual(self.processing_counts(result), self.counts(task))

    def test_two_conversions_compare_the_same_columns_in_the_window_and_in_processing(self):
        fields = "id:integer,ie:integer,sector:integer,propr:string,note:string"
        old = converted("Imobile livrare_1", "20260101", fields,
                        [[501, 70001, 3, "A", "x"], [502, None, 3, "B", "x"]])
        new = converted("Imobile livrare_2", "20260201", fields,
                        [[501, 70001, 3, "A", "y"], [502, None, 3, "B2", "y"]])
        task, result = self.both(old, new)
        self.assertEqual((task.key_old, task.fields), ("id", ["sector", "propr"]))
        self.assertEqual(self.counts(task), (0, 0, 1, 1))
        self.assertEqual(self.processing_counts(result), self.counts(task))

    def test_the_constructions_of_two_exports_pair_the_same_in_the_window_and_in_processing(self):
        imobile = "id:integer,ie:integer,sector:integer,propr:string"
        buildings = "id:integer,ie_imobil:integer,nr_constr:string,destinatie:string"
        rows = [[None, 70001, None, "A"], [None, 70002, None, "B"], [900, 70003, None, "C"]]
        houses = [[None, 70001, "C1", "CL"], [None, 70002, "C1", "CL"], [900, 70003, "C1", "CL"]]
        old = converted("Imobile ianuarie", "20260101", imobile, rows)
        old_houses = converted("Construcții ianuarie", "20260101", buildings, houses)
        new = converted("Imobile martie", "20260301", imobile, rows)
        new_houses = converted("Construcții martie", "20260301", buildings,
                               [[None, 70001, "C1", "CA"]] + houses[1:])
        task, result = self.both(old, new, OLD_BUILDINGS=old_houses, NEW_BUILDINGS=new_houses)
        self.assertEqual((task.key_old, task.buildings.key_old),
                         ("ie", ["ie_imobil", "nr_constr"]))
        self.assertEqual(self.counts(task.buildings), (0, 0, 1, 2))
        self.assertEqual(self.processing_counts(result, "BUILDINGS_"),
                         self.counts(task.buildings))

    def test_two_deliveries_without_ids_pair_their_constructions_by_the_ie_too(self):
        imobile = "id:integer,ie:integer,sector:integer"
        buildings = "id:integer,ie_imobil:integer,nr_constr:string,destinatie:string"
        old = converted("Imobile v1", "20260101", imobile, [[None, 70001, 3]])
        converted("Construcții v1", "20260101", buildings, [[None, 70001, "C1", "CL"]])
        new = converted("Imobile v2", "20260201", imobile, [[None, 70001, 3]])
        converted("Construcții v2", "20260201", buildings, [[None, 70001, "C1", "CA"]])
        dialog = self.window()
        dialog.old_combo.setLayer(old)
        dialog.new_combo.setLayer(new)
        self.assertTrue(dialog.buildings_box.isChecked())
        task = self.run_window(dialog)
        self.assertEqual(task.buildings.key_new, ["ie_imobil", "nr_constr"])
        self.assertEqual(self.counts(task.buildings), (0, 0, 1, 0))

    def test_buildings_paired_on_their_number_alone_report_a_move_to_another_imobil(self):
        imobile = "id:integer,ie:integer,sector:integer"
        buildings = "id:integer,nr_constr:string,destinatie:string"
        rows = [[501, 70001, 3], [502, 70002, 3]]
        old = converted("Imobile v1", "20260101", imobile, rows)
        converted("Construcții v1", "20260101", buildings, [[501, "C1", "CL"], [502, "C2", "CL"]])
        new = converted("Imobile v2", "20260201", imobile, rows)
        # C2 is now a construcție of imobil 501
        converted("Construcții v2", "20260201", buildings, [[501, "C1", "CL"], [501, "C2", "CL"]])
        dialog = self.window()
        dialog.old_combo.setLayer(old)
        dialog.new_combo.setLayer(new)
        # the conversion's imobil and number columns pair the buildings, so only destinatie is
        # compared
        self.assertEqual(dialog.building_fields.texts(), ["destinatie"])
        for side in ("old", "new"):
            imobil = dialog.building_keys[side][0]
            # pick "(fără)" the way a click in the list does
            imobil.setCurrentIndex(0)
            imobil.activated.emit(0)
        self.assertEqual(dialog.building_fields.texts(), ["id", "destinatie"])
        dialog.building_fields.set_all(True)
        task = self.run_window(dialog)
        self.assertEqual((task.buildings.key_old, task.buildings.fields),
                         (["nr_constr"], ["id", "destinatie"]))
        self.assertEqual(self.counts(task.buildings), (0, 0, 1, 1))


class ResultTests(WindowTest):
    """What the result window shows about how the sources were compared."""

    def files(self):
        """A delivery of two imobile, the second without an IE, and the eTerra export of the
        first."""
        eterra, livrare = (os.path.join(self.tmp, name) for name in ("eterra", "livrare"))
        ring = synthetic.square(500000, 400000, 20)
        synthetic.write(eterra, "70001.cgxml", Land(rings=[ring], ie="70001", style="eterra"))
        synthetic.write(livrare, "501.cgxml", Land(rings=[ring], cadgenno="501", ie="70001",
                                                   sector="3"))
        synthetic.write(livrare, "502.cgxml", Land(
            rings=[synthetic.square(500100, 400000, 20)], cadgenno="502", sector="3"))
        return eterra, livrare

    def compare_files(self, old, new, mode):
        dialog = self.window()
        dialog.mode.set_index(1)
        dialog.old_source.set_paths([old])
        dialog.new_source.set_paths([new])
        helpers.count_files(dialog.old_source, dialog.new_source)
        dialog.match_combo.setCurrentIndex(dialog.match_combo.findData(mode))
        task = self.run_window(dialog)
        dialog._on_cgxml_finished(True, task)
        return task, self.shown[-1][1]["notes"]

    def test_the_result_names_the_source_recognised_as_the_eterra_export(self):
        eterra, livrare = self.files()
        for mode in cgxml_compare.MODES:
            task, notes = self.compare_files(livrare, eterra, mode)
            self.assertEqual((task.matched, task.changed), (1, []), mode)
            self.assertIn("Sursa nouă a fost recunoscută ca export eTerra, după sectorul "
                          "cadastral necompletat al imobilelor ei.", notes, mode)

    def test_a_delivery_compared_with_itself_by_ie_names_the_files_found_otherwise(self):
        _eterra, livrare = self.files()
        task, notes = self.compare_files(livrare, livrare, cgxml_compare.IE)
        self.assertEqual((task.matched, task.unchanged, task.added, task.ignored), (2, 2, [], []))
        self.assertIn("1 imobil fără pereche după IE a fost găsit după numărul cadastral sau "
                      "după numele fișierului.", notes)
        self.assertFalse(any("eTerra" in note for note in notes), notes)

    def test_points_compared_by_distance_are_named_in_a_note(self):
        def points(name, east):
            return layer(name, "id:integer", [(f"POINT({X} {Y})", [1]),
                                              (f"POINT({X + 10 + east} {Y})", [2])],
                         geometry="Point")
        dialog = self.window()
        dialog.mode.set_index(0)
        dialog.old_combo.setLayer(points("Borne v1", 0))
        dialog.new_combo.setLayer(points("Borne v2", 0.2))
        task = self.run_window(dialog)
        self.assertEqual(self.counts(task), (0, 0, 1, 1))
        dialog._on_layers_finished(True, task)
        notes = self.shown[-1][1]["notes"]
        self.assertIn("Layerele 'Borne v1' și 'Borne v2' nu sunt amândouă de poligoane, deci "
                      "geometria s-a comparat după distanță: este mutată când un punct al ei se "
                      "află la peste 1 mm de cealaltă versiune.", notes)
        changed = next(found for found in self.shown[-1][1]["layers"]
                       if found.name().startswith("Modificate"))
        self.assertAlmostEqual(next(changed.getFeatures())["dist_mutata"], 0.2, places=3)


def tearDownModule():
    settings_service.reset_all()
    QgsProject.instance().clear()
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
