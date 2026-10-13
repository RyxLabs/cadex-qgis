# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Comparator window: two layers or two sets of CGXML files."""

import json
import os

from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QTabWidget, QVBoxLayout, QWidget,
)
from qgis.core import (  # type: ignore
    QgsMapLayerProxyModel, QgsMemoryProviderUtils, QgsProject,
)
from qgis.gui import QgsCollapsibleGroupBox, QgsMapLayerComboBox  # type: ignore

from . import _ui_helpers
from .base_dialog import BaseDialog
from .widgets import (
    CgxmlSource, CheckList, PageStack, PathRow, SegmentedControl, StatusLabel,
    data_field_names, fill_field_combo, hint_label, make_form,
)
from .. import features
from ..services import (
    cgxml_columns, cgxml_compare, compare_service, dialog_memory, fmt, layer_sources,
    layer_style, roles, settings_service, siruta, stereo70,
)
from ..services.compare_service import (
    ADDED, ADDED_COLOR, CHANGED, CHANGED_COLOR, DELETED, DELETED_COLOR,
)
from ..services.layer_sources import LayerSnapshot

LAYERS, CGXML = 0, 1
# the case choice of the layer comparison; the CGXML ones are keyed by their match mode
FOLD_LAYERS = "layers"
# the colors of the CGXML result rows, by label
CGXML_COLORS = {**compare_service.CHANGE_COLORS,
                cgxml_compare.IMOBILE_ADDED: ADDED_COLOR,
                cgxml_compare.IMOBILE_DELETED: DELETED_COLOR,
                cgxml_compare.IMOBILE_CHANGED: CHANGED_COLOR,
                cgxml_compare.BUILDINGS_ADDED: ADDED_COLOR,
                cgxml_compare.BUILDINGS_DELETED: DELETED_COLOR,
                cgxml_compare.BUILDINGS_CHANGED: CHANGED_COLOR}


def _columns_layout(which):
    """The column layout saved by Conversie CGXML."""
    return cgxml_columns.layout_from_json(which, settings_service.get(f"cgxml/columns_{which}"))


def _style_result(layer, color, kind_field=None):
    """A result layer in its category's color; changed records by their kind of change, so
    the legend can hide each kind."""
    if kind_field:
        layer_style.style_categories(layer, kind_field, compare_service.change_categories())
    else:
        layer_style.style_fill(layer, color)


def _add_features(layer, features_):
    """Add features to a memory layer: (how many it refused, the provider's first error).
    One at a time, since the memory provider adds none after the first it refuses."""
    provider = layer.dataProvider()
    refused, error = 0, ""
    for feature in features_:
        if not provider.addFeatures([feature])[0]:
            refused += 1
            error = error or provider.lastError()
    layer.updateExtents()
    return refused, error


def _first_listed(names, *candidates):
    """The first of candidates that is one of names, else None."""
    return next((name for name in candidates if name and name in names), None)


def _key_by_name(names):
    """The column named like an imobil ID ('fields/id' in Setări), else like an IE."""
    for role in ("fields/id", "fields/ie"):
        name = roles.pick_field(names, settings_service.patterns(role))
        if name:
            return name
    return None


