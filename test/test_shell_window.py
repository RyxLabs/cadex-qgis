# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""What every tool window shares: values remembered for the profile or for the project, runs
that belong to their project, the palette, the keyboard, screen readers, and unloading. Needs
a GUI QgsApplication; the offscreen platform works."""

import os
import re
import tempfile
import threading
import time
import unittest
from unittest import mock

import helpers
import synthetic

helpers.start_qgis(gui=True)

from qgis.core import (  # noqa: E402  # type: ignore
    QgsApplication, QgsProject, QgsTask, QgsVectorLayer,
)
from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402  # type: ignore
from qgis.PyQt.QtGui import QColor, QGuiApplication, QPalette  # noqa: E402  # type: ignore
from qgis.PyQt.QtWidgets import (  # noqa: E402  # type: ignore
    QAbstractButton, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QLabel,
    QLineEdit, QTabWidget, QToolButton,
)
from qgis.gui import QgsMapLayerComboBox  # noqa: E402  # type: ignore

from cadex.cadex import Cadex  # noqa: E402
from cadex.dialogs import _ui_helpers, theme  # noqa: E402
from cadex.dialogs.building_topology_dialog import BuildingTopologyDialog  # noqa: E402
from cadex.dialogs.cgxml_dialog import CgxmlDialog  # noqa: E402
from cadex.dialogs.compare_dialog import CompareDialog  # noqa: E402
from cadex.dialogs.diminuare_dialog import DiminuareDialog  # noqa: E402
from cadex.dialogs.intravilan_dialog import IntravilanDialog  # noqa: E402
from cadex.dialogs.settings_dialog import SettingsDialog  # noqa: E402
from cadex.dialogs.topology_check_dialog import TopologyCheckDialog  # noqa: E402
from cadex.dialogs.widgets import (  # noqa: E402
    CgxmlSource, CheckList, PathRow, SegmentedControl, StatusLabel, user_chose,
)
from cadex.services import building_topology_service as bts  # noqa: E402
from cadex.services import (  # noqa: E402
    cgxml_export, cgxml_sources, dialog_memory, intravilan_service, roles, settings_service,
    tasks,
)
from cadex.services.tasks import CadexTask  # noqa: E402

TOOL_WINDOWS = (CgxmlDialog, IntravilanDialog, DiminuareDialog, CompareDialog,
                TopologyCheckDialog, BuildingTopologyDialog)


square = helpers.square_in_romania


def imobile_layer(name, fields="id:string,sector:integer", rows=((0, ["101", 1]),)):
    return helpers.polygon_layer(name, [(square(x, 0, 100), values) for x, values in rows],
                                 fields=fields)


class WindowTest(unittest.TestCase):

    def setUp(self):
        # registered first, so it runs after every other cleanup: the windows and toolbars a
        # test leaves for deletion are deleted as in the event loop of QGIS
        self.addCleanup(QCoreApplication.sendPostedEvents, None, QEvent.Type.DeferredDelete)
        settings_service.reset_all()
        self.iface = helpers.fake_iface()
        self.project = QgsProject.instance()
        self.project.clear()
        self.tmp = tempfile.mkdtemp(prefix="cadex_shell_")
        self.shown = []

    def tearDown(self):
        self.project.clear()
        helpers.remove_folder(self.tmp)

    settle = staticmethod(helpers.settle)
    wait = staticmethod(helpers.wait)

    def window(self, build, parent=None):
        dialog = build(iface=self.iface, parent=parent)
        self.addCleanup(dialog.deleteLater)
        dialog.confirm = lambda *args, **kwargs: True
        return dialog

    def plugin(self):
        plugin = Cadex(self.iface)
        plugin.initGui()
        # unload() sets provider to None; a test may unload the plugin itself
        self.addCleanup(lambda: plugin.provider is not None and plugin.unload())
        return plugin

    def result_windows(self):
        """Replace the result window with a recorder; returns the list of what each one would
        have shown."""
        def show(_parent, _title, headline, **kwargs):
            self.shown.append(dict(kwargs, headline=headline))
        patcher = mock.patch.object(_ui_helpers, "show_result", show)
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.shown

    def held_back(self, task_class):
        """Make the runs of task_class wait on their thread until the returned event is set,
        so the test can act while a run is in progress."""
        gate = threading.Event()
        run = task_class.run

        def waiting(task):
            gate.wait(20)
            return run(task)
        patcher = mock.patch.object(task_class, "run", waiting)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(gate.set)
        return gate

    def saved_project(self, name, *layers):
        """Clear the project, add layers and save it as <name>.qgz; returns the file."""
        self.project.clear()
        self.project.addMapLayers(list(layers))
        path = os.path.join(self.tmp, f"{name}.qgz")
        self.assertTrue(self.project.write(path))
        return path

    def open_project(self, path):
        self.assertTrue(self.project.read(path))

    def other_project(self, *layers):
        """What File > New or File > Open does to the project, then these layers in it."""
        self.project.clear()
        self.project.addMapLayers(list(layers))

    def layer_names(self):
        return sorted(layer.name() for layer in self.project.mapLayers().values())

    @staticmethod
    def bar_text(dialog):
        return " ".join(label.text() for item in dialog.message_bar.items()
                        for label in item.findChildren(QLabel) if label.text())


class RememberedColumnTests(WindowTest):
    """The columns of Verificare Topologică from one run to the next window."""

    def topology(self, layer=None):
        dialog = self.window(TopologyCheckDialog)
        dialog.excel_row.edit.setText("")
        dialog.started = []
        dialog.start_task = lambda task, message="": dialog.started.append(task)
        if layer is not None:
            dialog.source_mode.set_index(0)
            dialog.imobile.set_checked_ids([layer.id()])
            dialog.constructii.set_checked_ids([])
        return dialog

    def columns(self, dialog):
        return dialog.id_combo.currentData(), dialog.sector_combo.currentData()

    def test_column_boxes_left_empty_in_a_run_are_preselected_by_name_in_the_next_window(self):
        # a first check of CGXML files, in a project without layers
        files = self.topology()
        files.source_mode.set_index(1)
        files.cgxml.set_paths([helpers.DATA_DIR])
        files.cgxml.refresh()
        helpers.count_files(files.cgxml)
        files._run()
        self.assertEqual(len(files.started), 1, self.bar_text(files))
        delivery = imobile_layer("Imobile livrare")
        self.project.addMapLayer(delivery)
        later = self.topology(delivery)
        self.assertEqual(self.columns(later), ("id", "sector"))
        later._run()
        [task] = later.started
        self.assertEqual((task.id_field, task.sector_field), ("id", "sector"))

    def test_a_stored_text_of_an_empty_entry_does_not_override_the_preselection(self):
        # profile-wide settings that hold the text of the empty entries instead of a column
        # name
        dialog_memory.save("TopologyCheckDialog", "id_field", "(numărul elementului)")
        dialog_memory.save("TopologyCheckDialog", "sector_field", "(un singur sector)")
        delivery = imobile_layer("Imobile livrare")
        self.project.addMapLayer(delivery)
        dialog = self.topology(delivery)
        self.assertEqual(self.columns(dialog), ("id", "sector"))
        self.assertFalse(user_chose(dialog.sector_combo))

    def test_a_column_name_stored_for_the_whole_profile_is_chosen_when_the_layer_has_it(self):
        dialog_memory.save("TopologyCheckDialog", "sector_field", "nr_tarla")
        layer = imobile_layer("Imobile", "id:string,nr_tarla:integer,sector:integer",
                              ((0, ["101", 7, 1]),))
        self.project.addMapLayer(layer)
        dialog = self.topology(layer)
        self.assertEqual(dialog.sector_combo.currentData(), "nr_tarla")

    def pick(self, combo, data):
        combo.setCurrentIndex(combo.findData(data))
        combo.activated.emit(combo.currentIndex())

    def test_a_column_picked_by_hand_is_picked_again_in_a_later_window(self):
        layer = imobile_layer("Imobile", "id:string,nr_tarla:integer,sector:integer",
                              ((0, ["101", 7, 1]),))
        self.project.addMapLayer(layer)
        first = self.topology(layer)
        self.pick(first.sector_combo, "nr_tarla")
        self.pick(first.id_combo, "")
        first._run()
        later = self.topology(layer)
        self.assertEqual(self.columns(later), ("", "nr_tarla"))
        self.assertTrue(user_chose(later.id_combo))
        # and it holds when the layers change
        self.project.addMapLayer(imobile_layer("Drumuri"))
        self.assertEqual(self.columns(later), ("", "nr_tarla"))

    def test_a_column_picked_in_one_project_is_not_picked_in_another(self):
        fields = "id:string,nr_tarla:integer,sector:integer"
        first = self.saved_project("uat_a", imobile_layer("Imobile A", fields, ((0, ["1", 7, 1]),)))
        second = self.saved_project("uat_b",
                                    imobile_layer("Imobile B", fields, ((0, ["2", 8, 2]),)))
        self.open_project(first)
        [layer] = self.project.mapLayers().values()
        dialog = self.topology(layer)
        self.pick(dialog.sector_combo, "nr_tarla")
        dialog.save_remembered()
        self.open_project(second)
        [layer] = self.project.mapLayers().values()
        self.assertEqual(self.topology(layer).sector_combo.currentData(), "sector")
        self.open_project(first)
        [layer] = self.project.mapLayers().values()
        self.assertEqual(self.topology(layer).sector_combo.currentData(), "nr_tarla")


class RunProjectTests(WindowTest):
    """A run that ends after its project was closed."""

    def other_project_opened(self):
        self.other_project(helpers.polygon_layer("Parcele B", [(square(900, 0, 10), ["Z"])],
                                                 fields="ID:string"))

    def test_its_layers_stay_out_of_the_project_opened_meanwhile(self):
        imobile = imobile_layer("Imobile A")
        constructii = helpers.polygon_layer("Constructii A", [(square(90, 10, 20), ["C1"])],
                                            fields="nr:string")
        self.project.addMapLayers([imobile, constructii])
        shown = self.result_windows()
        gate = self.held_back(bts.BuildingTopologyTask)
        dialog = self.window(BuildingTopologyDialog)
        dialog._run()
        self.assertIsNotNone(dialog._task, self.bar_text(dialog))
        self.other_project_opened()
        gate.set()
        self.assertTrue(self.wait(lambda: dialog._task is None))
        self.assertEqual(self.layer_names(), ["Parcele B"])
        [result] = shown
        self.assertEqual(result["headline"], "S-a verificat 1 construcție.")
        self.assertEqual(result["notes"][0], "Proiectul s-a schimbat în timpul rulării. Layerul "
                                             "rezultat nu a fost adăugat în proiectul deschis acum.")
        self.assertFalse([note for note in result["notes"] if "Constructii A_corectat" in note],
                         result["notes"])
        self.assertEqual(list(result["layers"]), [])
        self.assertTrue(result["warning"])

    def test_a_run_whose_project_stays_adds_its_layers_and_has_no_note_on_the_project(self):
        imobile = imobile_layer("Imobile A")
        constructii = helpers.polygon_layer("Constructii A", [(square(90, 10, 20), ["C1"])],
                                            fields="nr:string")
        self.project.addMapLayers([imobile, constructii])
        shown = self.result_windows()
        dialog = self.window(BuildingTopologyDialog)
        dialog._run()
        self.assertTrue(self.wait(lambda: dialog._task is None))
        self.assertIn("Constructii A_corectat", self.layer_names())
        [result] = shown
        self.assertFalse([note for note in result["notes"] if "Proiectul" in note])
        self.assertEqual([layer.name() for layer in result["layers"]], ["Constructii A_corectat"])

    def test_the_files_it_wrote_stay(self):
        shown = self.result_windows()
        gate = self.held_back(cgxml_export.ConversionTask)
        dialog = self.window(CgxmlDialog)
        dialog.source.set_paths([helpers.DATA_DIR])
        dialog.source.refresh()
        helpers.count_files(dialog.source)
        dialog.output_row.set_path(self.tmp)
        dialog.base_edit.setText("conversie")
        dialog.add_to_project.setChecked(True)
        dialog._run()
        self.assertIsNotNone(dialog._task, self.bar_text(dialog))
        self.other_project_opened()
        gate.set()
        self.assertTrue(self.wait(lambda: dialog._task is None, 60))
        self.assertEqual(self.layer_names(), ["Parcele B"])
        written = os.path.join(self.tmp, "conversie.gpkg")
        self.assertTrue(os.path.isfile(written))
        # the file is complete: it carries the markers the other tools read
        table = cgxml_export.table_name(dialog.name_imobile.text())
        again = QgsVectorLayer(f"{written}|layername={table}", "din nou", "ogr")
        self.assertTrue(again.isValid())
        self.assertTrue(roles.batch_of(again))
        [result] = shown
        self.assertEqual(result["notes"][0], "Proiectul s-a schimbat în timpul rulării. Layerele "
                                             "rezultate nu au fost adăugate în proiectul deschis "
                                             "acum. Fișierele scrise au rămas la locul lor.")
        self.assertFalse([note for note in result["notes"]
                          if note.startswith(("Layer adăugat", "Layere adăugate"))],
                         result["notes"])
        self.assertEqual(result["file_path"], written)

    def test_a_report_layer_removed_for_the_run_is_not_put_back_into_the_project_opened_meanwhile(self):
        imobile = imobile_layer("Imobile A")
        contour = helpers.polygon_layer("limita intravilan", [(square(-10, -10, 150), ["T1"])],
                                        fields="nume:string")
        report = os.path.join(self.tmp, "raport.gpkg")
        previous = helpers.gpkg_layer(imobile, report, layer_name="raport_intravilan")
        previous.setName("Raport A")
        self.project.addMapLayers([imobile, contour, previous])
        gate = self.held_back(intravilan_service.IntravilanTask)
        dialog = self.window(IntravilanDialog)
        dialog.imobile.set_checked_ids([imobile.id()])
        dialog.intravilan.set_checked_ids([contour.id()])
        dialog.gpkg_row.set_path(report)
        dialog.excel_row.set_path("")
        notices = []
        dialog.notice = notices.append
        dialog._run()
        self.assertIsNotNone(dialog._task, self.bar_text(dialog))
        self.assertNotIn("Raport A", self.layer_names())
        self.other_project_opened()
        dialog.cancel_task()
        gate.set()
        self.assertTrue(self.wait(lambda: dialog._task is None))
        self.assertEqual(notices, ["Raportul a fost anulat."])
        self.assertEqual(self.layer_names(), ["Parcele B"])


class RunPressTests(WindowTest):
    """The run button of the windows that read CGXML files: Conversie CGXML, and Verificare
    Topologică and the Comparator on files. The files of a source are counted on another
    thread from the moment it is chosen, and a press of the button never waits for them."""

    KINDS = ("Conversie CGXML", "Verificare Topologică", "Comparator")
    # the progress line: what the window does and for how long
    LOOKING = re.compile(r"Se caută fișierele\.\.\.\s+\d+:\d\d")

    def setUp(self):
        super().setUp()
        self.source = self.delivery("livrare")

    def delivery(self, name, files=2):
        """A folder of the test's own with that many CGXML files."""
        folder = os.path.join(self.tmp, name)
        os.makedirs(folder, exist_ok=True)
        for n in range(1, files + 1):
            synthetic.write(folder, f"{n}.cgxml", synthetic.Land(
                rings=[synthetic.square(500000 + 50 * n, 400000, 20)], cadgenno=str(n)))
        return folder

    def on_files(self, kind, path=None):
        """(the window of that kind, shown, with path as its CGXML source; its source fields;
        the list that gets the tasks it starts)"""
        path = path or self.source
        if kind == "Conversie CGXML":
            dialog = self.window(CgxmlDialog)
            sources = [dialog.source]
            dialog.output_row.set_path(os.path.join(self.tmp, "rezultat"))
        elif kind == "Verificare Topologică":
            dialog = self.window(TopologyCheckDialog)
            dialog.source_mode.set_index(1)
            sources = [dialog.cgxml]
            dialog.excel_row.edit.setText("")
        else:
            dialog = self.window(CompareDialog)
            dialog.mode.set_index(1)
            sources = [dialog.old_source, dialog.new_source]
            dialog.excel_row.edit.setText("")
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog.show()
        for source in sources:
            source.set_paths([path])
            source.refresh()
        return dialog, sources, started

    def slow_listings(self):
        """Every listing of a CGXML source takes as long as on a slow network drive: until
        the returned event is set, or 5 s."""
        gate = threading.Event()
        listing = cgxml_sources.list_sources

        def waiting(*args, **kwargs):
            gate.wait(5)
            return listing(*args, **kwargs)
        patcher = mock.patch.object(cgxml_sources, "list_sources", waiting)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(gate.set)
        return gate

    def file_checks(self, folder):
        """The list gets the thread of every file system call on a path in folder: whether
        it exists, whether it is a folder or a file, and its listing."""
        threads = []
        inside = os.path.normcase(folder)

        def recorded(real):
            def check(*args, **kwargs):
                path = args[0] if args else kwargs.get("path", ".")
                if isinstance(path, str) and os.path.normcase(path).startswith(inside):
                    threads.append(threading.current_thread())
                return real(*args, **kwargs)
            return check
        for module, names in ((os.path, ("exists", "isdir", "isfile")),
                              (os, ("listdir", "scandir"))):
            for name in names:
                patcher = mock.patch.object(module, name, recorded(getattr(module, name)))
                patcher.start()
                self.addCleanup(patcher.stop)
        return threads

    @staticmethod
    def labels(dialog):
        """The texts of the labels the window shows."""
        return [label.text() for label in dialog.findChildren(QLabel) if label.isVisible()]

    def counted(self, sources, text):
        """Wait until the status line of every source shows text."""
        self.assertTrue(self.wait(lambda: all(s.status.text() == text for s in sources), 10),
                        [s.status.text() for s in sources])

    def test_a_press_while_the_files_are_counted_returns_at_once_and_the_run_starts_after(self):
        gate = self.slow_listings()
        for kind in self.KINDS:
            with self.subTest(window=kind):
                gate.clear()
                dialog, _sources, started = self.on_files(kind)
                pressed = time.monotonic()
                dialog.run_btn.click()
                self.assertLess(time.monotonic() - pressed, 1.0)
                self.assertEqual(started, [])
                # the window waits as it does for a run
                self.assertTrue(any(self.LOOKING.fullmatch(text) for text in self.labels(dialog)),
                                self.labels(dialog))
                self.assertFalse(dialog.run_btn.isEnabled())
                gate.set()
                self.assertTrue(self.wait(lambda: started, 10))
                self.settle()
                self.assertEqual(len(started), 1)

    def test_a_press_on_a_counted_source_starts_the_run_at_once(self):
        for kind in self.KINDS:
            with self.subTest(window=kind):
                dialog, sources, started = self.on_files(kind)
                self.counted(sources, "S-au găsit 2 fișiere CGXML.")
                dialog.run_btn.click()
                self.assertEqual(len(started), 1, self.bar_text(dialog))

    def test_a_press_makes_no_file_system_call_on_the_source_from_the_window_thread(self):
        threads = self.file_checks(self.source)
        for kind in self.KINDS:
            with self.subTest(window=kind):
                dialog, _sources, started = self.on_files(kind)
                dialog.run_btn.click()
                self.assertTrue(self.wait(lambda: started, 10), self.bar_text(dialog))
                # once more, with the files counted
                dialog.run_btn.click()
                self.assertEqual(len(started), 2)
                self.assertTrue(threads)
                self.assertNotIn(threading.main_thread(), threads)

    def test_a_source_that_cannot_be_used_is_reported_once_its_files_are_counted(self):
        missing = os.path.join(self.tmp, "nu_exista")
        empty = self.delivery("fara_fisiere", files=0)
        damaged = os.path.join(self.tmp, "livrare.zip")
        with open(damaged, "wb") as fp:
            fp.write(b"o descarcare intrerupta")
        choose = "Alegeți folderul, arhiva ZIP sau fișierele CGXML."
        nothing = "Nu s-a găsit niciun fișier .cgxml în sursa aleasă."
        unreadable = "nu poate fi citită: 'livrare.zip' (arhiva ZIP este deteriorată sau incompletă)."
        # for a path that does not exist, a folder without CGXML files and a damaged archive
        warnings = {
            "Conversie CGXML": (choose, nothing, "Sursa aleasă " + unreadable),
            "Verificare Topologică": (choose, nothing, "Sursa aleasă " + unreadable),
            "Comparator": (
                "Alegeți sursa veche: un folder, o arhivă ZIP sau fișiere .cgxml.",
                "Nu s-a găsit niciun fișier .cgxml în sursa veche.",
                "Sursa veche " + unreadable),
        }
        gate = self.slow_listings()
        for kind in self.KINDS:
            for path, text in zip((missing, empty, damaged), warnings[kind]):
                with self.subTest(window=kind, source=os.path.basename(path)):
                    gate.clear()
                    dialog, _sources, started = self.on_files(kind, path)
                    dialog.run_btn.click()
                    gate.set()
                    self.assertTrue(self.wait(lambda: self.bar_text(dialog), 10))
                    self.assertEqual(self.bar_text(dialog), "<b>Atenție:</b> " + text)
                    self.assertEqual(started, [])
                    self.assertTrue(dialog.run_btn.isEnabled())

    def test_a_missing_source_that_appears_after_its_count_is_counted_again_by_the_press(self):
        for number, kind in enumerate(self.KINDS):
            with self.subTest(window=kind):
                name = os.path.join("nas", str(number))
                dialog, sources, started = self.on_files(kind, os.path.join(self.tmp, name))
                self.counted(sources, "Calea aleasă nu există.")
                # the network drive that holds it is back
                self.delivery(name)
                dialog.run_btn.click()
                self.assertTrue(self.wait(lambda: started, 5), self.bar_text(dialog))
                self.assertEqual(self.bar_text(dialog), "")

    def test_a_second_press_while_the_files_are_counted_starts_one_run(self):
        gate = self.slow_listings()
        for kind in self.KINDS:
            with self.subTest(window=kind):
                gate.clear()
                dialog, _sources, started = self.on_files(kind)
                dialog.run_btn.click()
                dialog.run_btn.click()
                gate.set()
                self.assertTrue(self.wait(lambda: started, 10))
                self.settle()
                self.assertEqual(len(started), 1)

    def test_a_project_opened_while_the_files_are_counted_starts_no_run(self):
        gate = self.slow_listings()
        for kind in self.KINDS:
            with self.subTest(window=kind):
                gate.clear()
                dialog, _sources, started = self.on_files(kind)
                dialog.run_btn.click()
                self.other_project()
                gate.set()
                self.assertTrue(self.wait(lambda: dialog.run_btn.isEnabled(), 10))
                self.settle()
                self.assertEqual(started, [])
                self.assertEqual(self.bar_text(dialog),
                                 "Rularea nu a pornit, deoarece proiectul s-a schimbat între timp.")

    def test_the_cancel_button_ends_the_wait_for_the_files(self):
        gate = self.slow_listings()
        dialog, _sources, started = self.on_files("Conversie CGXML")
        dialog.run_btn.click()
        dialog.cancel_task()
        self.assertTrue(dialog.run_btn.isEnabled())
        self.assertEqual(self.bar_text(dialog), "Rularea a fost anulată.")
        gate.set()
        self.settle(0.5)
        self.assertEqual(started, [])

    def test_closing_a_window_during_a_run_is_refused_with_a_notice(self):
        dialog = self.window(TopologyCheckDialog)
        dialog.show()
        self.addCleanup(dialog.close)
        dialog._set_running(True)
        self.addCleanup(dialog._set_running, False)
        for close in (dialog.close, dialog.reject):
            dialog.message_bar.clearWidgets()
            close()
            self.assertTrue(dialog.isVisible())
            self.assertEqual(self.bar_text(dialog),
                             "Fereastra nu se închide cât timp rularea este în curs. Rularea se "
                             "poate anula cu butonul 'Anulează'.")

    def test_the_window_stays_open_while_it_waits_for_the_files(self):
        gate = self.slow_listings()
        dialog, _sources, started = self.on_files("Verificare Topologică")
        dialog.run_btn.click()
        dialog.close()
        self.assertTrue(dialog.isVisible())
        self.assertIn("Fereastra nu se închide cât timp rularea este în curs.",
                      self.bar_text(dialog))
        gate.set()
        self.assertTrue(self.wait(lambda: started, 10))

    def test_a_window_removed_with_the_plugin_while_it_waits_for_the_files_starts_no_run(self):
        gate = self.slow_listings()
        dialog, _sources, started = self.on_files("Comparator")
        dialog.run_btn.click()
        dialog.dispose()
        gate.set()
        self.settle(0.5)
        self.assertEqual(started, [])


