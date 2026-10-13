# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verificare Topologică window: project layers or CGXML files."""

import os

from qgis.PyQt.QtWidgets import (  # type: ignore
    QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)
from qgis.core import Qgis, QgsMapLayerProxyModel, QgsMemoryProviderUtils  # type: ignore
from qgis.gui import QgsCollapsibleGroupBox, QgsMapLayerComboBox  # type: ignore

from . import _ui_helpers
from .base_dialog import BaseDialog
from .widgets import (
    CgxmlSource, LayerChecklist, PageStack, PathRow, SegmentedControl, converted_column,
    data_field_names, fill_field_combo, hint_label, make_form, preselect_layer,
)
from .. import features
from ..services import cgxml_sources, fmt, layer_style, stereo70
from ..services import topology_check_service as tcs
from ..services.layer_sources import LayerSnapshot

LAYERS, CGXML = 0, 1


class TopologyCheckDialog(BaseDialog):
    FEATURE = "check_topology"

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setMinimumWidth(680)
        # set once the user picks the building link; from then on the link of converted
        # layers is not preselected
        self._link_chosen = False
        self._setup_ui()

    # --- ui ---

    def _setup_ui(self):
        choice = QHBoxLayout()
        choice.addWidget(QLabel(self.tr("Se verifică:")))
        self.source_mode = SegmentedControl(
            [self.tr("Layerele din Proiect"), self.tr("Fișiere CGXML")],
            [self.tr("Layerele de imobile și de construcții deschise în proiect"),
             self.tr("Un folder, o arhivă ZIP sau fișiere .cgxml alese")])
        choice.addWidget(self.source_mode)
        choice.addStretch()
        self.body.addLayout(choice)

        self.source_stack = PageStack()
        self.layers_box = self._layers_page()
        self.cgxml_box = self._cgxml_page()
        self.source_stack.addWidget(self.layers_box)
        self.source_stack.addWidget(self.cgxml_box)
        self.body.addWidget(self.source_stack)

        self.tolerances = QgsCollapsibleGroupBox(self.tr("Toleranțe"))
        self.tolerances.setObjectName("tolerances")
        self.tolerances.setCollapsed(True)
        self.tolerances.collapsedStateChanged.connect(lambda *_: self.fit_height())
        tol_layout = QVBoxLayout(self.tolerances)
        tol_form = make_form()
        self.min_area = self._spin(0.0, 100.0, 3, 0.01, tcs.MIN_AREA, " mp", self.tr(
            "Suprapunerile și golurile mai mici de atât provin din rotunjirea coordonatelor "
            "și nu se raportează, la fel ca cele mai înguste decât toleranța de racordare."))
        tol_form.addRow(self.tr("Suprafață minimă:"), self.min_area)
        self.snap_precision = self._spin(0.0, 0.1, 3, 0.001, tcs.SNAP_PRECISION, " m", self.tr(
            "Un punct aflat mai aproape de atât de limita vecinului se consideră pe "
            "limită, iar o suprapunere sau un gol mai îngust de atât nu se raportează. "
            "Coordonatele rotunjite la milimetru pot depărta un punct de limita vecinului "
            "cu până la 1,4 mm."))
        tol_form.addRow(self.tr("Toleranță de racordare:"), self.snap_precision)
        self.snap_distance = self._spin(0.01, 5.0, 3, 0.05, tcs.SNAP_DISTANCE, " m", self.tr(
            "Un punct aflat mai aproape de atât de limita unui imobil vecin, cu teren "
            "neacoperit între ele sau în interiorul vecinului, este raportat ca "
            "neracordat."))
        tol_form.addRow(self.tr("Distanță de racordare:"), self.snap_distance)
        self.building_tolerance = self._spin(
            0.0, 1.0, 3, 0.005, tcs.BUILDING_TOLERANCE, " m", self.tr(
                "Cât poate ieși un punct al construcției din imobilul ei fără să fie "
                "raportat."))
        tol_form.addRow(self.tr("Ieșire construcții:"), self.building_tolerance)
        self.roads_sector = QLineEdit(tcs.ROADS)
        self.roads_sector.setMaximumWidth(self.building_tolerance.sizeHint().width())
        self.roads_sector.setToolTip(self.tr(
            "Sectorul cadastral al drumurilor dintre sectoare. O suprapunere cu un imobil din "
            "el este avertisment, iar în el nu se caută nici goluri, nici puncte "
            "neracordate. Lăsați câmpul necompletat dacă nu există un astfel de sector."))
        self.roads_sector.textChanged.connect(lambda *_: self._show_tolerance_state())
        tol_form.addRow(self.tr("Sectorul drumurilor:"), self.roads_sector)
        tol_layout.addLayout(tol_form)
        reset_line = QHBoxLayout()
        reset_line.addWidget(hint_label(self.tr(
            "Cu valorile implicite nu se raportează rotunjirea coordonatelor.")), 1)
        reset = _ui_helpers.small_button(self.tr("Implicite"),
                                         self.tr("Revine la valorile implicite"))
        reset.clicked.connect(self._reset_tolerances)
        reset_line.addWidget(reset)
        tol_layout.addLayout(reset_line)
        for spin, _default in self._tolerance_spins():
            spin.valueChanged.connect(lambda *_: self._show_tolerance_state())
        self.body.addWidget(self.tolerances)

        self.limits_group = QgsCollapsibleGroupBox(self.tr("Limitele Sectoarelor"))
        self.limits_group.setObjectName("limits")
        self.limits_group.setCheckable(True)
        self.limits_group.setChecked(False)
        # collapsed follows the check box, so the saved collapsed state must not apply
        self.limits_group.setSaveCollapsedState(False)
        self.limits_group.toggled.connect(lambda on: self.limits_group.setCollapsed(not on))
        self.limits_group.collapsedStateChanged.connect(lambda *_: self.fit_height())
        limits_layout = QVBoxLayout(self.limits_group)
        limits_layout.addWidget(hint_label(self.tr(
            "Cu limitele sectoarelor se găsesc și terenul neacoperit de la marginea sectorului "
            "și imobilele care ies din el. Fără ele se verifică golurile închise "
            "între imobile.")))
        limits_form = make_form()
        self.limit_combo = QgsMapLayerComboBox()
        self.limit_combo.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        self.limit_combo.setAllowEmptyLayer(True)
        # no fallback to the active layer, usually the imobile
        self.limit_combo.setLayer(None)
        preselect_layer(self.limit_combo, "roles/sectoare")
        self.limit_combo.layerChanged.connect(self._refresh_limit_fields)
        limits_form.addRow(self.tr("Layer cu limitele:"), self.limit_combo)
        self.limit_field = QComboBox()
        self.limit_field.setToolTip(self.tr(
            "Coloana cu numărul sectorului. Fără ea, fiecare limită ia sectorul "
            "majorității imobilelor din interiorul ei."))
        limits_form.addRow(self.tr("Coloana sectorului:"), self.limit_field)
        limits_layout.addLayout(limits_form)
        self.body.addWidget(self.limits_group)

        output = QGroupBox(self.tr("Rezultat"))
        out_form = make_form(output)
        self.excel_row = PathRow("save", self.tr("Salvați raportul Excel"),
                                 self.tr("Excel (*.xlsx)"), ".xlsx")
        self.excel_row.edit.setPlaceholderText(self.tr("Opțional"))
        out_form.addRow(self.tr("Raport Excel:"), self.excel_row)
        out_form.addRow(hint_label(self.tr(
            "Problemele găsite se adaugă în proiect ca layere temporare, colorate după "
            "tip. Datele verificate nu se modifică.")))
        self.body.addWidget(output)
        self.body.addStretch()

        self.set_tip(self.tr("Datele din fișierele CGXML se verifică după regulile ANCPI, "
                             "gratuit, pe"), features.WEBSITE_VALIDATOR, "cadex.ro/validator")
        self.run_btn = self.add_run_buttons(self.tr("Verifică"), self._run)

        self.source_mode.changed.connect(self._on_source_changed)
        self.remember("source_mode", self.source_mode)
        self.remember("cgxml", self.cgxml.edit)
        self.remember("recursive", self.cgxml.recursive)
        self.remember("min_area", self.min_area)
        self.remember("snap_precision", self.snap_precision)
        self.remember("snap_distance", self.snap_distance)
        self.remember("building_tolerance", self.building_tolerance)
        self.remember("roads_sector", self.roads_sector)
        self.remember("limits", self.limits_group)
        self.remember("excel", self.excel_row.edit)
        self._refresh_imobile_fields()
        self._refresh_link_fields()
        self._refresh_limit_fields()
        self.remember("id_field", self.id_combo, by_text=True)
        self.remember("sector_field", self.sector_combo, by_text=True)
        self.remember("link_field", self.link_combo, by_text=True)
        self.restore_remembered()
        # a remembered "(după suprapunere)" may be the untouched default of an earlier run
        self._refresh_link_fields()
        self.link_combo.activated.connect(self._on_link_chosen)
        self.limits_group.setCollapsed(not self.limits_group.isChecked())
        self._show_tolerance_state()
        self.cgxml.refresh()
        self._on_source_changed()

    def _layers_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        # side by side: stacked, the lists make the window too tall for a laptop screen
        lists = QHBoxLayout()
        imobile_group = QGroupBox(self.tr("Layere de Imobile"))
        imobile_layout = QVBoxLayout(imobile_group)
        self.imobile = LayerChecklist("roles/imobile")
        self.imobile.changed.connect(self._refresh_imobile_fields)
        imobile_layout.addWidget(self.imobile)
        lists.addWidget(imobile_group)
        constr_group = QGroupBox(self.tr("Layere de Construcții"))
        constr_layout = QVBoxLayout(constr_group)
        self.constructii = LayerChecklist("roles/constructii")
        self.constructii.changed.connect(self._refresh_link_fields)
        constr_layout.addWidget(self.constructii)
        lists.addWidget(constr_group)
        layout.addLayout(lists)

        self.fields_group = QGroupBox(self.tr("Câmpuri"))
        fields_form = make_form(self.fields_group)
        self.id_combo = QComboBox()
        self.id_combo.setToolTip(self.tr(
            "Câmpul după care se numesc imobilele în rezultat și la care trimite "
            "legătura construcțiilor."))
        fields_form.addRow(self.tr("ID imobil:"), self.id_combo)
        self.sector_combo = QComboBox()
        self.sector_combo.setToolTip(self.tr(
            "Golurile se caută în fiecare sector, iar limitele sectoarelor se potrivesc după "
            "această coloană. Sectorul drumurilor, ales la Toleranțe, nu se verifică la goluri "
            "și racordare."))
        fields_form.addRow(self.tr("Sector cadastral:"), self.sector_combo)
        self.link_combo = QComboBox()
        self.link_combo.setToolTip(self.tr(
            "Coloana din construcții cu ID-ul imobilului căruia îi aparține "
            "construcția, de exemplu 'id' dintr-un layer creat cu Conversie CGXML, unde "
            "se preselectează. Fără această coloană, construcția aparține imobilului pe care "
            "stă în cea mai mare parte."))
        fields_form.addRow(self.tr("Imobilul construcției:"), self.link_combo)
        layout.addWidget(self.fields_group)
        return page

    def _cgxml_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        group = QGroupBox(self.tr("Fișiere CGXML"))
        group_layout = QVBoxLayout(group)
        self.cgxml = CgxmlSource()
        group_layout.addWidget(self.cgxml)
        group_layout.addWidget(hint_label(self.tr(
            "Din fiecare fișier se citesc imobilul, sectorul lui cadastral și "
            "construcțiile lui, deci nu trebuie ales niciun câmp.")))
        layout.addWidget(group)
        return page

    def _spin(self, low, high, decimals, step, value, suffix, tooltip):
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setValue(value)
        spin.setSuffix(suffix)
        spin.setToolTip(tooltip)
        return spin

    def _tolerance_spins(self):
        return ((self.min_area, tcs.MIN_AREA), (self.snap_precision, tcs.SNAP_PRECISION),
                (self.snap_distance, tcs.SNAP_DISTANCE),
                (self.building_tolerance, tcs.BUILDING_TOLERANCE))

    def _reset_tolerances(self):
        for spin, default in self._tolerance_spins():
            spin.setValue(default)
        self.roads_sector.setText(tcs.ROADS)

    def _show_tolerance_state(self):
        # the section starts collapsed, so the title shows whether the defaults are in use
        changed = (any(abs(spin.value() - default) > 1e-9
                       for spin, default in self._tolerance_spins())
                   or self.roads_sector.text().strip() != tcs.ROADS)
        self.tolerances.setTitle(self.tr("Toleranțe (modificate)") if changed
                                 else self.tr("Toleranțe (valorile implicite)"))

    def _on_source_changed(self, *_args):
        self.source_stack.setCurrentWidget(
            self.cgxml_box if self.source_mode.index() == CGXML else self.layers_box)
        self.fit_height()

    def _refresh_imobile_fields(self):
        names = data_field_names(self.imobile.checked_layers())
        fill_field_combo(self.id_combo, names, "fields/id",
                         self.tr("(numărul elementului)"), self.id_combo.currentData())
        fill_field_combo(self.sector_combo, names, "fields/sector",
                         self.tr("(un singur sector)"), self.sector_combo.currentData())

    def _refresh_link_fields(self):
        # not preselected by name: an 'id' column in a construcții layer is often the
        # construcție's own number; a converted layer records in cadex/columns which column
        # holds the imobil
        layers = self.constructii.checked_layers()
        keep = self.link_combo.currentData()
        if not keep and not self._link_chosen:
            keep = converted_column(layers, "id")
        fill_field_combo(self.link_combo, data_field_names(layers), None,
                         self.tr("(după suprapunere)"), keep)

    def _on_link_chosen(self, *_args):
        self._link_chosen = True

    def _refresh_limit_fields(self, *_args):
        layer = self.limit_combo.currentLayer()
        names = data_field_names([layer]) if layer is not None else []
        fill_field_combo(self.limit_field, names, "fields/sector",
                         self.tr("(după imobilele din interior)"),
                         self.limit_field.currentData())

    # --- run ---

    def _limits_layer(self):
        """The sector outline layer, None when the section is off, False (after a warning)
        when it is on without a usable layer."""
        if not self.limits_group.isChecked():
            return None
        layer = self.limit_combo.currentLayer()
        if layer is None:
            self.warn(self.tr("Alegeți layerul cu limitele sectoarelor sau debifați "
                              "'Limitele Sectoarelor'."), self.limit_combo)
            return False
        if not self.validate_layer(layer, layer.name(), focus=self.limit_combo):
            return False
        return layer

    def _run(self):
        limits = self._limits_layer()
        if limits is False:
            return
        if self.snap_precision.value() >= self.snap_distance.value():
            self.warn(self.tr("Toleranța de racordare trebuie să fie mai mică decât "
                              "distanța de racordare."), self.snap_precision)
            return
        excel = self.excel_row.path()
        # the task adds the tolerances to the report's facts
        common = dict(
            min_area=self.min_area.value(), snap_precision=self.snap_precision.value(),
            snap_distance=self.snap_distance.value(),
            building_tolerance=self.building_tolerance.value(),
            roads_sector=self.roads_sector.text().strip() or None,
            excel_path=excel or None, limit_field=self.limit_field.currentData() or None,
            finished_cb=self._on_finished)

        if self.source_mode.index() == CGXML:
            task = self._cgxml_task(limits, common)
        else:
            task = self._layers_task(limits, common)
        if task is None or not self.confirm_overwrite([excel]):
            return
        self.save_remembered()
        self.start_task(task, self.tr("Se verifică topologia..."))

    def _cgxml_task(self, limits, common):
        paths = self.cgxml.paths()
        if not paths or not all(os.path.exists(p) for p in paths):
            self.warn(self.tr("Alegeți folderul, arhiva ZIP sau fișierele CGXML."),
                      self.cgxml.edit)
            return None
        try:
            sources = cgxml_sources.list_sources(paths, self.cgxml.recursive.isChecked())
        except Exception as e:
            self.warn(self.tr("Sursa nu poate fi citită: {0}").format(e), self.cgxml.edit)
            return None
        if not sources:
            self.warn(self.tr("Nu s-a găsit niciun fișier .cgxml în sursa aleasă."),
                      self.cgxml.edit)
            return None
        if all(isinstance(s, cgxml_sources.Unreadable) for s in sources):
            self.warn(self.tr("Sursa aleasă nu poate fi citită: {0}.").format(
                cgxml_sources.unreadable_text(sources)), self.cgxml.edit)
            return None
        if limits and not self.check_crs([limits]):
            return None
        return tcs.TopologyCheckTask(
            cgxml_paths=paths, recursive=self.cgxml.recursive.isChecked(),
            limits=LayerSnapshot(limits) if limits else None,
            facts=[(self.tr("Sursă"), "; ".join(paths))], **common)

    def _layers_task(self, limits, common):
        imobile = self.imobile.checked_layers()
        constructii = self.constructii.checked_layers()
        if not imobile:
            self.warn(self.tr("Bifați cel puțin un layer de imobile."), self.imobile.list)
            return None
        if {lyr.id() for lyr in imobile} & {lyr.id() for lyr in constructii}:
            self.warn(self.tr("Același layer este bifat și la imobile, și la "
                              "construcții. Debifați-l dintr-una dintre liste."))
            return None
        for layer in imobile + constructii:
            if not self.validate_layer(layer, layer.name()):
                return None
        if not self.check_crs(imobile + constructii + ([limits] if limits else [])):
            return None
        id_field = self.id_combo.currentData() or None
        link_field = self.link_combo.currentData() or None
        if constructii and link_field and not id_field:
            # without it the link values match no imobil and every construcție gets an
            # 'Imobil negăsit' finding
            self.warn(self.tr("Alegeți și coloana din imobile la care trimite "
                              "'Imobilul construcției'."), self.id_combo)
            return None
        if id_field and not self.confirm_distinct(imobile, id_field):
            return None
        facts = [(self.tr("Layere de imobile"), ", ".join(lyr.name() for lyr in imobile)),
                 (self.tr("Layere de construcții"),
                  ", ".join(lyr.name() for lyr in constructii) or "-")]
        return tcs.TopologyCheckTask(
            imobile=[LayerSnapshot(lyr) for lyr in imobile],
            constructii=[LayerSnapshot(lyr) for lyr in constructii],
            id_field=id_field,
            sector_field=self.sector_combo.currentData() or None,
            link_field=link_field,
            number_field=converted_column(constructii, "nr_constr"),
            limits=LayerSnapshot(limits) if limits else None,
            facts=facts, **common)

    # --- result ---

    def _on_finished(self, ok, task):
        self.task_done()
        if task.isCanceled():
            self.notice(self.tr("Verificarea a fost anulată."))
            return
        if not ok:
            self.fail(self.tr("Verificarea nu s-a putut finaliza:\n\n{0}").format(
                task.error_msg or self.tr("eroare necunoscută")))
            return
        unwritten = []
        layers = self.result_layers(task, unwritten)
        headline, counts, notes = self.summary(task, layers, unwritten)
        details = self._details(task)
        if details:
            notes.append(self.tr("Lista completă a problemelor este în detalii."))
        # a run that checked nothing or left something out is not shown as clean
        warning = bool(any(f.severity == tcs.ERROR for f in task.findings)
                       or not task.parcel_count or task.read_errors or task.notes()
                       or task.report_error or unwritten)
        written = task.excel_path if task.excel_path and not task.report_error else None
        self.show_result(headline, counts=counts, notes=notes, file_path=written,
                         details=details, layers=layers, warning=warning)

    def result_layers(self, task, unwritten=None):
        """Add the findings to the project as an area and a point layer, styled by type.
        unwritten, a list, receives a note for each layer that could not hold every
        finding."""
        fields = tcs.findings_fields()
        layers = []
        for findings, title, wkb in (
                ([f for f in task.findings if f.is_area],
                 self.tr("Probleme topologice (suprafețe)"), Qgis.WkbType.MultiPolygon),
                ([f for f in task.findings if not f.is_area],
                 self.tr("Probleme topologice (puncte)"), Qgis.WkbType.Point)):
            if not findings:
                continue
            layer = QgsMemoryProviderUtils.createMemoryLayer(title, fields, wkb, task.crs)
            features = tcs.findings_features(findings, fields, wkb)
            missing, error = _ui_helpers.add_features(layer, features)
            if missing and unwritten is not None:
                unwritten.append((fmt.plural(
                    missing, self.tr("{0} din {1} nu a putut fi scrisă în layerul '{2}'."),
                    self.tr("{0} din {1} nu au putut fi scrise în layerul '{2}'.")).format(
                        fmt.count(missing, self.tr("problemă"), self.tr("probleme")),
                        fmt.number(len(features), 0), title) + f" {error}").strip())
            layer_style.style_categories(
                layer, "tip", [(kind, kind, tcs.KIND_COLORS[kind]) for kind in tcs.KINDS])
            layers.append(layer)
        return _ui_helpers.add_layers(layers, self.tr("Verificare Topologică"))

    def summary(self, task, layers, unwritten=()):
        """(headline, counts, notes) for the result window. unwritten holds the notes of
        result_layers on findings a layer could not hold."""
        headline = fmt.plural(
            task.parcel_count, self.tr("S-a verificat {0}, cu {1}."),
            self.tr("S-au verificat {0}, cu {1}.")).format(
                fmt.count(task.parcel_count, self.tr("imobil"), self.tr("imobile")),
                fmt.count(task.building_count, self.tr("construcție"), self.tr("construcții")))
        by_kind = tcs.counts_by_kind(task.findings)
        counts = [(kind, by_kind[kind], tcs.KIND_COLORS[kind])
                  for kind in tcs.KINDS if by_kind[kind]]
        notes = []
        if not task.parcel_count:
            notes.append(self.tr("Niciun imobil nu a putut fi verificat."))
        elif not task.findings:
            notes.append(self.tr("Nu s-a găsit nicio problemă."))
        linked = task.building_count
        if linked and by_kind[tcs.LINK_MISSING] > linked / 2:
            notes.append(self.tr(
                "La majoritatea construcțiilor imobilul indicat nu a fost găsit. "
                "Verificați dacă 'Imobilul construcției' și 'ID imobil' conțin aceleași "
                "valori."))
        if task.read_errors:
            notes.append(fmt.plural(
                len(task.read_errors), self.tr("{0} nu a putut fi citit."),
                self.tr("{0} nu au putut fi citite.")).format(
                    fmt.count(len(task.read_errors), self.tr("fișier"), self.tr("fișiere"))))
        notes += task.notes()
        notes += self.report_notes(task)
        notes += list(unwritten)
        notes += stereo70.notes(task.snapshots())
        if layers:
            notes.append(fmt.plural(
                len(layers),
                self.tr("Layer creat: {0}. Este temporar; salvați-l dacă doriți să-l păstrați."),
                self.tr("Layere create: {0}. Sunt temporare; salvați-le dacă doriți să le "
                        "păstrați."),
            ).format(", ".join(layer.name() for layer in layers)))
        return headline, counts, notes

    def _details(self, task):
        lines = [f"{name}: {message}" for name, message in task.read_errors]
        lines += [self.tr("Imobilul {0} apare în: {1}. S-a verificat doar primul fișier.")
                  .format(imobil, ", ".join(files)) for imobil, files in task.duplicates]
        lines += [self.tr("Imobilul {0} apare în locuri diferite, în fișierele: {1}. Fiecare "
                          "fișier s-a verificat ca imobil separat.")
                  .format(imobil, ", ".join(files)) for imobil, files in task.reused_keys]
        return "\n".join(lines) or None
