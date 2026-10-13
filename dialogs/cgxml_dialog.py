# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conversie CGXML window."""

import json
import os
import re

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtGui import QFont  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QAbstractItemView, QCheckBox, QComboBox, QGroupBox, QHeaderView, QLineEdit,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from qgis.core import QgsVectorLayer  # type: ignore

from . import _ui_helpers
from .base_dialog import BaseDialog
from .widgets import (
    CgxmlSource, ExistingFiles, PathRow, StatusLabel, add_field_row, hint_label, make_form,
)
from .. import features
from ..i18n import tr as _tr
from ..services import (
    cgxml_columns, cgxml_export, deps, dialog_memory, fmt, layer_style, roles, settings_service,
    siruta,
)

# sample values for the DXF label preview
_SAMPLES = {
    "id": "10188", "ie": "50124", "ie_imobil": "50124", "sector": "3",
    "judet": "Brașov", "uat": "Cristian", "localitate": "Cristian",
    "adresa": "UAT Cristian, Strada Mare, Nr. 10", "desc_adresa": "lângă școală",
    "nr_postal": "10", "tip_teren": "Intravilan", "nr_tarla": "12",
    "nr_parcele": "345/1", "categ_fol": "CC, A", "nr_titlu": "1234",
    "imprej": "Da", "sup_mas": "2628", "sup_acte": "2600",
    "propr": "POPESCU ION", "alte_pers": "BANCA EXEMPLU SA (ipotecă)",
    "nr_cad_vechi": "1543", "nr_cf_vechi": "4663",
    "note": "...", "nr_constr": "C1",
    "destinatie": "CL", "destinatie_text": "Construcții de locuințe",
    "etaje": "2", "sup_tot": "240", "are_acte": "Da",
}
_FILE_NAME = re.compile(r'^[^<>:"/\\|?*\x00-\x1f]+$')
KEY_ROLE = Qt.ItemDataRole.UserRole


def _inner_tabs():
    tabs = QTabWidget()
    tabs.setDocumentMode(True)
    return tabs