class ProjectPathTests(WindowTest):
    """Output and source paths from one project to another."""

    def two_projects(self):
        first = self.saved_project("uat_a", imobile_layer("Imobile A"))
        second = self.saved_project("uat_b", imobile_layer("Imobile B"))
        return first, second

    def test_a_report_path_is_offered_only_in_the_project_it_was_used_in(self):
        first, second = self.two_projects()
        report = os.path.join(self.tmp, "uat_a", "raport_intravilan.xlsx")
        self.open_project(first)
        dialog = self.window(IntravilanDialog)
        dialog.excel_row.set_path(report)
        dialog.tolerance.setValue(2.5)
        dialog.save_remembered()
        # the paths are kept beside the project, not in it
        self.assertFalse(self.project.isDirty())
        self.open_project(second)
        other = self.window(IntravilanDialog)
        self.assertEqual(other.excel_row.path(), "")
        # a value that is not a path, like the tolerance, is shared by every project
        self.assertEqual(other.tolerance.value(), 2.5)
        self.open_project(first)
        self.assertEqual(self.window(IntravilanDialog).excel_row.path(), report)

    def test_a_window_left_open_takes_the_paths_of_the_project_opened(self):
        first, second = self.two_projects()
        self.open_project(first)
        dialog = self.window(IntravilanDialog)
        dialog.gpkg_row.set_path(os.path.join(self.tmp, "a.gpkg"))
        dialog.excel_row.set_path(os.path.join(self.tmp, "a.xlsx"))
        dialog.save_remembered()
        self.open_project(second)
        self.assertEqual((dialog.gpkg_row.path(), dialog.excel_row.path()), ("", ""))
        dialog.excel_row.set_path(os.path.join(self.tmp, "b.xlsx"))
        dialog.save_remembered()
        self.open_project(first)
        self.assertEqual((dialog.gpkg_row.path(), dialog.excel_row.path()),
                         (os.path.join(self.tmp, "a.gpkg"), os.path.join(self.tmp, "a.xlsx")))
        self.open_project(second)
        self.assertEqual((dialog.gpkg_row.path(), dialog.excel_row.path()),
                         ("", os.path.join(self.tmp, "b.xlsx")))

    def test_a_path_remembered_for_every_project_is_not_offered(self):
        # a path stored for the whole profile rather than for a project
        dialog_memory.save("IntravilanDialog", "excel", os.path.join(self.tmp, "vechi.xlsx"))
        dialog_memory.save("CompareDialog", "excel_layers", os.path.join(self.tmp, "c.xlsx"))
        self.assertEqual(self.window(IntravilanDialog).excel_row.path(), "")
        self.assertEqual(self.window(CompareDialog).excel_row.path(), "")

    def used_without_a_file(self):
        """A new project, not saved yet, in which Conversie CGXML and Raport Intravilan have
        been run; returns the source, the output folder and the report of those runs."""
        self.project.clear()
        source, out = os.path.join(self.tmp, "livrare"), os.path.join(self.tmp, "rezultate")
        report = os.path.join(out, "raport_intravilan.xlsx")
        conversion = self.window(CgxmlDialog)
        conversion.source.set_paths([source])
        conversion.output_row.set_path(out)
        conversion.save_remembered()
        intravilan = self.window(IntravilanDialog)
        intravilan.excel_row.set_path(report)
        intravilan.save_remembered()
        return source, out, report

    def offered(self):
        """(source, output folder, report) that new windows of the two tools open with."""
        conversion, intravilan = self.window(CgxmlDialog), self.window(IntravilanDialog)
        return (conversion.source.edit.text(), conversion.output_row.path(),
                intravilan.excel_row.path())

    def test_paths_used_before_a_project_was_saved_are_offered_when_it_is_opened_again(self):
        used = self.used_without_a_file()
        self.project.addMapLayer(imobile_layer("Imobile A"))
        saved = os.path.join(self.tmp, "uat_a.qgz")
        self.assertTrue(self.project.write(saved))
        self.assertEqual(self.offered(), used)
        # the next day
        self.project.clear()
        self.open_project(saved)
        self.assertEqual(self.offered(), used)

    def test_a_new_project_is_not_offered_the_paths_of_the_project_saved_before_it(self):
        self.used_without_a_file()
        self.assertTrue(self.project.write(os.path.join(self.tmp, "uat_a.qgz")))
        self.project.clear()
        self.assertEqual(self.offered(), ("", "", ""))

    def test_a_new_project_is_not_offered_the_paths_of_a_project_closed_without_a_file(self):
        used = self.used_without_a_file()
        # while that project is open, the windows opened later start with its paths
        self.assertEqual(self.offered(), used)
        self.project.clear()
        self.assertEqual(self.offered(), ("", "", ""))

    def test_a_later_session_is_not_offered_the_paths_of_a_project_never_saved(self):
        self.used_without_a_file()
        # QGIS closed and started again: the application object is a new one
        QgsApplication.instance().setProperty(dialog_memory.SESSION_MARK, None)
        self.assertEqual(self.offered(), ("", "", ""))

    def test_a_project_saved_over_a_file_with_paths_of_its_own_takes_those(self):
        first = self.saved_project("uat_a", imobile_layer("Imobile A"))
        dialog = self.window(IntravilanDialog)
        dialog.excel_row.set_path(os.path.join(self.tmp, "raport_a.xlsx"))
        dialog.save_remembered()
        self.used_without_a_file()
        self.assertTrue(self.project.write(first))
        self.assertEqual(self.offered(), ("", "", os.path.join(self.tmp, "raport_a.xlsx")))

    def test_a_report_path_of_the_comparator_pasted_in_quotes_stays_with_its_project(self):
        first, second = self.two_projects()
        report = os.path.join(self.tmp, "uat_a_comparatie.xlsx")
        self.open_project(first)
        dialog = self.window(CompareDialog)
        dialog.mode.set_index(0)
        # as the file manager's 'Copy as path' gives it
        dialog.excel_row.edit.setText(f'"{report}"')
        self.assertEqual(dialog.excel_row.path(), report)
        dialog.save_remembered()
        self.open_project(second)
        self.assertEqual(dialog.excel_row.path(), "")
        self.assertEqual(self.window(CompareDialog).excel_row.path(), "")
        self.open_project(first)
        self.assertEqual(self.window(CompareDialog).excel_row.path(), report)

    def test_the_comparator_keeps_the_report_of_each_mode_for_its_project(self):
        first, second = self.two_projects()
        layers, files = os.path.join(self.tmp, "layere.xlsx"), os.path.join(self.tmp, "f.xlsx")
        self.open_project(first)
        dialog = self.window(CompareDialog)
        dialog.mode.set_index(0)
        dialog.excel_row.edit.setText(layers)
        dialog.mode.set_index(1)
        dialog.excel_row.edit.setText(files)
        dialog.save_remembered()
        self.open_project(second)
        self.assertEqual(dialog.excel_row.path(), "")
        dialog.mode.set_index(0)
        self.assertEqual(dialog.excel_row.path(), "")
        self.assertEqual(self.window(CompareDialog).excel_row.path(), "")
        self.open_project(first)
        self.assertEqual(dialog.excel_row.path(), layers)
        dialog.mode.set_index(1)
        self.assertEqual(dialog.excel_row.path(), files)

    def test_the_sector_of_the_roads_is_the_same_in_every_project(self):
        first, second = self.two_projects()
        self.open_project(first)
        dialog = self.window(TopologyCheckDialog)
        dialog.roads_sector.setText("99")
        dialog.save_remembered()
        self.open_project(second)
        self.assertEqual(dialog.roads_sector.text(), "99")
        self.assertEqual(self.window(TopologyCheckDialog).roads_sector.text(), "99")
        # typed and not run yet: opening a project leaves it as typed
        dialog.roads_sector.setText("7")
        self.open_project(first)
        self.assertEqual(dialog.roads_sector.text(), "7")

    def test_a_cgxml_source_is_counted_again_when_the_project_brings_another(self):
        first, second = self.two_projects()
        few = os.path.join(self.tmp, "livrare")
        os.makedirs(few)
        name = next(n for n in sorted(os.listdir(helpers.DATA_DIR)) if n.endswith(".cgxml"))
        with open(os.path.join(helpers.DATA_DIR, name), "rb") as source, \
                open(os.path.join(few, name), "wb") as copy:
            copy.write(source.read())
        self.open_project(first)
        dialog = self.window(CgxmlDialog)
        dialog.show()
        dialog.source.set_paths([helpers.DATA_DIR])
        dialog.source.refresh()
        dialog.output_row.set_path(self.tmp)
        self.assertEqual(helpers.cgxml_count(dialog.source), 11)
        dialog.save_remembered()
        self.open_project(second)
        self.assertEqual(dialog.source.paths(), [])
        self.assertEqual(dialog.source.status.text(), "")
        dialog.source.set_paths([few])
        dialog.source.refresh()
        self.assertEqual(helpers.cgxml_count(dialog.source), 1)
        dialog.save_remembered()
        self.open_project(first)
        self.assertEqual(dialog.source.paths(), [helpers.DATA_DIR])
        self.assertEqual(helpers.cgxml_count(dialog.source), 11)
        self.assertIn("11", dialog.source.status.text())
        dialog.close()

    def test_diminuare_reads_the_table_of_the_project_opened(self):
        first, second = self.two_projects()
        table = os.path.join(self.tmp, "parcele.csv")
        with open(table, "w", encoding="utf-8-sig") as fp:
            fp.write("Nr;Suprafata act\n1;6000\n2;4000\n")
        self.open_project(first)
        dialog = self.window(DiminuareDialog)
        dialog.file_row.set_path(table)
        dialog._load_file()
        dialog.contour_spin.setValue(9000)
        self.assertIsNotNone(dialog._result)
        dialog.save_remembered()
        self.open_project(second)
        self.settle()
        self.assertEqual(dialog.file_row.path(), "")
        self.assertEqual((dialog._rows, dialog._result), ([], None))
        # the contour area was measured in the other project
        self.assertEqual(dialog.contour_spin.value(), 0.0)
        self.open_project(first)
        self.assertTrue(self.wait(lambda: dialog._rows))
        self.assertEqual(dialog.file_row.path(), table)

    def test_the_overwrite_question_names_the_file_with_its_folder(self):
        report = os.path.join(self.tmp, "raport_intravilan.xlsx")
        open(report, "wb").close()
        dialog = self.window(IntravilanDialog)
        questions = []
        dialog.confirm = lambda title, message, *a, **k: questions.append(message) or False
        self.assertFalse(dialog.confirm_overwrite([report]))
        self.assertEqual(questions, [f"Fișierul '{report}' există deja. Doriți să fie înlocuit?"])
        gpkg = os.path.join(self.tmp, "raport.gpkg")
        open(gpkg, "wb").close()
        dialog.confirm_overwrite([report, gpkg])
        self.assertEqual(questions[1], f"Fișierele '{report}', '{gpkg}' există deja. Doriți să "
                                       "fie înlocuite?")

    def test_the_values_of_the_projects_used_longest_ago_are_dropped(self):
        clock = iter(range(1000, 2000))
        with mock.patch.object(dialog_memory, "MAX_PROJECTS", 3), \
                mock.patch.object(dialog_memory.time, "time", lambda: next(clock)):
            for number in range(5):
                self.project.setFileName(os.path.join(self.tmp, f"proiect_{number}.qgz"))
                dialog_memory.save("Fereastra", "raport", f"raport_{number}.xlsx", project=True)
            kept = []
            for number in range(5):
                self.project.setFileName(os.path.join(self.tmp, f"proiect_{number}.qgz"))
                kept.append(dialog_memory.load("Fereastra", "raport", project=True))
        self.assertEqual(kept, [None, None, "raport_2.xlsx", "raport_3.xlsx", "raport_4.xlsx"])


