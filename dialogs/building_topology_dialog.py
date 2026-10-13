# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Topologie Construcții window."""

from qgis.PyQt.QtWidgets import (  # type: ignore
    QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QVBoxLayout,
)
from qgis.core import QgsMemoryProviderUtils  # type: ignore
from qgis.gui import QgsCollapsibleGroupBox  # type: ignore

from . import _ui_helpers
from .base_dialog import BaseDialog
from .widgets import (
    CheckList, LayerChecklist, converted_column, data_field_names,
    fill_field_combo, hint_label, make_form,
)
from ..services import building_topology_service as bts
from ..services import fmt, layer_style, stereo70
from ..services.layer_sources import LayerSnapshot, merged_fields


class BuildingTopologyDialog(BaseDialog):
    FEATURE = "building_topology"

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setMinimumWidth(640)
        # set once the user picks the building link; from then on the link of converted
        # layers is not preselected
        self._link_chosen = False
        self._setup_ui()

    def _setup_ui(self):
        # side by side: stacked, the lists make the window too tall for a laptop screen
        lists = QHBoxLayout()
        imobile_group = QGroupBox(self.tr("Layere de Imobile"))
        imobile_layout = QVBoxLayout(imobile_group)
        self.imobile = LayerChecklist("roles/imobile")
        self.imobile.changed.connect(self._refresh_join)
        imobile_layout.addWidget(self.imobile)
        lists.addWidget(imobile_group)

        constr_group = QGroupBox(self.tr("Layere de Construcții"))
        constr_layout = QVBoxLayout(constr_group)
        self.constructii = LayerChecklist("roles/constructii")
        self.constructii.changed.connect(self._refresh_link)
        constr_layout.addWidget(self.constructii)
        lists.addWidget(constr_group)
        self.body.addLayout(lists)

        link = QGroupBox(self.tr("Legătura cu Imobilul"))
        link_form = make_form(link)
        self.link_combo = QComboBox()
        self.link_combo.setToolTip(self.tr(
            "Coloana din construcții cu ID-ul imobilului căruia îi aparține "
            "construcția, de exemplu 'id' dintr-un layer creat cu Conversie CGXML, unde "
            "se preselectează. Fără această coloană, construcția aparține imobilului pe care "
            "stă în cea mai mare parte. Cu ea, o construcție care stă în cea mai mare parte "
            "pe alt imobil este marcată 'Pe alt imobil' și nu se taie."))
        self.link_combo.currentIndexChanged.connect(self._on_link_changed)
        link_form.addRow(self.tr("Imobilul construcției:"), self.link_combo)
        self.key_combo = QComboBox()
        self.key_combo.setToolTip(self.tr(
            "Coloana din imobile cu aceleași valori ca 'Imobilul construcției'."))
        link_form.addRow(self.tr("ID imobil:"), self.key_combo)
        self.body.addWidget(link)

        params = QGroupBox(self.tr("Tăiere"))
        form = make_form(params)
        self.buffer = QDoubleSpinBox()
        self.buffer.setRange(0.0, 1.0)
        self.buffer.setDecimals(4)
        self.buffer.setSingleStep(0.001)
        self.buffer.setValue(0.002)
        self.buffer.setSuffix(" m")
        self.buffer.setToolTip(self.tr(
            "Partea unei construcții care iese din imobil se taie după limita lui mutată spre "
            "interior cu această distanță, astfel încât niciun punct rămas din ea să nu fie pe "
            "limită sau în afara ei. 0,002 m înseamnă 2 mm."))
        form.addRow(self.tr("Retragere față de limită:"), self.buffer)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.0, 10000.0)
        self.threshold.setDecimals(2)
        self.threshold.setValue(1.0)
        self.threshold.setSuffix(" mp")
        self.threshold.setToolTip(self.tr(
            "O construcție care pierde mai mult de atât este marcată "
            "'Pierdere peste prag'."))
        form.addRow(self.tr("Prag de verificare:"), self.threshold)
        self.body.addWidget(params)

        self.join_group = QgsCollapsibleGroupBox(self.tr("Preluare Coloane din Imobil"))
        self.join_group.setObjectName("join")
        self.join_group.setCheckable(True)
        self.join_group.setChecked(False)
        # collapsed follows the check box, so the saved collapsed state must not apply
        self.join_group.setSaveCollapsedState(False)
        self.join_group.toggled.connect(lambda on: self.join_group.setCollapsed(not on))
        self.join_group.collapsedStateChanged.connect(lambda *_: self.fit_height())
        join_layout = QVBoxLayout(self.join_group)
        join_layout.addWidget(hint_label(self.tr(
            "Coloanele bifate se copiază pe fiecare construcție, de la imobilul pe "
            "care se află, cu prefixul 'imb_'.")))
        self.join_list = CheckList(self.tr("Bifați un layer de imobile."))
        join_layout.addWidget(self.join_list)
        self.body.addWidget(self.join_group)

        output = QGroupBox(self.tr("Rezultat"))
        output_layout = QVBoxLayout(output)
        output_layout.addWidget(hint_label(self.tr(
            "Pentru fiecare layer de construcții se creează un layer temporar nou, "
            "'<nume>_corectat', cu coloanele 'stare', 'sup_inainte', 'sup_dupa', "
            "'sup_pierduta' și 'obs_topo'; un nume pe care layerul îl are deja primește un "
            "sufix, de exemplu 'stare_2'. Layerele originale nu se modifică.")))
        self.body.addWidget(output)
        self.body.addStretch()

        self.run_btn = self.add_run_buttons(self.tr("Corectează"), self._run)

        self.remember("buffer", self.buffer)
        self.remember("threshold", self.threshold)
        self.remember("join", self.join_group)
        self._refresh_join()
        self._refresh_link()
        self.remember("link_field", self.link_combo, by_text=True)
        self.remember("key_field", self.key_combo, by_text=True)
        self.restore_remembered()
        # a remembered "(după suprapunere)" may be the untouched default of an earlier run
        self._refresh_link()
        self.link_combo.activated.connect(self._on_link_chosen)
        self.join_group.setCollapsed(not self.join_group.isChecked())
        self._on_link_changed()

    def _refresh_link(self):
        # not preselected by name: an 'id' column in a construcții layer is often the
        # construcție's own number; a converted layer records in cadex/columns which column
        # holds the imobil
        layers = self.constructii.checked_layers()
        keep = self.link_combo.currentData()
        if not keep and not self._link_chosen:
            keep = converted_column(layers, "id")
        fill_field_combo(self.link_combo, data_field_names(layers), None,
                         self.tr("(după suprapunere)"), keep)
        self._on_link_changed()

    def _on_link_chosen(self, *_args):
        self._link_chosen = True

    def _refresh_key(self):
        fill_field_combo(self.key_combo, data_field_names(self.imobile.checked_layers()),
                         "fields/id", self.tr("(niciunul)"), self.key_combo.currentData())

    def _on_link_changed(self, *_args):
        self.key_combo.setEnabled(bool(self.link_combo.currentData()))

    def _refresh_join(self):
        self._refresh_key()
        self.join_list.set_names(data_field_names(self.imobile.checked_layers()),
                                 default_on=False)

    def _run(self):
        imobile = self.imobile.checked_layers()
        constructii = self.constructii.checked_layers()
        if not imobile:
            self.warn(self.tr("Bifați cel puțin un layer de imobile."), self.imobile.list)
            return
        if not constructii:
            self.warn(self.tr("Bifați cel puțin un layer de construcții."),
                      self.constructii.list)
            return
        if {lyr.id() for lyr in imobile} & {lyr.id() for lyr in constructii}:
            self.warn(self.tr("Același layer este bifat și la imobile, și la "
                              "construcții. Debifați-l dintr-una dintre liste."))
            return
        for layer in imobile + constructii:
            if not self.validate_layer(layer, layer.name()):
                return
        if not self.check_crs(imobile + constructii):
            return
        link_field = self.link_combo.currentData() or None
        key_field = self.key_combo.currentData() or None
        if link_field and not key_field:
            self.warn(self.tr("Alegeți și coloana din imobile la care trimite "
                              "'Imobilul construcției'."), self.key_combo)
            return
        if key_field and not self.confirm_distinct(imobile, key_field):
            return

        self.save_remembered()
        imobile_snaps = [LayerSnapshot(lyr) for lyr in imobile]
        join_fields = []
        if self.join_group.isChecked():
            merged = merged_fields(imobile_snaps)
            chosen = {merged.lookupField(name) for name in self.join_list.checked_texts()}
            join_fields = [merged.at(index) for index in sorted(chosen - {-1})]
        self.start_task(bts.BuildingTopologyTask(
            imobile=imobile_snaps,
            constructii=[LayerSnapshot(lyr) for lyr in constructii],
            buffer_m=self.buffer.value(), threshold=self.threshold.value(),
            join_fields=join_fields, link_field=link_field, key_field=key_field,
            finished_cb=self._on_finished),
            self.tr("Se corectează construcțiile..."))

    def _on_finished(self, ok, task):
        self.task_done()
        if task.isCanceled():
            self.notice(self.tr("Corectarea a fost anulată."))
            return
        if not ok:
            self.fail(self.tr("Corectarea nu s-a putut finaliza:\n\n{0}").format(
                task.error_msg or self.tr("eroare necunoscută")))
            return
        unwritten = []
        layers = _ui_helpers.add_layers(
            [self._build_layer(snapshot, rows, task, unwritten)
             for snapshot, rows in zip(task.constructii, task.results)],
            self.tr("Topologie Construcții"))

        states = task.counts
        total = sum(states.values())
        headline = fmt.plural(total, self.tr("S-a verificat {0}."),
                              self.tr("S-au verificat {0}.")).format(
            fmt.count(total, self.tr("construcție"), self.tr("construcții")))
        counts = [(state, states[state], bts.STATE_COLORS[state]) for state in bts.STATES
                  if states[state] or state in (bts.UNCHANGED, bts.FIXED)]
        notes = []
        if states[bts.LOSS]:
            # 'stare' is 'stare_2' in a layer that has a 'stare' column of its own
            columns = " / ".join(f"'{name}'" for name in sorted(
                {bts.output_fields(snapshot.fields, task.join_fields)[1]["stare"]
                 for snapshot in task.constructii}))
            notes.append(fmt.plural(
                states[bts.LOSS],
                self.tr("{0} pierde mai mult de {1}. Filtrați layerul după coloana "
                        "{2} pentru a o verifica."),
                self.tr("{0} pierd mai mult de {1}. Filtrați layerul după coloana "
                        "{2} pentru a le verifica."),
            ).format(fmt.count(states[bts.LOSS], self.tr("construcție"), self.tr("construcții")),
                     fmt.area(self.threshold.value()), columns))
        notes += self.report_notes(task)
        notes += unwritten
        notes += stereo70.notes(task.snapshots())
        notes.append(fmt.plural(
            len(layers),
            self.tr("Layer creat: {0}. Este temporar; salvați-l dacă doriți să-l păstrați."),
            self.tr("Layere create: {0}. Sunt temporare; salvați-le dacă doriți să le "
                    "păstrați."),
        ).format(", ".join(layer.name() for layer in layers)))
        self.show_result(headline, counts=counts, notes=notes, layers=layers,
                         warning=bool(states[bts.LOSS] or states[bts.FAILED]
                                      or states[bts.NO_PARCEL] or states[bts.ELSEWHERE]
                                      or unwritten or self.report_notes(task)))

    def _build_layer(self, snapshot, rows, task, unwritten):
        """The corrected copy of one construcții layer, in Stereo 70, not yet in the project.
        unwritten, a list, receives a note when the layer could not hold every
        construcție."""
        fields, names, _join_names = bts.output_fields(snapshot.fields, task.join_fields)
        name = f"{snapshot.name}_corectat"
        layer = QgsMemoryProviderUtils.createMemoryLayer(
            name, fields, snapshot.wkb_type, stereo70.crs())
        features = bts.result_features(rows, fields, task.join_fields, snapshot.wkb_type)
        missing, error = _ui_helpers.add_features(layer, features)
        if missing:
            unwritten.append((fmt.plural(
                missing, self.tr("{0} din {1} nu a putut fi scrisă în layerul '{2}'."),
                self.tr("{0} din {1} nu au putut fi scrise în layerul '{2}'.")).format(
                    fmt.count(missing, self.tr("construcție"), self.tr("construcții")),
                    fmt.number(len(features), 0), name) + f" {error}").strip())
        layer_style.style_categories(
            layer, names["stare"], [(s, s, bts.STATE_COLORS[s]) for s in bts.STATES])
        return layer