class ColumnTree(QWidget):
    """Column layout of one output layer: which columns, in what order, under what field
    names."""

    def __init__(self, which, parent=None):
        super().__init__(parent)
        self.which = which
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_tr("Coloană"), _tr("Nume câmp"), _tr("Tip")])
        self.tree.headerItem().setToolTip(0, _tr("Denumirea coloanei, folosită și în Excel"))
        self.tree.headerItem().setToolTip(
            1, _tr("Numele câmpului în GeoPackage și Shapefile; dublu clic pentru a-l schimba"))
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumHeight(220)
        # only the name column is editable
        self.tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        self.tree.itemDoubleClicked.connect(lambda item, _col: self.tree.editItem(item, 1))
        # items lack ItemIsDropEnabled, so a dragged row cannot become a child of another
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setDropIndicatorShown(True)
        self.tree.currentItemChanged.connect(lambda *_: self._update_buttons())
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.resizeSection(1, 130)
        layout.addWidget(self.tree)

        self.up_btn = _ui_helpers.small_button(
            "", _tr("Mută coloana selectată mai sus"),
            _ui_helpers.theme_icon("/mActionArrowUp.svg"))
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn = _ui_helpers.small_button(
            "", _tr("Mută coloana selectată mai jos"),
            _ui_helpers.theme_icon("/mActionArrowDown.svg"))
        self.down_btn.clicked.connect(lambda: self._move(1))
        reset = _ui_helpers.small_button(
            _tr("Implicite"), _tr("Revine la coloanele, ordinea și numele inițiale"))
        reset.clicked.connect(lambda: self.set_layout(cgxml_columns.default_layout(which)))
        row, _all, _none = _ui_helpers.make_select_row(
            lambda: _ui_helpers.set_all_check_states(self.tree, True),
            lambda: _ui_helpers.set_all_check_states(self.tree, False),
            extra_buttons=(self.up_btn, self.down_btn, reset))
        layout.addLayout(row)
        self._update_buttons()

    def set_layout(self, layout):
        kinds = {"int": _tr("număr întreg"), "real": _tr("număr"), "text": _tr("text")}
        self.tree.clear()
        for column in layout:
            label = cgxml_columns.label_of(self.which, column["key"])
            item = QTreeWidgetItem([label, column["name"],
                                    kinds[cgxml_columns.kind_of(self.which, column["key"])]])
            item.setData(0, KEY_ROLE, column["key"])
            hint = cgxml_columns.hint_of(self.which, column["key"])
            item.setToolTip(0, f"{label}: {hint}" if hint else label)
            item.setFlags((item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                           | Qt.ItemFlag.ItemIsEditable) & ~Qt.ItemFlag.ItemIsDropEnabled)
            item.setCheckState(0, Qt.CheckState.Checked if column["on"]
                               else Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(item)
        self._update_buttons()

    def current_layout(self):
        result = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            result.append({"key": item.data(0, KEY_ROLE), "name": item.text(1).strip(),
                           "on": _ui_helpers.is_checked(item, 0)})
        return result

    def _update_buttons(self):
        item = self.tree.currentItem()
        index = self.tree.indexOfTopLevelItem(item) if item is not None else -1
        self.up_btn.setEnabled(index > 0)
        self.down_btn.setEnabled(0 <= index < self.tree.topLevelItemCount() - 1)

    def _move(self, step):
        item = self.tree.currentItem()
        if item is None:
            return
        index = self.tree.indexOfTopLevelItem(item)
        target = index + step
        if not 0 <= target < self.tree.topLevelItemCount():
            return
        self.tree.takeTopLevelItem(index)
        self.tree.insertTopLevelItem(target, item)
        self.tree.setCurrentItem(item)


class LabelTree(QWidget):
    """DXF label of one layer: columns, prefix and suffix, with a preview."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_tr("Coloană"), _tr("Prefix"), _tr("Sufix"),
                                   _tr("Exemplu")])
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumHeight(150)
        # only prefix and suffix are editable
        self.tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        self.tree.itemDoubleClicked.connect(
            lambda item, col: self.tree.editItem(item, col if col in (1, 2) else 1))
        header = self.tree.header()
        for i, width in ((0, 200), (1, 70), (2, 70)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(i, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.itemChanged.connect(self._refresh_example)
        layout.addWidget(self.tree)
        row, _all, _none = _ui_helpers.make_select_row(
            lambda: _ui_helpers.set_all_check_states(self.tree, True),
            lambda: _ui_helpers.set_all_check_states(self.tree, False))
        layout.addLayout(row)

    def set_columns(self, columns, config):
        """columns: [(key, column label)]; config: [{"key", "prefix", "suffix", "on"}]."""
        stored = {c["key"]: c for c in config}
        italic = QFont()
        italic.setItalic(True)
        self.tree.blockSignals(True)
        self.tree.clear()
        for key, name in columns:
            entry = stored.get(key)
            prefix, suffix = cgxml_export.LABEL_DEFAULTS.get(key, ("", ""))
            if entry is not None:
                prefix = entry.get("prefix", prefix)
                suffix = entry.get("suffix", suffix)
            item = QTreeWidgetItem([name, prefix, suffix, ""])
            item.setData(0, KEY_ROLE, key)
            item.setToolTip(0, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                          | Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(0, Qt.CheckState.Checked if entry and entry.get("on")
                               else Qt.CheckState.Unchecked)
            item.setFont(3, italic)
            self.tree.addTopLevelItem(item)
            self._set_example(item)
        self.tree.blockSignals(False)

    def _set_example(self, item):
        key = item.data(0, KEY_ROLE)
        item.setText(3, f"{item.text(1)}{_SAMPLES.get(key, '...')}{item.text(2)}")

    def _refresh_example(self, item, _column):
        self.tree.blockSignals(True)
        self._set_example(item)
        self.tree.blockSignals(False)

    def config(self):
        result = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            result.append({"key": item.data(0, KEY_ROLE), "prefix": item.text(1),
                           "suffix": item.text(2),
                           "on": _ui_helpers.is_checked(item, 0)})
        return result


class CgxmlDialog(BaseDialog):
    FEATURE = "convert_cgxml"

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setMinimumWidth(640)
        self._auto_base = ""
        self._setup_ui()

    # --- ui ---

    def _setup_ui(self):
        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_tab(), self.tr("General"))
        self.columns_tab_index = self.tabs.addTab(self._columns_tab(), self.tr("Coloane"))
        self.labels_tab_index = self.tabs.addTab(self._labels_tab(), self.tr("Etichete DXF"))
        self.tabs.setTabToolTip(self.labels_tab_index,
                                self.tr("Textul etichetelor, pentru formatul DXF."))
        self.body.addWidget(self.tabs)

        self.set_tip(self.tr("Fișierele CGXML se verifică după regulile ANCPI, gratuit, pe"),
                     features.WEBSITE_VALIDATOR, "cadex.ro/validator")
        self.run_btn = self.add_run_buttons(self.tr("Convertește"), self._run)

        self.remember("recursive", self.source.recursive)
        self.remember("format", self.format_combo)
        self.remember("add_to_project", self.add_to_project)
        self.remember("input", self.source.edit)
        self.remember("output", self.output_row.edit)
        self.restore_remembered()
        self._on_format_changed()
        self.source.refresh()

    def _general_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        source = QGroupBox(self.tr("Fișiere CGXML"))
        source_layout = QVBoxLayout(source)
        self.source = CgxmlSource()
        self.source.changed.connect(self._on_source_changed)
        source_layout.addWidget(self.source)
        layout.addWidget(source)

        output = QGroupBox(self.tr("Rezultat"))
        form = make_form(output)
        self.format_combo = QComboBox()
        self.format_combo.addItem(self.tr("GeoPackage (.gpkg), un fișier cu ambele layere"), "gpkg")
        self.format_combo.addItem(self.tr("Shapefile (.shp), un fișier pe layer"), "shp")
        self.format_combo.addItem(self.tr("DXF (.dxf), contururi și etichete"), "dxf")
        self.format_combo.addItem(self.tr("Excel (.xlsx), tabele fără geometrie"), "xlsx")
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        form.addRow(self.tr("Format:"), self.format_combo)
        self.output_row = PathRow("folder", self.tr("Alegeți folderul de ieșire"))
        self.output_row.edit.textChanged.connect(lambda _text: self._show_targets())
        form.addRow(self.tr("Folder de ieșire:"), self.output_row)
        self.base_edit = QLineEdit()
        self.base_edit.setToolTip(self.tr(
            "Numele fișierului rezultat, fără extensie. Urmează numele folderului "
            "sursă până când scrieți altul."))
        self.base_edit.textChanged.connect(lambda _text: self._show_targets())
        form.addRow(self.tr("Nume fișier:"), self.base_edit)
        self.targets_label = StatusLabel()
        add_field_row(form, self.targets_label)
        self._existing = ExistingFiles(self)
        self._existing.found.connect(self._show_existing)
        self.name_imobile = QLineEdit(settings_service.get("cgxml/layer_imobile"))
        self.name_imobile.textChanged.connect(lambda _text: self._show_targets())
        form.addRow(self.tr("Nume layer imobile:"), self.name_imobile)
        self.name_constructii = QLineEdit(settings_service.get("cgxml/layer_constructii"))
        self.name_constructii.textChanged.connect(lambda _text: self._show_targets())
        form.addRow(self.tr("Nume layer construcții:"), self.name_constructii)
        self.add_to_project = QCheckBox(
            self.tr("Adaugă layerele în proiect, cu simbologie și etichete"))
        self.add_to_project.setChecked(True)
        add_field_row(form, self.add_to_project)
        layout.addWidget(output)
        layout.addStretch()
        return tab

    def _columns_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(hint_label(self.tr(
            "Bifați coloanele de scris și trageți-le în ordinea dorită. Numele câmpului este "
            "cel din GeoPackage și Shapefile; dublu clic pe el pentru a-l înlocui cu numele "
            "folosit în firma dumneavoastră. Poate conține litere fără diacritice, cifre și "
            "'_', iar în Shapefile se scurtează la 10 caractere. În Excel, capul de tabel "
            "are denumirea coloanei. Alegerea se păstrează pentru conversiile următoare.")))
        inner = _inner_tabs()
        self.columns = {}
        for which, title in (("imobile", self.tr("Imobile")),
                             ("constructii", self.tr("Construcții"))):
            tree = ColumnTree(which)
            tree.set_layout(cgxml_columns.layout_from_json(
                which, settings_service.get(f"cgxml/columns_{which}")))
            self.columns[which] = tree
            inner.addTab(tree, title)
        layout.addWidget(inner)
        return tab

    def _labels_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(hint_label(self.tr(
            "Textul scris în DXF în interiorul fiecărui poligon. Fiecare coloană "
            "bifată devine un rând al etichetei (dublu clic pe prefix sau sufix "
            "pentru a-l modifica).")))
        try:
            stored = json.loads(dialog_memory.load("CgxmlDialog", "labels", "") or "{}")
        except (TypeError, ValueError):
            stored = {}
        self.labels = {}
        inner = _inner_tabs()
        for which, title in (("imobile", self.tr("Imobile")),
                             ("constructii", self.tr("Construcții"))):
            tree = LabelTree()
            config = stored.get(which) or [
                {"key": k, "on": True} for k in cgxml_export.DEFAULT_LABELS[which]]
            tree.set_columns(self._label_columns(which), config)
            self.labels[which] = tree
            inner.addTab(tree, title)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(inner)
        return tab

    def _label_columns(self, which):
        return [(key, cgxml_columns.label_of(which, key)) for key, _name in
                cgxml_columns.output_names(self.columns[which].current_layout(), False)]

    def _on_tab_changed(self, index):
        # the label columns follow the column layout
        if index != self.labels_tab_index:
            return
        for which, tree in self.labels.items():
            tree.set_columns(self._label_columns(which), tree.config())

    # --- inputs ---

    def _on_source_changed(self):
        paths = self.source.paths()
        self._update_base_name(paths)
        # whether the source exists comes from the count on a worker thread; checking here
        # would block the window on an unreachable network drive
        missing = self.source.missing()
        if paths and missing is not None and paths[0] not in missing \
                and not self.output_row.path():
            # beside the source folder, not inside it, so the result is not copied along with
            # the delivery
            self.output_row.set_path(os.path.dirname(os.path.normpath(paths[0])))

    def _update_base_name(self, paths):
        current = self.base_edit.text().strip()
        if current and current != self._auto_base:
            return
        stem = cgxml_export.source_stem(paths)
        self._auto_base = cgxml_export.BASE_PREFIX + stem if stem else "cgxml"
        self.base_edit.setText(self._auto_base)

    def _on_format_changed(self, *_args):
        fmt_key = self.format_combo.currentData()
        self.add_to_project.setEnabled(fmt_key in ("gpkg", "shp"))
        self.tabs.setTabEnabled(self.labels_tab_index, fmt_key == "dxf")
        self._show_targets()

    def _show_targets(self):
        """List the files the run will write; _show_existing() then warns about those that
        exist, checked on a worker thread because the folder can be on a network drive."""
        output_dir = self.output_row.path()
        base = self.base_edit.text().strip()
        if not output_dir or not base or not _FILE_NAME.match(base):
            self._existing.check([])
            self.targets_label.set_status("")
            return
        targets = self._targets(self.format_combo.currentData(), output_dir, base)
        names = ", ".join(os.path.basename(t) for t in targets)
        self.targets_label.set_status(fmt.plural(
            len(targets), self.tr("Se va crea {0}."), self.tr("Se vor crea {0}.")).format(names))
        self._existing.check(targets)

    def _show_existing(self, existing):
        if existing:
            self.targets_label.set_status(fmt.plural(
                len(existing), self.tr("{0} există deja și va fi înlocuit."),
                self.tr("{0} există deja și vor fi înlocuite.")).format(
                    ", ".join(os.path.basename(t) for t in existing)), "warning")

    # --- run ---

    def _layer_names(self):
        return {"imobile": self.name_imobile.text().strip() or self.tr("Imobile"),
                "constructii": self.name_constructii.text().strip() or self.tr("Construcții")}

    def _targets(self, fmt_key, output_dir, base):
        if fmt_key == "shp":
            names = self._layer_names()
            return [os.path.join(output_dir, f"{base}_{cgxml_export.table_name(names[w])}.shp")
                    for w in ("imobile", "constructii")]
        return [os.path.join(output_dir, base + cgxml_export.EXTENSIONS[fmt_key])]

    def _layout_problems(self, fmt_key, which, layout):
        """GeoPackage and Shapefile write the field names, Excel heads its columns with the
        labels and DXF writes no columns."""
        if fmt_key in ("gpkg", "shp"):
            return cgxml_columns.layout_problems(layout, fmt_key == "shp", which,
                                                 geopackage=fmt_key == "gpkg")
        if fmt_key == "xlsx" and not cgxml_columns.output_names(layout, False):
            return [self.tr("Nu este bifată nicio coloană.")]
        return []

    def _same_place(self, fmt_key, names):
        """The warning for layer names the output would store in the same place, else ""."""
        first, second = names["imobile"], names["constructii"]
        stored = cgxml_export.stored_layer_name(fmt_key, first)
        if stored.lower() != cgxml_export.stored_layer_name(fmt_key, second).lower():
            return ""
        if first.lower() == second.lower():
            return self.tr("Layerele de imobile și de construcții au nevoie de nume diferite.")
        return self.tr(
            "Numele '{0}' și '{1}' devin același nume în fișierul rezultat, '{2}', unde nu "
            "contează literele mari, diacriticele și semnele de punctuație. Alegeți nume "
            "diferite pentru layerele de imobile și de construcții.").format(
                first, second, stored)

    def _run(self):
        paths = self.source.paths()
        # counted on a worker thread when the path was set; count() waits for a count still
        # in progress, so the run lists no folder here
        count = self.source.count() if paths else None
        if not paths or self.source.missing():
            self.warn(self.tr("Alegeți folderul, arhiva ZIP sau fișierele CGXML."),
                      self.source.edit)
            return
        if count == 0:
            self.warn(self.tr("Nu s-a găsit niciun fișier .cgxml în sursa aleasă."),
                      self.source.edit)
            return
        found = self.source.found()
        if not found:
            # every archive or folder of the source is damaged or cannot be read, and the
            # status line under the field names them
            self.warn(self.source.status.text(), self.source.edit)
            return
        output_dir = self.output_row.path()
        if not output_dir:
            self.warn(self.tr("Alegeți folderul de ieșire."), self.output_row.edit)
            return
        base = self.base_edit.text().strip()
        if not base or not _FILE_NAME.match(base):
            self.warn(self.tr("Numele fișierului rezultat trebuie completat și nu poate "
                              "conține caracterele < > : \" / \\ | ? *"), self.base_edit)
            return
        fmt_key = self.format_combo.currentData()
        layouts = {w: tree.current_layout() for w, tree in self.columns.items()}
        for which, title in (("imobile", self.tr("imobile")),
                             ("constructii", self.tr("construcții"))):
            problems = self._layout_problems(fmt_key, which, layouts[which])
            if problems:
                self.warn(self.tr("Coloanele pentru {0} nu pot fi scrise: {1}")
                          .format(title, " ".join(problems)), self.columns[which].tree)
                return
        names = self._layer_names()
        same = self._same_place(fmt_key, names)
        if same:
            self.warn(same, self.name_constructii)
            return
        if fmt_key == "dxf" and not deps.ensure("ezdxf", self):
            return

        targets = self._targets(fmt_key, output_dir, base)
        if not self.confirm_overwrite(targets) or not self.release_files(targets):
            return

        self._save_choices(layouts, names)
        label_config = {w: tree.config() for w, tree in self.labels.items()}
        self.start_task(cgxml_export.ConversionTask(
            input_paths=paths, recursive=self.source.recursive.isChecked(),
            output_dir=output_dir, base_name=base, fmt=fmt_key, layouts=layouts,
            layer_names=names, label_config=label_config,
            finished_cb=self._on_finished),
            fmt.plural(found, self.tr("Se convertește {0}..."),
                       self.tr("Se convertesc {0}...")).format(
                fmt.count(found, self.tr("fișier"), self.tr("fișiere"))))

    def _save_choices(self, layouts, names):
        self.save_remembered()
        for which, layout in layouts.items():
            settings_service.set_(f"cgxml/columns_{which}",
                                  cgxml_columns.layout_to_json(layout))
        settings_service.set_("cgxml/layer_imobile", names["imobile"])
        settings_service.set_("cgxml/layer_constructii", names["constructii"])
        dialog_memory.save("CgxmlDialog", "labels", json.dumps(
            {w: tree.config() for w, tree in self.labels.items()}, ensure_ascii=False))

    # --- result ---

    def _on_finished(self, ok, task):
        self.task_done()
        # a cancel that came after the outputs were put in place leaves a complete run
        if not ok and task.isCanceled():
            self.notice(self.tr("Conversia a fost anulată."))
            return
        if not ok:
            self.fail(self.tr("Conversia nu s-a putut finaliza:\n\n{0}").format(
                task.error_msg or self.tr("eroare necunoscută")))
            return

        added = []
        if task.fmt in ("gpkg", "shp"):
            added = self._load_layers(task)
        self._show_targets()

        read_count = task.read_count
        headline = fmt.plural(read_count, self.tr("S-a citit {0}: {1} și {2}."),
                              self.tr("S-au citit {0}: {1} și {2}.")).format(
            fmt.count(read_count, self.tr("fișier"), self.tr("fișiere")),
            fmt.count(len(task.imobile), self.tr("imobil"), self.tr("imobile")),
            fmt.count(len(task.constructii), self.tr("construcție"), self.tr("construcții")))
        notes = []
        if task.fmt in ("gpkg", "shp", "dxf"):
            unmapped = (cgxml_export.unmapped_notes("imobile", task.imobile)
                        + cgxml_export.unmapped_notes("constructii", task.constructii))
            if unmapped:
                notes.extend(unmapped)
                notes.append(self.tr("Formatul Excel include și înregistrările fără geometrie."))
        if task.fmt in ("gpkg", "shp") and "constructii" not in task.outputs:
            notes.append(self.tr("Sursa nu are construcții cu coordonate, deci nu s-a scris un "
                                 "layer de construcții."))
        if task.errors:
            notes.append(fmt.plural(
                len(task.errors), self.tr("{0} nu a putut fi citit."),
                self.tr("{0} nu au putut fi citite.")).format(
                    fmt.count(len(task.errors), self.tr("fișier"), self.tr("fișiere"))))
        if task.invalid:
            notes.append(fmt.plural(
                task.invalid,
                self.tr("{0} are geometrie invalidă, păstrată așa cum este în fișier."),
                self.tr("{0} au geometrie invalidă, păstrată așa cum este în fișier."),
            ).format(fmt.count(task.invalid, self.tr("poligon"), self.tr("poligoane"))))
        if task.truncated:
            notes.append(cgxml_export.truncated_note(task.truncated))
        notes.extend(task.write_notes)
        report = self.report_notes(task)
        notes.extend(report)
        no_names = siruta.problem()
        if no_names:
            notes.append(no_names)
        if added:
            notes.append(fmt.plural(
                len(added), self.tr("Layer adăugat în proiect: {0}."),
                self.tr("Layere adăugate în proiect: {0}."),
            ).format(", ".join(layer.name() for layer in added)))
        details = "\n".join(f"{f}: {m}" for f, m in task.errors + task.warnings)
        if details:
            notes.append(self.tr("Lista completă a problemelor este în detalii."))
        self.show_result(headline, notes=notes, file_path=task.outputs.get("file"),
                         details=details or None,
                         warning=bool(task.errors or task.write_notes or report or no_names),
                         layers=added)

    def _load_layers(self, task):
        """Style and mark the output layers, save that with their files, and add them to the
        project when asked. The layers added, or []."""
        layers = []
        layouts = task.layouts
        batch = roles.new_batch()
        for which in ("imobile", "constructii"):
            uri = task.outputs.get(which)
            if not uri:
                continue
            layer = QgsVectorLayer(
                uri, cgxml_export.display_name(task.layer_names[which], task.base_name), "ogr")
            if not layer.isValid():
                self.log(f"Layer rezultat invalid: {uri}")
                continue
            names = cgxml_columns.output_names(layouts[which], task.fmt == "shp")
            layer_style.style_cadastral(layer, which,
                                        layer_style.cadastral_label_parts(which, names))
            roles.mark_batch(layer, batch)
            roles.mark_columns(layer, names)
            layer_style.store_with_file(layer)
            layers.append(layer)
        if not self.add_to_project.isChecked():
            return []
        return _ui_helpers.add_layers(layers, task.base_name, report=False)