class ProjectChoiceTests(WindowTest):
    """What the user chose in a window, after the project is replaced."""

    def untick_first(self, checklist):
        checklist.list.item(0).setCheckState(Qt.CheckState.Unchecked)

    def ticked(self, checklist):
        return sorted(layer.name() for layer in checklist.checked_layers())

    def test_a_closed_window_ticks_the_layers_of_the_next_project_by_name(self):
        self.project.addMapLayers([imobile_layer("Imobile A1"), imobile_layer("Imobile A2")])
        plugin = self.plugin()
        tool = plugin.check_topology()
        self.untick_first(tool.imobile)
        self.assertEqual(self.ticked(tool.imobile), ["Imobile A2"])
        tool.reject()
        self.other_project(imobile_layer("Imobile B1"), imobile_layer("Imobile B2"))
        tool = plugin.check_topology()
        self.assertEqual(self.ticked(tool.imobile), ["Imobile B1", "Imobile B2"])

    def test_an_open_window_ticks_the_layers_of_the_next_project_by_name(self):
        self.project.addMapLayers([imobile_layer("Imobile A1"), imobile_layer("Imobile A2")])
        dialog = self.window(TopologyCheckDialog)
        dialog.show()
        self.untick_first(dialog.imobile)
        self.other_project(imobile_layer("Imobile B1"), imobile_layer("Imobile B2"))
        self.settle()
        self.assertEqual(self.ticked(dialog.imobile), ["Imobile B1", "Imobile B2"])
        dialog.close()

    def test_a_column_preselected_in_one_project_does_not_decide_the_next(self):
        self.project.addMapLayer(imobile_layer("Imobile A", "nr_cad:string", ((0, ["1"]),)))
        dialog = self.window(TopologyCheckDialog)
        self.assertEqual(dialog.id_combo.currentData(), "nr_cad")
        # here "id" comes first among the names of the ID column
        self.other_project(imobile_layer("Imobile B", "nr_cad:string,id:string",
                                         ((0, ["1", "101"]),)))
        self.assertEqual(self.ticked(dialog.imobile), ["Imobile B"])
        self.assertEqual(dialog.id_combo.currentData(), "id")

    def test_a_column_picked_by_hand_does_not_hold_for_the_next_project(self):
        self.project.addMapLayer(imobile_layer("Imobile A"))
        dialog = self.window(TopologyCheckDialog)
        dialog.sector_combo.setCurrentIndex(0)
        dialog.sector_combo.activated.emit(0)
        self.assertEqual(dialog.sector_combo.currentData(), "")
        self.other_project(imobile_layer("Imobile B"))
        self.assertEqual(self.ticked(dialog.imobile), ["Imobile B"])
        self.assertEqual(dialog.sector_combo.currentData(), "sector")

    def converted(self, label, batch):
        """An imobile and a construcții layer of one Conversie CGXML run."""
        imobile = helpers.converted_layer(f"Imobile {label}", batch, "id:string",
                                          [(square(0, 0, 40), ["1"])])
        constructii = helpers.converted_layer(f"Construcții {label}", batch,
                                              "id:string,nr_constr:string",
                                              [(square(5, 5, 5), ["1", "C1"])])
        return imobile, constructii

    def test_a_building_link_set_by_hand_does_not_hold_for_the_next_project(self):
        for build in (BuildingTopologyDialog, TopologyCheckDialog):
            with self.subTest(window=build.__name__):
                self.other_project(*self.converted("A", "20250101"))
                dialog = self.window(build)
                self.assertEqual(dialog.link_combo.currentData(), "id")
                # "(după suprapunere)", picked by hand
                dialog.link_combo.setCurrentIndex(0)
                dialog.link_combo.activated.emit(0)
                self.other_project(*self.converted("B", "20260101"))
                self.assertEqual(self.ticked(dialog.constructii), ["Construcții B"])
                self.assertEqual(dialog.link_combo.currentData(), "id")

    def test_the_comparator_starts_on_the_versions_of_the_next_project(self):
        self.project.addMapLayers([imobile_layer("Parcele 1"), imobile_layer("Parcele 2"),
                                   imobile_layer("Parcele 3")])
        dialog = self.window(CompareDialog)
        dialog.show()
        dialog.old_combo.setLayer(self.project.mapLayersByName("Parcele 3")[0])
        dialog.old_combo.activated.emit(dialog.old_combo.currentIndex())
        dialog.close()
        older, _buildings = self.converted("v1", "20250101")
        newer, _buildings = self.converted("v2", "20260101")
        self.other_project(older, newer)
        dialog.show()
        self.assertEqual([dialog.old_combo.currentLayer().name(),
                          dialog.new_combo.currentLayer().name()], ["Imobile v1", "Imobile v2"])
        dialog.close()

    def test_ticks_set_by_hand_in_a_column_list_do_not_hold_for_the_next_project(self):
        fields = "ID_IMOBIL:string,Proprietar:string"
        self.project.addMapLayer(imobile_layer("Imobile A", fields, ((0, ["A", "X"]),)))
        dialog = self.window(IntravilanDialog)
        item = dialog.field_list.list.findItems("Proprietar", Qt.MatchFlag.MatchExactly)[0]
        item.setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dialog.field_list.checked_texts(), ["ID_IMOBIL"])
        self.other_project(imobile_layer("Imobile B", fields, ((0, ["B", "Y"]),)))
        self.assertEqual(self.ticked(dialog.imobile), ["Imobile B"])
        self.assertEqual(dialog.field_list.checked_texts(), ["ID_IMOBIL", "Proprietar"])