class CompareDialog(BaseDialog):
    FEATURE = "compare_layers"

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setMinimumWidth(660)
        self._setup_ui()

    # --- ui ---

    def _setup_ui(self):
        # the layers whose field changes refresh the lists, by list
        self._followed = {}
        # (ids of the layers, old key, new key) picked by hand; they hold while one of those
        # layers stays chosen, after that the keys are preselected again
        self._key_choice = None
        # {"old" / "new": id of the layer whose columns the key combo lists}
        self._key_layers = {"old": None, "new": None}
        # {layer id: its key column, renamed or deleted while the window is open}
        self._lost_keys = {}
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(self.tr("Se compară:")))
        self.mode = SegmentedControl(
            [self.tr("Două Layere"), self.tr("Două Seturi de Fișiere CGXML")],
            [self.tr("Două layere din proiect, potrivite după o coloană cheie"),
             self.tr("Două livrări, două exporturi eTerra sau un export eTerra și o livrare, "
                     "fiecare dintr-un folder, o arhivă ZIP sau fișiere alese")])
        mode_row.addWidget(self.mode)
        mode_row.addStretch()
        self.body.addLayout(mode_row)

        self.pages = PageStack()
        self.layers_page = self._layers_page()
        self.cgxml_page = self._cgxml_page()
        self.pages.addWidget(self.layers_page)
        self.pages.addWidget(self.cgxml_page)
        self.body.addWidget(self.pages)

        criteria = QGroupBox(self.tr("Criterii"))
        criteria_layout = QVBoxLayout(criteria)
        geom_line = QHBoxLayout()
        self.compare_geometry = QCheckBox(self.tr("Compară și geometria, cu toleranța"))
        self.compare_geometry.setChecked(True)
        self.compare_geometry.setToolTip(self.tr(
            "Un imobil cu aceleași atribute, dar cu limita mutată, apare la 'Modificate'. "
            "Limita se consideră mutată când suprafața dintre limita veche și cea nouă, sau "
            "diferența de suprafață, depășește toleranța."))
        geom_line.addWidget(self.compare_geometry)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.0, 10000.0)
        self.tolerance.setDecimals(2)
        self.tolerance.setValue(0.1)
        self.tolerance.setSuffix(" mp")
        self.compare_geometry.toggled.connect(self.tolerance.setEnabled)
        geom_line.addWidget(self.tolerance)
        geom_line.addStretch()
        criteria_layout.addLayout(geom_line)
        self.buildings = QCheckBox(self.tr("Compară și construcțiile fiecărui imobil, după "
                                           "numărul lor"))
        self.buildings.setChecked(True)
        criteria_layout.addWidget(self.buildings)
        self.fold_text = QCheckBox(self.tr("Ignoră majusculele și diacriticele"))
        self.fold_text.setToolTip(self.tr(
            "Cu bifa pusă, 'Ştefan' și 'ȘTEFAN' sunt aceeași valoare. Spațiile de la "
            "capete și cele duble nu contează niciodată."))
        criteria_layout.addWidget(self.fold_text)
        # each mode has its own setting, shown in fold_text; matching by IE is mostly an eTerra
        # export against a delivery, typed by different people, so there it starts ticked
        self._fold_by_kind = {FOLD_LAYERS: QCheckBox(), cgxml_compare.FILENAME: QCheckBox(),
                              cgxml_compare.IE: QCheckBox()}
        self._fold_by_kind[cgxml_compare.IE].setChecked(True)
        self._fold_kind = None
        self.body.addWidget(criteria)

        out = QGroupBox(self.tr("Rezultat"))
        out_form = make_form(out)
        self.excel_row = PathRow("save", self.tr("Salvați raportul Excel"),
                                 self.tr("Excel (*.xlsx)"), ".xlsx")
        self.excel_row.edit.setPlaceholderText(self.tr("Opțional"))
        out_form.addRow(self.tr("Raport Excel:"), self.excel_row)
        # each mode keeps its own report path, so one mode's run never offers to overwrite
        # the other's report; the field shows the current mode's
        self._excel_paths = {LAYERS: QLineEdit(), CGXML: QLineEdit()}
        self._excel_mode = None
        boxes = QHBoxLayout()
        self.layer_added = QCheckBox(self.tr("Apărute"))
        self.layer_deleted = QCheckBox(self.tr("Dispărute"))
        self.layer_changed = QCheckBox(self.tr("Modificate"))
        for box, color, tooltip in (
                (self.layer_added, ADDED_COLOR,
                 self.tr("Ce există doar în versiunea nouă")),
                (self.layer_deleted, DELETED_COLOR,
                 self.tr("Ce există doar în versiunea veche")),
                (self.layer_changed, CHANGED_COLOR,
                 self.tr("Ce există în ambele versiuni și diferă, cu ce s-a modificat"))):
            box.setChecked(True)
            box.setIcon(_ui_helpers.swatch_icon(color))
            box.setToolTip(tooltip)
            boxes.addWidget(box)
            boxes.addSpacing(8)
        boxes.addStretch()
        out_form.addRow(self.tr("Layere în proiect:"), boxes)
        self.body.addWidget(out)
        self.body.addStretch()

        self.set_tip(self.tr("Comparația completă a două livrări, cu persoane, înscrieri și "
                             "părțile II și III, o faceți pe"),
                     features.WEBSITE_COMPARATOR, "cadex.ro/comparator")
        self.run_btn = self.add_run_buttons(self.tr("Compară"), self._run)

        self._preselect_versions()
        # until the user picks the layers, each opening starts on the latest versions, also
        # when they were converted while the window was closed
        self._layers_chosen = False
        for combo in (self.old_combo, self.new_combo):
            combo.activated.connect(self._on_layers_chosen)
        self.old_combo.layerChanged.connect(self._refresh)
        self.new_combo.layerChanged.connect(self._refresh)
        self.old_key.currentIndexChanged.connect(self._refresh_fields)
        for combo in (self.old_key, self.new_key):
            combo.activated.connect(self._on_key_chosen)
        self.new_key.currentIndexChanged.connect(self._refresh_fields)
        self.mode.changed.connect(self._on_mode_changed)
        self.match_combo.currentIndexChanged.connect(self._show_match_hint)
        # restored before the mode and the match: changing those shows the stored values in
        # fold_text and the report field
        for kind, store in self._fold_by_kind.items():
            self.remember(f"fold_{kind}", store)
        self.remember("excel_layers", self._excel_paths[LAYERS])
        self.remember("excel_cgxml", self._excel_paths[CGXML])
        self.remember("mode", self.mode)
        self.remember("compare_geometry", self.compare_geometry)
        self.remember("tolerance", self.tolerance)
        self.remember("buildings", self.buildings)
        self.remember("layer_added", self.layer_added)
        self.remember("layer_deleted", self.layer_deleted)
        self.remember("layer_changed", self.layer_changed)
        self.remember("old_cgxml", self.old_source.edit)
        self.remember("new_cgxml", self.new_source.edit)
        self.remember("old_recursive", self.old_source.recursive)
        self.remember("new_recursive", self.new_source.recursive)
        self.remember("match", self.match_combo)
        self.restore_remembered()
        self._fold_kind = None
        self.tolerance.setEnabled(self.compare_geometry.isChecked())
        self.buildings.toggled.connect(self._on_buildings_toggled)
        self._on_buildings_toggled(self.buildings.isChecked())
        self._refresh_building_fields()
        self._refresh()
        self._show_match_hint()
        self.old_source.refresh()
        self.new_source.refresh()
        self._on_mode_changed()

    def showEvent(self, event):
        if not self._layers_chosen:
            self._preselect_versions()
        super().showEvent(event)

    def _on_layers_chosen(self, *_args):
        self._layers_chosen = True

    def _preselect_versions(self):
        """Start on two imobile layers: the latest Conversie CGXML run that has an earlier
        version (_earlier_version), else the first two named like imobile, else any two
        layers."""
        layers = [self.old_combo.layer(i) for i in range(self.old_combo.count())]
        layers = [layer for layer in layers if layer is not None and not roles.is_report(layer)]
        converted = sorted((layer for layer in layers if roles.batch_of(layer)
                            and roles.column_of(layer, "id")
                            and not roles.column_of(layer, "nr_constr")),
                           key=roles.batch_of)
        versions = []
        keys = [layer_sources.imobil_keys([layer]) for layer in converted]
        for i in range(len(converted) - 1, 0, -1):
            earlier = self._earlier_version(converted, keys, i)
            if earlier is not None:
                versions = [converted[earlier], converted[i]]
                break
        patterns = settings_service.patterns("roles/imobile")
        named = [layer for layer in layers if roles.layer_matches(layer.name(), patterns)]
        for pair in (versions, named[:2], layers[:2]):
            if len(pair) == 2:
                self.old_combo.setLayer(pair[0])
                self.new_combo.setLayer(pair[1])
                return

    @staticmethod
    def _earlier_version(converted, keys, i):
        """The index of the nearest run before converted[i] that holds a version of its
        imobile (layer_sources.same_imobile), None when there is none. A run with the very
        same imobile is taken only when no other is found, and not under the same name:
        that is most often the same files converted again."""
        candidates = [j for j in range(i - 1, -1, -1)
                      if layer_sources.same_imobile(keys[j], keys[i])]
        other_imobile = [j for j in candidates if keys[j] != keys[i]]
        other_name = [j for j in candidates if converted[j].name() != converted[i].name()]
        return (other_imobile or other_name or [None])[0]

    def _layers_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layers = QGroupBox(self.tr("Layere de Comparat"))
        grid = QGridLayout(layers)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        for column, text in ((1, self.tr("Versiunea veche")), (3, self.tr("Versiunea nouă"))):
            grid.addWidget(QLabel(text), 0, column)
        grid.addWidget(QLabel(self.tr("Layer:")), 1, 0)
        self.old_combo = QgsMapLayerComboBox()
        self.old_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        grid.addWidget(self.old_combo, 1, 1)
        swap = _ui_helpers.small_button("⇄", self.tr("Inversează layerul vechi cu cel nou"))
        swap.clicked.connect(self._swap)
        grid.addWidget(swap, 1, 2)
        self.new_combo = QgsMapLayerComboBox()
        self.new_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        grid.addWidget(self.new_combo, 1, 3)
        grid.addWidget(QLabel(self.tr("Coloana cheie:")), 2, 0)
        self.old_key = QComboBox()
        grid.addWidget(self.old_key, 2, 1)
        self.new_key = QComboBox()
        grid.addWidget(self.new_key, 2, 3)
        self.key_hint = StatusLabel()
        grid.addWidget(self.key_hint, 3, 1, 1, 3)
        self.eterra_hint = StatusLabel()
        grid.addWidget(self.eterra_hint, 4, 1, 1, 3)
        layout.addWidget(layers)

        columns = QGroupBox(self.tr("Coloane de Comparat"))
        columns_layout = QVBoxLayout(columns)
        columns_layout.addWidget(QLabel(self.tr("Coloanele prezente în ambele layere:")))
        self.field_list = CheckList(self.tr("Alegeți două layere cu coloane comune."), height=100)
        columns_layout.addWidget(self.field_list)
        layout.addWidget(columns)
        layout.addWidget(self._buildings_box())
        return page

    def _buildings_box(self):
        box = QgsCollapsibleGroupBox(self.tr("Construcții"))
        box.setObjectName("compare_buildings")
        box.setCheckable(True)
        box.setChecked(False)
        # collapsed follows the check box, so the saved collapsed state must not apply
        box.setSaveCollapsedState(False)
        box.setCollapsed(True)
        box.toggled.connect(lambda on: box.setCollapsed(not on))
        box.collapsedStateChanged.connect(lambda *_: self.fit_height())
        # a click is the user's choice; from then on choosing converted layers leaves the box
        # as the user set it
        box.clicked.connect(self._on_buildings_chosen)
        self.buildings_box = box
        layout = QVBoxLayout(box)
        layout.addWidget(hint_label(self.tr(
            "Construcțiile se potrivesc după imobil și numărul lor, de exemplu 1203 C2. Pentru "
            "layerele create cu Conversie CGXML, layerele și coloanele se preselectează.")))
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        for column, text in ((1, self.tr("Versiunea veche")), (2, self.tr("Versiunea nouă"))):
            grid.addWidget(QLabel(text), 0, column)
        self.building_combos = {}
        self.building_keys = {}
        for column, side in ((1, "old"), (2, "new")):
            combo = QgsMapLayerComboBox()
            combo.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
            combo.setAllowEmptyLayer(True)
            combo.setLayer(None)
            combo.layerChanged.connect(self._refresh_building_fields)
            grid.addWidget(combo, 1, column)
            self.building_combos[side] = combo
            imobil, number = QComboBox(), QComboBox()
            imobil.setToolTip(self.tr("Coloana cu imobilul construcției. Fără ea, numărul "
                                      "construcției trebuie să fie unic în tot layerul."))
            number.setToolTip(self.tr("Coloana cu numărul construcției, de exemplu C1."))
            for combo_box in (imobil, number):
                combo_box.currentIndexChanged.connect(self._refresh_building_list)
            grid.addWidget(imobil, 2, column)
            grid.addWidget(number, 3, column)
            self.building_keys[side] = (imobil, number)
        for row, text in ((1, self.tr("Layer:")), (2, self.tr("Imobilul:")),
                          (3, self.tr("Nr. construcției:"))):
            grid.addWidget(QLabel(text), row, 0)
        layout.addLayout(grid)
        layout.addWidget(QLabel(self.tr("Coloanele prezente în ambele layere:")))
        self.building_fields = CheckList(self.tr("Alegeți două layere de construcții."),
                                         height=80)
        layout.addWidget(self.building_fields)
        self._buildings_chosen = False
        return box

    def _on_buildings_chosen(self, *_args):
        self._buildings_chosen = True

    @staticmethod
    def _converted_buildings(layer):
        """The construcții layer converted together with an imobile layer, else None."""
        batch = roles.batch_of(layer) if layer is not None else ""
        if not batch:
            return None
        for other in QgsProject.instance().mapLayers().values():
            if (other is not layer and roles.batch_of(other) == batch
                    and roles.column_of(other, "nr_constr")):
                return other
        return None

    def _preselect_buildings(self):
        """Take the construcții layers converted with the chosen imobile layers. Until the
        user ticks or unticks the section, it is ticked only when both are found and can be
        paired: with an eTerra export by the IE of their imobil, since its cadastral numbers
        are not the IDs of a delivery."""
        found = {side: self._converted_buildings(combo.currentLayer())
                 for side, combo in (("old", self.old_combo), ("new", self.new_combo))}
        eterra = any(cgxml_compare.is_eterra_export(combo.currentLayer())
                     for combo in (self.old_combo, self.new_combo))
        imobil_key = "ie_imobil" if eterra else "id"
        usable = (all(found.values()) and found["old"] is not found["new"]
                  and all(roles.column_of(layer, imobil_key) for layer in found.values()))
        if usable:
            for side, layer in found.items():
                self.building_combos[side].setLayer(layer)
                imobil, number = self.building_keys[side]
                for combo_box, key in ((imobil, imobil_key), (number, "nr_constr")):
                    index = combo_box.findData(roles.column_of(layer, key))
                    if index >= 0:
                        combo_box.setCurrentIndex(index)
        if not self._buildings_chosen:
            self.buildings_box.setChecked(usable)

    def _refresh_building_fields(self, *_args):
        self._follow_fields("buildings", [combo.currentLayer()
                                          for combo in self.building_combos.values()],
                            self._refresh_building_fields)
        for side, combo in self.building_combos.items():
            layer = combo.currentLayer()
            # a row number is unique on every row and would pair no building
            names = data_field_names([layer]) if layer is not None else []
            imobil, number = self.building_keys[side]
            fill_field_combo(imobil, names, None, self.tr("(fără)"), imobil.currentData())
            fill_field_combo(number, names, None, self.tr("(alegeți)"), number.currentData())
        self._refresh_building_list()

    def _building_key(self, side):
        imobil, number = self.building_keys[side]
        if not number.currentData():
            return None
        return [k for k in (imobil.currentData(), number.currentData()) if k]

    def _refresh_building_list(self, *_args):
        old = self.building_combos["old"].currentLayer()
        new = self.building_combos["new"].currentLayer()
        if old is None or new is None:
            self.building_fields.set_names([])
            return
        keys = set((self._building_key("old") or []) + (self._building_key("new") or []))
        new_names = set(data_field_names([new]))
        chosen = cgxml_compare.converted_columns(old, new, cgxml_compare.BUILDING_COLUMNS)
        self.building_fields.set_names(
            [name for name in data_field_names([old]) if name in new_names and name not in keys],
            True if chosen is None else chosen)

    def _follow_fields(self, name, layers, slot):
        """Call slot when the fields of one of layers change, replacing the layers followed
        under name: a column renamed or deleted while the window is open is removed from the
        lists."""
        layers = list({layer.id(): layer for layer in layers if layer is not None}.values())
        followed = self._followed.get(name, [])
        if len(followed) == len(layers) and all(a is b for a, b in zip(followed, layers)):
            return
        for layer in followed:
            try:
                layer.updatedFields.disconnect(slot)
            except (RuntimeError, TypeError):
                pass  # the layer was deleted, or never connected
        for layer in layers:
            layer.updatedFields.connect(slot)
        self._followed[name] = layers

    def _cgxml_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        files = QGroupBox(self.tr("Fișiere de Comparat"))
        form = make_form(files)
        self.old_source = CgxmlSource()
        form.addRow(self.tr("Sursa veche (înainte):"), self.old_source)
        self.new_source = CgxmlSource()
        form.addRow(self.tr("Sursa nouă (după):"), self.new_source)
        self.match_combo = QComboBox()
        for mode in cgxml_compare.MODES:
            self.match_combo.addItem(self.tr(cgxml_compare.MODE_NAMES[mode]), mode)
        form.addRow(self.tr("Potrivirea fișierelor:"), self.match_combo)
        self.match_hint = hint_label("")
        form.addRow("", self.match_hint)
        layout.addWidget(files)

        columns = QGroupBox(self.tr("Coloane de Comparat"))
        columns_layout = QVBoxLayout(columns)
        columns_layout.addWidget(QLabel(self.tr(
            "Coloanele comparate pentru fiecare imobil și construcție găsite în ambele surse:")))
        self.cgxml_column_tabs = QTabWidget()
        self.cgxml_column_tabs.setDocumentMode(True)
        self.cgxml_fields = CheckList("", height=120)
        self.cgxml_fields.set_items(self._column_items(
            "cgxml_columns", cgxml_compare.comparable_columns(), cgxml_compare.DEFAULT_COLUMNS))
        self.cgxml_building_fields = CheckList("", height=120)
        self.cgxml_building_fields.set_items(self._column_items(
            "cgxml_building_columns", cgxml_compare.comparable_building_columns(),
            cgxml_compare.BUILDING_COLUMNS))
        self.cgxml_column_tabs.addTab(self.cgxml_fields, self.tr("Imobile"))
        self.cgxml_column_tabs.addTab(self.cgxml_building_fields, self.tr("Construcții"))
        columns_layout.addWidget(self.cgxml_column_tabs)
        layout.addWidget(columns)
        return page

    def _column_items(self, memory_key, columns, defaults):
        """(label, key, checked, tooltip) items, checked as at the last run."""
        try:
            stored = json.loads(dialog_memory.load(type(self).__name__, memory_key, "") or "null")
        except (TypeError, ValueError):
            stored = None
        ticked = set(stored) if isinstance(stored, list) else set(defaults)
        return [(self.tr(label), key, key in ticked, key) for key, label in columns]

    def save_remembered(self):
        self._excel_paths[self.mode.index()].setText(self.excel_row.edit.text())
        if self._fold_kind is not None:
            self._fold_by_kind[self._fold_kind].setChecked(self.fold_text.isChecked())
        super().save_remembered()

    def _sync_fold(self):
        """Show the case choice of the current kind of comparison, keeping the previous one."""
        kind = (FOLD_LAYERS if self.mode.index() == LAYERS
                else self.match_combo.currentData() or cgxml_compare.FILENAME)
        if kind == self._fold_kind:
            return
        if self._fold_kind is not None:
            self._fold_by_kind[self._fold_kind].setChecked(self.fold_text.isChecked())
        self.fold_text.setChecked(self._fold_by_kind[kind].isChecked())
        self._fold_kind = kind

    def _on_mode_changed(self, *_args):
        mode = self.mode.index()
        if self._excel_mode != mode:
            if self._excel_mode is not None:
                self._excel_paths[self._excel_mode].setText(self.excel_row.edit.text())
            self.excel_row.edit.setText(self._excel_paths[mode].text())
            self._excel_mode = mode
        self._sync_fold()
        cgxml = mode == CGXML
        self.pages.setCurrentWidget(self.cgxml_page if cgxml else self.layers_page)
        self.buildings.setVisible(cgxml)
        self.fit_height()

    def _on_buildings_toggled(self, on):
        self.cgxml_column_tabs.setTabEnabled(1, on)

    def _show_match_hint(self, *_args):
        mode = self.match_combo.currentData() or cgxml_compare.FILENAME
        self.match_hint.setText(self.tr(cgxml_compare.MODE_HINTS[mode]))
        self._sync_fold()

    # --- layer fields ---

    def _swap(self):
        old, new = self.old_combo.currentLayer(), self.new_combo.currentLayer()
        key_old, key_new = self.old_key.currentData(), self.new_key.currentData()
        if self._key_choice is not None:
            layers, chosen_old, chosen_new = self._key_choice
            self._key_choice = (layers, chosen_new, chosen_old)
        combos = (self.old_combo, self.new_combo, self.old_key, self.new_key)
        for combo in combos:
            combo.blockSignals(True)
        self.old_combo.setLayer(new)
        self.new_combo.setLayer(old)
        for combo in combos:
            combo.blockSignals(False)
        # the window opens again on this order
        self._layers_chosen = True
        self._swap_buildings()
        self._refresh()
        for combo, key in ((self.old_key, key_new), (self.new_key, key_old)):
            index = combo.findData(key) if key else -1
            if index >= 0:
                combo.setCurrentIndex(index)

    def _swap_buildings(self):
        """Swap the construcții layers and their imobil and number columns, as _swap() swaps
        the imobile layers."""
        combos = self.building_combos
        layers = {side: combo.currentLayer() for side, combo in combos.items()}
        keys = {side: [combo.currentData() for combo in self.building_keys[side]]
                for side in combos}
        for combo in combos.values():
            combo.blockSignals(True)
        combos["old"].setLayer(layers["new"])
        combos["new"].setLayer(layers["old"])
        for combo in combos.values():
            combo.blockSignals(False)
        self._refresh_building_fields()
        for side, other in (("old", "new"), ("new", "old")):
            for combo, key in zip(self.building_keys[side], keys[other]):
                index = combo.findData(key) if key is not None else -1
                if index >= 0:
                    combo.setCurrentIndex(index)

    def _refresh(self, *_args):
        old, new = self.old_combo.currentLayer(), self.new_combo.currentLayer()
        self._follow_fields("layers", (old, new), self._refresh)
        key_old, key_new = self._keys_for(old, new)
        old_names = data_field_names([old]) if old else []
        new_names = data_field_names([new]) if new else []
        self._fill_key("old", old, old_names, key_old, self.old_key.currentData(),
                       _key_by_name(old_names))
        self._fill_key("new", new, new_names, key_new, self.new_key.currentData(),
                       self.old_key.currentData(), _key_by_name(new_names))
        self._refresh_fields()
        self._preselect_buildings()

    def _fill_key(self, side, layer, names, *candidates):
        """Fill the key combo of side ("old" or "new") with names, the columns of layer, and
        choose the first of candidates it lists. When the key column chosen on that layer was
        renamed or deleted, the combo stays unchosen until the user picks a column or the
        column is back, so that a run never pairs the records by another column."""
        combo = self.old_key if side == "old" else self.new_key
        layer_id = layer.id() if layer is not None else None
        key = combo.currentData() if self._key_layers[side] == layer_id else None
        if layer_id is not None:
            if key and key not in names:
                self._lost_keys[layer_id] = key
            elif key or self._lost_keys.get(layer_id) in names:
                self._lost_keys.pop(layer_id, None)
        lost = layer_id in self._lost_keys
        fill_field_combo(combo, names, keep=None if lost else _first_listed(names, *candidates))
        if lost:
            combo.setCurrentIndex(-1)
        self._key_layers[side] = layer_id

    def _lost_key_text(self, old, new) -> str:
        """The hint for a key combo left unchosen because its column is gone, else ""."""
        for layer, combo in ((old, self.old_key), (new, self.new_key)):
            lost = self._lost_keys.get(layer.id()) if layer is not None else None
            if lost and not combo.currentData():
                return self.tr("Coloana cheie '{0}' nu mai există în layerul '{1}'. Alegeți "
                               "coloana cheie.").format(lost, layer.name())
        return ""

    def _keys_for(self, old, new):
        """The key columns of two layers: those picked by hand while one of the layers they
        were picked for is chosen, else compare_service.key_columns()."""
        chosen = {layer.id() for layer in (old, new) if layer is not None}
        if self._key_choice is not None:
            layers, key_old, key_new = self._key_choice
            if layers & chosen:
                return key_old, key_new
            self._key_choice = None
        if old is None or new is None:
            return None, None
        return compare_service.key_columns(old, new)

    def _on_key_chosen(self, *_args):
        layers = {layer.id() for layer in (self.old_combo.currentLayer(),
                                           self.new_combo.currentLayer()) if layer is not None}
        self._key_choice = (layers, self.old_key.currentData(), self.new_key.currentData())

    def _refresh_fields(self, *_args):
        old, new = self.old_combo.currentLayer(), self.new_combo.currentLayer()
        key_old, key_new = self.old_key.currentData(), self.new_key.currentData()
        lost = self._lost_key_text(old, new)
        if old is not None and new is not None and old.id() == new.id():
            self.key_hint.set_status(self.tr("Alegeți două layere diferite."), "warning")
        elif lost:
            self.key_hint.set_status(lost, "warning")
        elif key_old and key_new:
            if key_old == key_new:
                self.key_hint.set_status(self.tr("Înregistrările se potrivesc după '{0}'.")
                                         .format(key_old))
            else:
                self.key_hint.set_status(self.tr("Înregistrările se potrivesc după '{0}' "
                                                 "în layerul vechi și '{1}' în cel nou.")
                                         .format(key_old, key_new))
        else:
            self.key_hint.set_status("")
        self.eterra_hint.set_status(self.tr(cgxml_compare.eterra_advice(old, new)), "info")
        if not old or not new:
            self.field_list.set_names([])
            return
        new_names = set(data_field_names([new]))
        chosen = cgxml_compare.converted_columns(old, new, cgxml_compare.DEFAULT_COLUMNS)
        self.field_list.set_names(
            [name for name in data_field_names([old])
             if name in new_names and name not in (key_old, key_new)],
            True if chosen is None else chosen)

    # --- run ---

    def _outputs_ok(self):
        excel = self.excel_row.path()
        wants_layers = any(b.isChecked() for b in
                           (self.layer_added, self.layer_deleted, self.layer_changed))
        if not excel and not wants_layers:
            self.warn(self.tr("Alegeți un raport Excel sau cel puțin un layer rezultat."),
                      self.excel_row.edit)
            return None
        if excel and not self.confirm_overwrite([excel]):
            return None
        return excel

    def _run(self):
        if self.mode.index() == CGXML:
            self._run_cgxml()
        else:
            self._run_layers()

    def _run_layers(self):
        old, new = self.old_combo.currentLayer(), self.new_combo.currentLayer()
        if not self.validate_layer(old, self.tr("vechi"), focus=self.old_combo) or \
                not self.validate_layer(new, self.tr("nou"), focus=self.new_combo):
            return
        if old.id() == new.id():
            self.warn(self.tr("Alegeți două layere diferite."), self.new_combo)
            return
        key_old, key_new = self.old_key.currentData(), self.new_key.currentData()
        if not key_old or not key_new:
            self.warn(self.tr("Alegeți coloana cheie pentru ambele layere."),
                      self.old_key if not key_old else self.new_key)
            return
        fields = self.field_list.checked_texts()
        compare_geometry = (self.compare_geometry.isChecked()
                            and old.isSpatial() and new.isSpatial())
        if not fields and not compare_geometry:
            self.warn(self.tr("Bifați cel puțin o coloană de comparat sau "
                              "comparația geometriei."), self.field_list.list)
            return
        building_layers = []
        if self.buildings_box.isChecked():
            building_layers = self._building_layers()
            if not building_layers:
                return
        if not self.check_crs([old, new] + building_layers):
            return
        excel = self._outputs_ok()
        if excel is None:
            return
        self.save_remembered()
        buildings = None
        if building_layers:
            old_b, new_b = building_layers
            buildings = compare_service.CompareTask(
                old=LayerSnapshot(old_b), new=LayerSnapshot(new_b),
                key_old=self._building_key("old"), key_new=self._building_key("new"),
                fields=self.building_fields.checked_texts(),
                compare_geometry=(self.compare_geometry.isChecked()
                                  and old_b.isSpatial() and new_b.isSpatial()),
                tolerance=self.tolerance.value(), excel_path=None,
                fold_text=self.fold_text.isChecked())
        self.start_task(compare_service.CompareTask(
            old=LayerSnapshot(old), new=LayerSnapshot(new),
            key_old=key_old, key_new=key_new, fields=fields,
            compare_geometry=compare_geometry, tolerance=self.tolerance.value(),
            excel_path=excel, fold_text=self.fold_text.isChecked(), buildings=buildings,
            finished_cb=self._on_layers_finished),
            self.tr("Se compară layerele..."))

    def _building_layers(self):
        """The old and new construcții layers, or [] after a warning."""
        old, new = self.building_combos["old"], self.building_combos["new"]
        if not self.validate_layer(old.currentLayer(), self.tr("vechi de construcții"),
                                   focus=old) or \
                not self.validate_layer(new.currentLayer(), self.tr("nou de construcții"),
                                        focus=new):
            return []
        if old.currentLayer().id() == new.currentLayer().id():
            self.warn(self.tr("Alegeți două layere de construcții diferite."), new)
            return []
        old_key, new_key = self._building_key("old"), self._building_key("new")
        if old_key is None or new_key is None:
            self.warn(self.tr("Alegeți coloana cu numărul construcției pentru ambele "
                              "layere de construcții."),
                      self.building_keys["old" if old_key is None else "new"][1])
            return []
        if len(old_key) != len(new_key):
            self.warn(self.tr("Alegeți coloana imobilului în ambele layere de construcții "
                              "sau în niciunul."), self.building_keys["old"][0])
            return []
        return [old.currentLayer(), new.currentLayer()]

    def _source_ok(self, source, which):
        paths = source.paths()
        if not paths or not all(os.path.exists(p) for p in paths):
            self.warn(self.tr("Alegeți {0}: un folder, o arhivă ZIP sau fișiere .cgxml.")
                      .format(which), source.edit)
            return False
        source.refresh()
        count = source.count()
        if count is None:
            # part of the source cannot be read, and the status line under the field names it
            self.warn(self.tr("{0} nu poate fi folosită. {1}").format(
                which[:1].upper() + which[1:], source.status.text()), source.edit)
            return False
        if not count:
            self.warn(self.tr("Nu s-a găsit niciun fișier .cgxml în {0}.").format(which),
                      source.edit)
            return False
        return True

    def _run_cgxml(self):
        if not self._source_ok(self.old_source, self.tr("sursa veche")) or \
                not self._source_ok(self.new_source, self.tr("sursa nouă")):
            return
        columns = self.cgxml_fields.checked_data()
        if not columns and not self.compare_geometry.isChecked():
            self.warn(self.tr("Bifați cel puțin o coloană de comparat sau "
                              "comparația geometriei."), self.cgxml_fields.list)
            return
        building_columns = self.cgxml_building_fields.checked_data()
        excel = self._outputs_ok()
        if excel is None:
            return
        self.save_remembered()
        dialog_memory.save(type(self).__name__, "cgxml_columns", json.dumps(columns))
        dialog_memory.save(type(self).__name__, "cgxml_building_columns",
                           json.dumps(building_columns))
        self.start_task(cgxml_compare.CgxmlCompareTask(
            old_paths=self.old_source.paths(), new_paths=self.new_source.paths(),
            old_recursive=self.old_source.recursive.isChecked(),
            new_recursive=self.new_source.recursive.isChecked(),
            mode=self.match_combo.currentData(), columns=columns,
            compare_geometry=self.compare_geometry.isChecked(),
            tolerance=self.tolerance.value(), buildings=self.buildings.isChecked(),
            building_columns=building_columns,
            fold_text=self.fold_text.isChecked(), excel_path=excel or None,
            finished_cb=self._on_cgxml_finished),
            self.tr("Se compară fișierele..."))

    # --- result: layers ---

    def _finished_ok(self, ok, task):
        self.task_done()
        if task.isCanceled():
            self.notice(self.tr("Comparația a fost anulată."))
            return False
        if not ok:
            self.fail(self.tr("Comparația nu s-a putut finaliza:\n\n{0}").format(
                task.error_msg or self.tr("eroare necunoscută")))
            return False
        return True

    def _on_layers_finished(self, ok, task):
        if not self._finished_ok(ok, task):
            return
        b = task.buildings
        unwritten = []
        layers = self._result_layers(
            task, (self.tr("Apărute [{0}]"), self.tr("Dispărute [{0}]"),
                   self.tr("Modificate [{0}]")), unwritten)
        if b is not None:
            layers += self._result_layers(
                b, (self.tr("Construcții apărute [{0}]"), self.tr("Construcții dispărute [{0}]"),
                    self.tr("Construcții modificate [{0}]")), unwritten)
        _ui_helpers.add_layers(layers, self.tr("Comparator: {0} și {1}")
                               .format(task.old.name, task.new.name))

        records = (self.tr("înregistrare"), self.tr("înregistrări"))
        headline = self.tr("Layerul vechi are {0}, cel nou {1}.").format(
            fmt.count(task.old_read, *records), fmt.count(task.new_read, *records))
        counts = ([(self.tr("Apărute"), len(task.added), ADDED_COLOR),
                   (self.tr("Dispărute"), len(task.deleted), DELETED_COLOR),
                   (self.tr("Modificate"), len(task.changed_keys()), CHANGED_COLOR)]
                  + self._kind_counts(task.kind_rows())
                  + [(self.tr("Nemodificate"), task.unchanged_count(), None)])
        warnings = self._key_warnings(task, "")
        details = self._duplicate_details(task, self.tr("vechi"), self.tr("nou"))
        layer_names = [task.old, task.new]
        if b is not None:
            headline += " " + self.tr("Layerul vechi de construcții are {0}, cel nou {1}.").format(
                fmt.count(b.old_read, *records), fmt.count(b.new_read, *records))
            counts += ([None,
                        (self.tr("Construcții apărute"), len(b.added), ADDED_COLOR),
                        (self.tr("Construcții dispărute"), len(b.deleted), DELETED_COLOR),
                        (self.tr("Construcții modificate"), len(b.changed_keys()),
                         CHANGED_COLOR)]
                       + self._kind_counts(b.kind_rows())
                       + [(self.tr("Construcții nemodificate"), b.unchanged_count(), None)])
            warnings += self._key_warnings(b, self.tr("În layerele de construcții, "))
            details += self._duplicate_details(b, self.tr("construcții, vechi"),
                                               self.tr("construcții, nou"))
            layer_names += [b.old, b.new]
        report = self.report_notes(task)
        notes = (warnings + unwritten + report + stereo70.notes(layer_names)
                 + self._created_note(layers))
        self.show_result(headline, counts=counts, notes=notes,
                         file_path=None if report else task.excel_path or None,
                         details="\n".join(details) or None,
                         warning=bool(warnings or unwritten or report), layers=layers)

    def _result_layers(self, task, names, unwritten):
        """The layers of the added, removed and changed records of task that are wanted;
        names are their names, with {0} for the layer name. The features a layer refused are
        noted in unwritten."""
        added, deleted, changed = names
        layers = []
        for box, category, name, color in (
                (self.layer_added, ADDED, added.format(task.new.name), ADDED_COLOR),
                (self.layer_deleted, DELETED, deleted.format(task.old.name), DELETED_COLOR),
                (self.layer_changed, CHANGED, changed.format(task.new.name), CHANGED_COLOR)):
            if not box.isChecked():
                continue
            result = task.result_features(category)
            if result.features:
                layers.append(self._result_layer(result, name, color, unwritten))
        return layers

    def _result_layer(self, result, name, color, unwritten):
        """A Stereo 70 memory layer of a compare_service.ResultFeatures, styled by its kind
        field when it has one; the features it refused are noted in unwritten."""
        layer = QgsMemoryProviderUtils.createMemoryLayer(
            name, result.fields, result.wkb_type, stereo70.crs())
        refused, error = _add_features(layer, result.features)
        if refused:
            note = fmt.plural(
                refused, self.tr("{0} din {1} nu a putut fi scris în layerul '{2}'."),
                self.tr("{0} din {1} nu au putut fi scrise în layerul '{2}'.")).format(
                    fmt.count(refused, self.tr("element"), self.tr("elemente")),
                    fmt.number(len(result.features), 0), name)
            unwritten.append(f"{note} {error}" if error else note)
        if layer.isSpatial():
            _style_result(layer, color, result.kind_field)
        return layer

    @staticmethod
    def _kind_counts(rows):
        """The result rows of the kinds of change, each in the color of its legend entry."""
        return [(label, count, compare_service.CHANGE_COLORS[label.strip()])
                for label, count in rows]

    def _key_warnings(self, task, lead):
        """Rows without a key and repeated keys; lead starts each sentence."""
        warnings = []
        missing = task.old_missing + task.new_missing
        if missing:
            warnings.append(lead + fmt.plural(
                missing,
                self.tr("{0} fără valoare în coloana cheie a fost ignorat."),
                self.tr("{0} fără valoare în coloana cheie au fost ignorate."),
            ).format(fmt.count(missing, self.tr("rând"), self.tr("rânduri"))))
        dups = len(task.old_dups) + len(task.new_dups)
        if dups:
            warnings.append(lead + fmt.plural(
                dups,
                self.tr("{0} apare de mai multe ori; s-a comparat doar primul ei rând."),
                self.tr("{0} apar de mai multe ori; s-a comparat doar primul rând al "
                        "fiecăreia."),
            ).format(fmt.count(dups, self.tr("cheie"), self.tr("chei"))))
        return warnings

    def _duplicate_details(self, task, old_side, new_side):
        rows = (self.tr("rând"), self.tr("rânduri"))
        return ([f"{old_side}: {k} ({fmt.count(n, *rows)})"
                 for k, n in sorted(task.old_dups.items())]
                + [f"{new_side}: {k} ({fmt.count(n, *rows)})"
                   for k, n in sorted(task.new_dups.items())])

    def _created_note(self, layers):
        if not layers:
            return []
        return [fmt.plural(
            len(layers),
            self.tr("Layer creat: {0}. Este temporar; salvați-l dacă doriți să-l păstrați."),
            self.tr("Layere create: {0}. Sunt temporare; salvați-le dacă doriți să le "
                    "păstrați."),
        ).format(", ".join(layer.name() for layer in layers))]

    # --- result: CGXML ---

    def _on_cgxml_finished(self, ok, task):
        if not self._finished_ok(ok, task):
            return
        wanted = [(self.layer_added, task.added, "imobile", cgxml_compare.IMOBILE_ADDED,
                   ADDED_COLOR, False),
                  (self.layer_deleted, task.deleted, "imobile", cgxml_compare.IMOBILE_DELETED,
                   DELETED_COLOR, False),
                  (self.layer_changed, task.changed, "imobile", cgxml_compare.IMOBILE_CHANGED,
                   CHANGED_COLOR, True)]
        if task.buildings:
            wanted += [(self.layer_added, task.buildings_added, "constructii",
                        cgxml_compare.BUILDINGS_ADDED, ADDED_COLOR, False),
                       (self.layer_deleted, task.buildings_deleted, "constructii",
                        cgxml_compare.BUILDINGS_DELETED, DELETED_COLOR, False),
                       (self.layer_changed, task.buildings_changed, "constructii",
                        cgxml_compare.BUILDINGS_CHANGED, CHANGED_COLOR, True)]
        layers, left_out, unwritten = [], [], []
        for box, outcomes, which, label, color, with_changes in wanted:
            if not box.isChecked() or not outcomes:
                continue
            name = self.tr(label)
            result = cgxml_compare.result_features(outcomes, which, _columns_layout(which),
                                                   with_changes, task.compare_geometry)
            left_out.append((name, result.left_out))
            if result.features:
                layers.append(self._result_layer(result, name, color, unwritten))
        _ui_helpers.add_layers(layers, self.tr("Comparator CGXML"))

        counts = [(self.tr(label), value, CGXML_COLORS.get(label.strip()))
                  for label, value in task.summary_rows()]
        building_rows = task.building_summary_rows()
        if building_rows:
            counts += [None] + [(self.tr(label), value, CGXML_COLORS.get(label.strip()))
                                for label, value in building_rows]
        if not task.matched:
            headline = self.tr("Niciun imobil nu a fost găsit în ambele surse.")
        elif task.changed:
            headline = fmt.plural(
                len(task.changed), self.tr("{0} găsit în ambele surse s-a modificat."),
                self.tr("{0} găsite în ambele surse s-au modificat.")).format(
                    fmt.count(len(task.changed), self.tr("imobil"), self.tr("imobile")))
        else:
            headline = self.tr("Niciun imobil găsit în ambele surse nu s-a modificat.")
        report = self.report_notes(task)
        notes = task.notes()
        if task.renamed:
            notes.append(fmt.plural(
                task.renamed,
                self.tr("{0} are alt nume de fișier în sursa nouă și a fost găsit după IE "
                        "sau după numărul cadastral."),
                self.tr("{0} au alt nume de fișier în sursa nouă și au fost găsite după IE "
                        "sau după numărul cadastral.")).format(
                fmt.count(task.renamed, self.tr("imobil"), self.tr("imobile"))))
        if task.ignored:
            notes.append(fmt.plural(
                len(task.ignored), self.tr("{0} a fost ignorat; motivul este în detalii."),
                self.tr("{0} au fost ignorate; motivele sunt în detalii.")).format(
                    fmt.count(len(task.ignored), self.tr("fișier"), self.tr("fișiere"))))
        if task.problems:
            notes.append(fmt.plural(
                len(task.problems),
                self.tr("{0} despre fișierele comparate este în detalii."),
                self.tr("{0} despre fișierele comparate sunt în detalii.")).format(
                    fmt.count(len(task.problems), self.tr("observație"),
                              self.tr("observații"))))
        left_out_note = cgxml_compare.left_out_note(
            left_out, in_report=bool(task.excel_path) and not report)
        if left_out_note:
            notes.append(left_out_note)
        notes += unwritten + report + self._created_note(layers)
        details = "\n".join([f"{i.side}, {i.file_name}: {i.reason}" for i in task.ignored]
                            + [f"{p.side}, {p.file_name}: {p.text}" for p in task.problems])
        warning = bool(task.ignored or task.problems or unwritten or report or not task.matched
                       or siruta.problem())
        self.show_result(headline, counts=counts, notes=notes,
                         file_path=None if report else task.excel_path,
                         details=details or None, warning=warning, layers=layers)
