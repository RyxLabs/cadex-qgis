# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Verificare Topologică and Topologie Construcții windows with a fake iface: what they
show after a run and what they hand to their task. Needs a GUI QgsApplication; the offscreen
platform works."""

import tempfile
import unittest

import helpers
import synthetic

helpers.start_qgis(gui=True)

from qgis.core import QgsFeature, QgsProject  # noqa: E402  # type: ignore

from cadex.dialogs.building_topology_dialog import BuildingTopologyDialog  # noqa: E402
from cadex.dialogs.topology_check_dialog import CGXML, TopologyCheckDialog  # noqa: E402
from cadex.services import building_topology_service as bts  # noqa: E402
from cadex.services import settings_service  # noqa: E402
from cadex.services import topology_check_service as tcs  # noqa: E402
from cadex.services.layer_sources import LayerSnapshot  # noqa: E402


box = helpers.box_in_romania


class WindowTest(unittest.TestCase):

    def setUp(self):
        settings_service.reset_all()
        self.iface = helpers.fake_iface()
        self.project = QgsProject.instance()
        self.project.clear()

    def tearDown(self):
        self.project.clear()

    def window(self, kind):
        dialog = kind(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def shown(self, dialog, task):
        """What the window gives the result window for a finished task."""
        self.assertTrue(task.run(), task.error_msg)
        shown = []
        dialog.show_result = lambda headline, **kw: shown.append(dict(kw, headline=headline))
        dialog._on_finished(True, task)
        return shown[0]


class TopologyCheckWindowTests(WindowTest):
    """Sector 1 west of x = 250 and sector 2 east of it."""

    def test_a_hole_between_two_sectors_is_listed_with_warning_severity(self):
        # a 20 x 20 m hole across the line between the sectors
        imobile = helpers.polygon_layer("Imobile", [
            (box(0, 0, 240, 100), ["A", 1]), (box(260, 0, 500, 100), ["B", 2]),
            (box(240, 0, 250, 40), ["C", 1]), (box(250, 0, 260, 40), ["D", 2]),
            (box(240, 60, 250, 100), ["E", 1]), (box(250, 60, 260, 100), ["F", 2]),
        ], fields="id:string,sector:integer")
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector")
        result = self.shown(self.window(TopologyCheckDialog), task)
        self.assertEqual([(kind, count) for kind, count, _color in result["counts"]],
                         [(tcs.GAP, 1)])
        [layer] = result["layers"]
        self.assertEqual([(f["gravitate"], f["sector"], f["detalii"])
                          for f in layer.getFeatures()],
                         [(tcs.WARNING, "1/2", "Gol de 400 mp între sectoarele 1 și 2, mărginit "
                                               "de imobilele A, B, C, D, E, F.")])
        # nothing in the result is an error, so the result window is not marked as a warning
        self.assertFalse(result["warning"])

    def test_an_unnumbered_outline_with_no_imobil_in_it_is_named_in_a_note(self):
        imobile = helpers.polygon_layer("Imobile", [(box(0, 0, 250, 100), ["A", 1])],
                                        fields="id:string,sector:integer")
        limits = helpers.polygon_layer("Sectoare", [(box(0, 0, 250, 100), [1]),
                                                    (box(250, 0, 500, 100), [2])])
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector", limits=LayerSnapshot(limits))
        result = self.shown(self.window(TopologyCheckDialog), task)
        self.assertEqual(result["counts"], [])
        self.assertIn("O limită de sector nu cuprinde niciun imobil și nu a fost verificată.",
                      result["notes"])
        self.assertTrue(result["warning"])

    def started(self, dialog):
        """The task the window starts when Verifică is pressed. On CGXML files the task starts
        once the files are counted, so it is waited for."""
        dialog.excel_row.edit.setText("")
        [task] = helpers.tasks_started_by_button(dialog)
        dialog.hide()
        return task

    def test_a_source_with_no_imobil_to_check_is_shown_as_nothing_checked(self):
        # a layer whose only feature has no geometry
        layer = helpers.polygon_layer("Imobile", [], fields="id:string")
        feature = QgsFeature(layer.fields())
        feature.setAttributes(["A"])
        layer.dataProvider().addFeatures([feature])
        self.project.addMapLayer(layer)
        from_layers = self.window(TopologyCheckDialog)
        from_layers.source_mode.set_index(0)
        from_layers.imobile.set_checked_ids([layer.id()])
        # two CGXML files without coordinates, as an eTerra export holds
        folder = tempfile.mkdtemp(prefix="cadex_topo_window_")
        self.addCleanup(helpers.remove_folder, folder)
        for ie in ("70001", "70002"):
            synthetic.write(folder, f"{ie}.cgxml",
                            synthetic.Land(rings=[], ie=ie, style="eterra"))
        from_files = self.window(TopologyCheckDialog)
        from_files.source_mode.set_index(CGXML)
        from_files.cgxml.edit.setText(folder)
        for dialog, left_out in (
                (from_layers, "1 element fără geometrie nu a fost verificat."),
                (from_files, "2 înregistrări fără coordonate nu au fost verificate.")):
            result = self.shown(dialog, self.started(dialog))
            self.assertEqual(result["headline"], "S-au verificat 0 imobile, cu 0 construcții.")
            self.assertEqual(result["notes"],
                             ["Niciun imobil nu a putut fi verificat.", left_out])
            self.assertTrue(result["warning"])


class BuildingTopologyWindowTests(WindowTest):
    """Imobil 1 from x 0 to 20 with a construcție that reaches 5 mm into imobil 2."""

    def setUp(self):
        super().setUp()
        self.imobile = helpers.polygon_layer("Imobile", [
            (box(0, 0, 20, 20), ["1"]), (box(20, 0, 40, 20), ["2"])], fields="id:string")
        self.constructii = helpers.polygon_layer(
            "Construcții", [(box(15, 5, 20.005, 10), ["C1"])], fields="nr:string")
        self.project.addMapLayers([self.imobile, self.constructii])

    def state_after_a_run(self, dialog):
        dialog.imobile.set_checked_ids([self.imobile.id()])
        dialog.constructii.set_checked_ids([self.constructii.id()])
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        [task] = started
        self.assertTrue(task.run(), task.error_msg)
        return task.results[0][0]["stare"]

    def test_topologie_constructii_opens_with_the_building_tolerance_of_verificare_topologica(self):
        cut = self.window(BuildingTopologyDialog)
        check = self.window(TopologyCheckDialog)
        self.assertEqual(cut.tolerance.value(), check.building_tolerance.value())
        self.assertEqual(self.state_after_a_run(cut), bts.UNCHANGED)

    def test_with_the_tolerance_set_to_zero_the_same_building_is_cut(self):
        cut = self.window(BuildingTopologyDialog)
        cut.tolerance.setValue(0.0)
        self.assertEqual(self.state_after_a_run(cut), bts.FIXED)


class BuildingLinkTests(WindowTest):
    """'Imobilul construcției' in both windows, with layers written by Conversie CGXML: the
    column that conversion wrote is preselected until the user picks another entry."""

    WINDOWS = (TopologyCheckDialog, BuildingTopologyDialog)

    def setUp(self):
        super().setUp()
        self.imobile = helpers.converted_layer("Imobile A", "20250101", "id:string",
                                               [(box(0, 0, 40, 40), ["1"])], in_project=True)
        self.constructii = helpers.converted_layer(
            "Construcții A", "20250101", "id:string,nr_constr:string",
            [(box(5, 5, 10, 10), ["1", "C1"])], in_project=True)

    def run_by_overlap(self, dialog):
        """Pick '(după suprapunere)' as a click in the list does, then press the run
        button."""
        self.assertEqual(dialog.link_combo.currentData(), "id")
        dialog.link_combo.setCurrentIndex(0)
        dialog.link_combo.activated.emit(0)
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        self.assertEqual(len(started), 1)

    def test_the_link_by_overlap_picked_by_hand_is_picked_again_in_the_next_window(self):
        for build in self.WINDOWS:
            with self.subTest(window=build.__name__):
                self.run_by_overlap(self.window(build))
                later = self.window(build)
                self.assertEqual(later.link_combo.currentData(), "")
                # the list of construcții layers changes: unticked, then ticked again
                later.constructii.set_checked_ids([])
                later.constructii.set_checked_ids([self.constructii.id()])
                self.assertEqual(later.link_combo.currentData(), "")
                # and the next run keeps it as the user's choice
                started = []
                later.start_task = lambda task, message="": started.append(task)
                later._run()
                self.assertEqual(len(started), 1)
                self.assertEqual(self.window(build).link_combo.currentData(), "")

    def test_a_window_without_a_choice_of_the_user_starts_on_the_column_of_the_conversion(self):
        for build in self.WINDOWS:
            with self.subTest(window=build.__name__):
                first = self.window(build)
                self.assertEqual(first.link_combo.currentData(), "id")
                started = []
                first.start_task = lambda task, message="": started.append(task)
                first._run()
                self.assertEqual(len(started), 1)
                self.assertEqual(self.window(build).link_combo.currentData(), "id")


if __name__ == "__main__":
    helpers.main()