class KeptWindowTests(WindowTest):
    """A window the plugin keeps through a change of project, beside a window built in the
    project now open: every widget of the two holds the same."""

    TOOLS = ("convert_cgxml", "intravilan_report", "area_reduction", "compare_layers",
             "check_topology", "building_topology")
    # shown some time after the window, by a worker thread
    LATER = ("targets_label", "rows_label")

    def uat(self, tag, imobile):
        """A project saved as uat_<tag>.qgz, with the layers of two conversions of that many
        imobile, the limits of its sectors, an intravilan and a contour, each in a GeoPackage
        of its own."""
        self.project.clear()

        def stored(layer):
            path = os.path.join(self.tmp, f"{tag}_{len(os.listdir(self.tmp))}.gpkg")
            return helpers.gpkg_layer(layer, path)

        layers = []
        for version in (1, 2):
            parcels = stored(helpers.polygon_layer(
                f"Imobile {tag}_v{version}",
                [(square(50 * n, 0, 40), [100 + n, 70000 + n, 5]) for n in range(imobile)],
                fields="id:integer,ie:integer,sector:integer"))
            houses = stored(helpers.polygon_layer(
                f"Construcții {tag}_v{version}",
                [(square(50 * n + 5, 5, 10), [100 + n, "C1"]) for n in range(imobile)],
                fields="id:integer,nr_constr:string"))
            for layer in (parcels, houses):
                roles.mark_columns(layer, [(field.name(), field.name())
                                           for field in layer.fields() if field.name() != "fid"])
                roles.mark_batch(layer, f"2026010{version}")
            layers += [parcels, houses]
        for name, fields, value in ((f"limite sector {tag}", "sector:string", "5"),
                                    (f"intravilan {tag}", "nume:string", "x"),
                                    (f"contur {tag}", "nume:string", "x")):
            layers.append(stored(helpers.polygon_layer(
                name, [(square(-5, -5, 60), [value])], fields=fields)))
        self.project.addMapLayers(layers)
        path = os.path.join(self.tmp, f"uat_{tag}.qgz")
        self.assertTrue(self.project.write(path))
        return path

    def held(self, widget):
        """What a widget holds, NotImplemented for anything that is no input or text of a
        window."""
        if isinstance(widget, QgsMapLayerComboBox):
            return widget.currentLayer().name() if widget.currentLayer() else None
        if isinstance(widget, QComboBox):
            return [widget.itemText(i) for i in range(widget.count())], widget.currentText()
        if isinstance(widget, CheckList):
            return widget.texts(), widget.checked_texts()
        if isinstance(widget, PathRow):
            return widget.path()
        if isinstance(widget, CgxmlSource):
            return widget.edit.text()
        if isinstance(widget, SegmentedControl):
            return widget.index()
        if isinstance(widget, StatusLabel):
            return widget.text()
        if isinstance(widget, (QCheckBox, QGroupBox)):
            return widget.isChecked()
        if isinstance(widget, QLineEdit):
            return widget.text()
        if isinstance(widget, QDoubleSpinBox):
            return widget.value()
        if isinstance(widget, QTabWidget):
            return widget.currentIndex()
        if isinstance(widget, dict) and widget:
            values = {str(key): self.held(value) for key, value in widget.items()}
            return NotImplemented if NotImplemented in values.values() else values
        if isinstance(widget, (tuple, list)) and widget:
            values = [self.held(value) for value in widget]
            return NotImplemented if NotImplemented in values else values
        return NotImplemented

    def state(self, dialog):
        """{attribute: what it holds} of the widgets the window keeps as attributes."""
        return {name: self.held(value) for name, value in vars(dialog).items()
                if name not in self.LATER and self.held(value) is not NotImplemented}

    def compare_with_new_windows(self, plugin, label):
        for tool in self.TOOLS:
            with self.subTest(project=label, window=tool):
                kept = getattr(plugin, tool)()
                self.settle(0.3)
                new = type(kept)(iface=self.iface, parent=self.iface.mainWindow())
                self.addCleanup(new.deleteLater)
                new.show()
                self.settle(0.3)
                try:
                    self.assertEqual(self.state(kept), self.state(new))
                finally:
                    new.reject()
                    new.dispose()
                    kept.reject()

    def test_a_window_kept_through_changes_of_project_holds_what_a_new_window_holds(self):
        first, second = self.uat("A", 3), self.uat("B", 2)
        self.project.clear()
        plugin = self.plugin()
        self.open_project(first)
        self.settle()
        self.compare_with_new_windows(plugin, "the one the windows were built in")
        self.open_project(second)
        self.settle()
        self.compare_with_new_windows(plugin, "another, opened with the windows closed")
        self.open_project(first)
        self.settle()
        self.compare_with_new_windows(plugin, "the first again")
        self.project.clear()
        self.settle()
        self.compare_with_new_windows(plugin, "a new one, without layers")


class PaletteTests(WindowTest):
    """A change of the palette while QGIS runs: another QGIS theme, or the system's dark mode."""

    def dark_palette(self):
        before = QApplication.palette()
        self.addCleanup(self.settle, 0.1)
        self.addCleanup(QApplication.setPalette, before)
        dark = QPalette(before)
        for role, color in ((QPalette.ColorRole.Window, "#323232"),
                            (QPalette.ColorRole.WindowText, "#E6E6E6"),
                            (QPalette.ColorRole.Base, "#262626"),
                            (QPalette.ColorRole.Text, "#E6E6E6"),
                            (QPalette.ColorRole.Button, "#3C3C3C"),
                            (QPalette.ColorRole.ButtonText, "#E6E6E6")):
            dark.setColor(role, QColor(color))
        QApplication.setPalette(dark)
        self.settle(0.1)

    @staticmethod
    def linked_notes(dialog):
        return [label.text() for label in dialog.findChildren(QLabel) if "<a " in label.text()]

    def test_an_open_window_takes_the_colours_of_the_new_palette(self):
        dialog = self.window(TopologyCheckDialog)
        dialog.show()
        self.settle(0.1)
        self.assertIn(theme.ACCENT, dialog.styleSheet())
        icon = dialog.windowIcon().pixmap(16).toImage()
        self.dark_palette()
        self.assertTrue(dialog.tokens.dark)
        # section titles and links in the blue that is legible on a dark window
        self.assertIn(f"color: {theme.ACCENT_CYAN}", dialog.styleSheet())
        [note] = self.linked_notes(dialog)
        self.assertIn(f"color:{theme.ACCENT_CYAN}", note)
        self.assertNotEqual(dialog.windowIcon().pixmap(16).toImage(), icon)
        dialog.close()

    @staticmethod
    def icon_images(window):
        """The window's icon and, in the help window, the icons of its list of tools."""
        icons = [window.windowIcon()]
        tools = getattr(window, "tools", None)
        if tools is not None:
            icons += [tools.item(row).icon() for row in range(tools.count())]
        return [icon.pixmap(16).toImage() for icon in icons]

    def test_a_closed_window_opens_again_in_the_colours_and_icons_of_the_new_palette(self):
        plugin = self.plugin()
        openers = (plugin.check_topology, plugin.open_help)
        light = []
        for opened in openers:
            window = opened()
            light.append(window.windowIcon().pixmap(16).toImage())
            window.reject()
        self.dark_palette()
        for opened, light_icon in zip(openers, light):
            window = opened()
            name = type(window).__name__
            self.assertTrue(window.tokens.dark, name)
            self.assertIn(f"color: {theme.ACCENT_CYAN}", window.styleSheet())
            for note in self.linked_notes(window):
                self.assertIn(f"color:{theme.ACCENT_CYAN}", note)
            self.assertNotEqual(window.windowIcon().pixmap(16).toImage(), light_icon, name)
            # the icons a window built under the new palette shows
            self.assertEqual(self.icon_images(window), self.icon_images(self.window(type(window))),
                             name)

    def test_text_typed_in_a_closed_window_stays_through_a_change_of_the_palette(self):
        plugin = self.plugin()
        window = plugin.check_topology()
        window.excel_row.edit.setText("raport_verificare.xlsx")
        window.reject()
        self.dark_palette()
        self.assertEqual(plugin.check_topology().excel_row.edit.text(), "raport_verificare.xlsx")

    def test_the_toolbar_takes_the_icons_of_the_new_palette(self):
        plugin = self.plugin()
        action = plugin.actions["convert_cgxml"]
        light = action.icon().pixmap(16).toImage()
        self.dark_palette()
        self.assertNotEqual(action.icon().pixmap(16).toImage(), light)


class KeyboardTests(WindowTest):
    """Keyboard focus, mnemonics and the names a screen reader announces."""

    MNEMONIC = re.compile(r"&([a-zA-Z])")

    def opened(self, build):
        dialog = self.window(build)
        dialog.show()
        self.settle(0.1)
        self.addCleanup(dialog.close)
        return dialog

    def test_the_mode_switch_shows_which_segment_has_the_keyboard_focus(self):
        dialog = self.opened(TopologyCheckDialog)
        dialog.activateWindow()
        self.settle(0.1)
        for segment in dialog.source_mode.buttons:
            dialog.excel_row.edit.setFocus(Qt.FocusReason.TabFocusReason)
            self.settle(0.1)
            size, plain = segment.size(), segment.grab().toImage()
            segment.setFocus(Qt.FocusReason.TabFocusReason)
            self.settle(0.1)
            self.assertTrue(segment.hasFocus())
            self.assertNotEqual(segment.grab().toImage(), plain, segment.text())
            # the switch does not move when the focus enters it
            self.assertEqual(segment.size(), size, segment.text())

    def test_path_fields_and_their_buttons_have_a_name_for_a_screen_reader(self):
        dialog = self.opened(IntravilanDialog)
        self.assertEqual(dialog.excel_row.edit.accessibleName(), "Raport Excel")
        self.assertEqual(dialog.gpkg_row.edit.accessibleName(), "Layer GeoPackage")
        [browse] = dialog.excel_row.findChildren(QToolButton)
        self.assertEqual(browse.accessibleName(), "Salvați raportul Excel")
        compare = self.opened(CompareDialog)
        self.assertEqual(compare.old_source.edit.accessibleName(), "Sursa veche (înainte)")
        convert = self.opened(CgxmlDialog)
        self.assertEqual(convert.source.edit.accessibleName(),
                         "Folderul, arhiva ZIP sau fișierele .cgxml")

    def test_buttons_that_show_only_an_icon_have_a_name_for_a_screen_reader(self):
        for build in TOOL_WINDOWS + (SettingsDialog,):
            dialog = self.opened(build)
            for button in dialog.findChildren(QAbstractButton):
                if button.toolTip() and not any(c.isalnum() for c in button.text()):
                    self.assertEqual(button.accessibleName(), button.toolTip(), build.__name__)
            names = [b.accessibleName() for b in dialog.findChildren(QToolButton)]
            self.assertIn("Cum se folosește acest instrument", names, build.__name__)

    def test_fields_in_rows_built_by_hand_have_a_name_for_a_screen_reader(self):
        compare = self.opened(CompareDialog)
        self.assertEqual(compare.old_key.accessibleName(), "Coloana cheie, Versiunea veche")
        self.assertEqual(compare.new_combo.accessibleName(), "Layer, Versiunea nouă")
        number = compare.building_keys["new"][1]
        self.assertEqual(number.accessibleName(), "Nr. construcției, Versiunea nouă")
        diminuare = self.opened(DiminuareDialog)
        self.assertEqual(diminuare.file_row.edit.accessibleName(), "Tabel Excel / CSV")
        self.assertEqual(diminuare.contour_spin.accessibleName(), "Suprafață contur")

    def mnemonics(self, dialog):
        """({letter: widgets that carry it}, the letters of the run and close buttons)"""
        carriers = {}
        buttons = [b for b in dialog.button_box.buttons()
                   if b is dialog._close_btn or b.objectName() == "cadexPrimary"]
        labels = [label for label in dialog.findChildren(QLabel) if label.buddy() is not None]
        for widget in buttons + labels:
            for letter in self.MNEMONIC.findall(widget.text()):
                carriers.setdefault(letter.lower(), []).append(widget.text())
        return carriers, [self.MNEMONIC.findall(b.text()) for b in buttons]

    def test_the_run_and_close_buttons_and_the_form_labels_have_mnemonics(self):
        for build in TOOL_WINDOWS + (SettingsDialog,):
            dialog = self.opened(build)
            carriers, of_buttons = self.mnemonics(dialog)
            self.assertTrue(all(len(found) == 1 for found in of_buttons),
                            (build.__name__, of_buttons))
            # a letter that two controls share would only move the focus between them
            self.assertEqual({k: v for k, v in carriers.items() if len(v) > 1}, {},
                             build.__name__)
            self.assertGreater(len(carriers), len(of_buttons), build.__name__)

    def test_a_form_label_gives_the_keyboard_focus_to_its_field(self):
        dialog = self.opened(IntravilanDialog)
        label = next(label for label in dialog.findChildren(QLabel)
                     if label.text().replace("&", "") == "Raport Excel:")
        self.assertIs(label.buddy(), dialog.excel_row.edit)
        self.assertEqual(len(self.MNEMONIC.findall(label.text())), 1)


class ScreenRatioTests(WindowTest):

    def given_to_labels(self, build):
        """The pixmaps the labels are given while build() runs on a screen scaled to 200 %.
        QLabel.pixmap() of Qt 6.11 returns a pixmap for the ratio of the screen the test runs
        on, so what the labels are given is recorded."""
        given = []
        set_pixmap = QLabel.setPixmap

        def record(label, pixmap):
            given.append(pixmap)
            set_pixmap(label, pixmap)

        with mock.patch.object(QGuiApplication, "devicePixelRatio", lambda app: 2.0), \
                mock.patch.object(QLabel, "setPixmap", record):
            build()
        return given

    @staticmethod
    def sizes(pixmaps):
        """The (width in the window, pixel ratio) pairs of the pixmaps."""
        return sorted({(round(pixmap.width() / pixmap.devicePixelRatio()),
                        pixmap.devicePixelRatio()) for pixmap in pixmaps})

    def test_icons_in_labels_are_drawn_for_the_pixel_ratio_of_the_screen(self):
        def build():
            self.window(TopologyCheckDialog)
            status = StatusLabel("Calea aleasă nu există.", "warning")
            self.addCleanup(status.deleteLater)
            from cadex.dialogs.result_dialog import ResultDialog
            result = ResultDialog(None, "Probă", "Gata.")
            self.addCleanup(result.deleteLater)
        # the status icon, the note's mark, the result icon and the tool's icon, each as wide
        # in the window as on an unscaled screen
        self.assertEqual(self.sizes(self.given_to_labels(build)),
                         [(16, 2.0), (18, 2.0), (28, 2.0), (36, 2.0)])

    def test_the_colour_marks_of_diminuare_are_drawn_for_the_pixel_ratio_of_the_screen(self):
        given = self.given_to_labels(lambda: self.window(DiminuareDialog))
        # the two marks of the legend under the table, beside the tool's icon
        marks = [pixmap for pixmap in given if self.sizes([pixmap]) == [(12, 2.0)]]
        self.assertEqual(len(marks), 2, self.sizes(given))
        for mark in marks:
            # painted over its whole width, not a 12 pixel drawing in a corner
            self.assertGreater(mark.toImage().pixelColor(19, 12).alpha(), 0)


class UnloadTests(WindowTest):
    """Unloading the plugin while a run is in progress."""

    class Run(CadexTask):
        """Keeps running for delay seconds after it is canceled, as a run finishes the imobil
        it is on."""

        def __init__(self, delay):
            super().__init__("CADex: probă")
            self.delay = delay
            self.ended = threading.Event()

        def run(self):
            while not self.stopped():
                time.sleep(0.01)
            time.sleep(self.delay)
            self.ended.set()
            return False

    def running(self, plugin, delay):
        window = plugin.check_topology()
        task = self.Run(delay)
        window.start_task(task)
        self.assertTrue(self.wait(lambda: task.status() == QgsTask.TaskStatus.Running))
        return task

    def test_an_unloaded_plugin_is_not_kept_in_memory_by_its_toolbar(self):
        import gc
        import weakref
        # the welcome message would hold the plugin for the seconds it is shown
        settings_service.set_("general/welcome_shown", True)
        plugin = Cadex(self.iface)
        plugin.initGui()
        released = weakref.ref(plugin)
        gc.disable()
        self.addCleanup(gc.enable)
        plugin.unload()
        del plugin
        # the toolbar and the actions are deleted on the next pass of the event loop
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertIsNone(released())

    def test_unloading_waits_for_a_canceled_run_to_leave_its_thread(self):
        plugin = self.plugin()
        task = self.running(plugin, 0.3)
        plugin.unload()
        self.assertTrue(task.ended.is_set())

    def test_unloading_does_not_wait_past_its_limit_for_a_run_that_goes_on(self):
        plugin = self.plugin()
        task = self.running(plugin, 3.0)
        with mock.patch.object(tasks, "UNLOAD_WAIT", 0.3):
            started = time.monotonic()
            plugin.unload()
            waited = time.monotonic() - started
        self.assertLess(waited, 2.0)
        self.assertTrue(self.wait(task.ended.is_set, 10))
        self.settle(0.2)


def tearDownModule():
    settings_service.reset_all()
    QgsProject.instance().clear()
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
