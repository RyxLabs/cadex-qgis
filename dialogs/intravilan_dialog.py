# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Raport Intravilan / Extravilan window."""

import os

from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QVBoxLayout,
)
from qgis.core import (  # type: ignore
    QgsMemoryProviderUtils, QgsProject, QgsProviderRegistry, QgsVectorLayer,
)

from . import _ui_helpers
from .base_dialog import BaseDialog
from .widgets import (
    CheckList, LayerChecklist, PathRow, add_field_row, data_field_names,
    fill_field_combo, make_form,
)
from .. import features
from ..services import fmt, intravilan_service, layer_style, outputs, roles, stereo70
from ..services.layer_sources import LayerSnapshot


def _table_of(layer) -> str:
    """The GeoPackage table a layer reads, "" when its source names none: OGR then opens the
    first one."""
    parts = QgsProviderRegistry.instance().decodeUri(layer.providerType(), layer.source())
    return str(parts.get("layerName") or "")


class IntravilanDialog(BaseDialog):
    FEATURE = "intravilan_report"

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setMinimumWidth(660)
        # the project layers of the result being replaced, taken out for the run and put back
        # when it fails: [(layer, layer tree group, index in the group)]
        self._released = []
        self._setup_ui()

    def _setup_ui(self):
        # side by side: stacked, the lists make the window too tall for a laptop screen
        lists = QHBoxLayout()
        imobile_group = QGroupBox(self.tr("Layere de Imobile"))
        imobile_layout = QVBoxLayout(imobile_group)
        self.imobile = LayerChecklist("roles/imobile")
        self.imobile.changed.connect(self._refresh_fields)
        imobile_layout.addWidget(self.imobile)
        self.selected_only = QCheckBox(self.tr("Doar imobilele selectate"))
        self.selected_only.setToolTip(self.tr(
            "Imobilele se pot selecta pe hartă cu fereastra deschisă."))
        imobile_layout.addWidget(self.selected_only)
        lists.addWidget(imobile_group)

        intra_group = QGroupBox(self.tr("Layere Contur Intravilan"))
        intra_layout = QVBoxLayout(intra_group)
        self.intravilan = LayerChecklist("roles/intravilan")
        intra_layout.addWidget(self.intravilan)
        intra_layout.addStretch()
        lists.addWidget(intra_group)
        self.body.addLayout(lists)

        analysis = QGroupBox(self.tr("Analiză"))
        form = make_form(analysis)
        self.id_combo = QComboBox()
        self.id_combo.setToolTip(self.tr(
            "Câmpul după care se numesc imobilele în clasamentele din raport."))
        form.addRow(self.tr("ID imobil:"), self.id_combo)
        self.owner_combo = QComboBox()
        self.owner_combo.setToolTip(self.tr(
            "Câmpul cu proprietarul. Raportul numără imobilele cu proprietar "
            "NEIDENTIFICAT și pe cele fără proprietar completat."))
        form.addRow(self.tr("Proprietar:"), self.owner_combo)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.0, 1000.0)
        self.tolerance.setDecimals(2)
        self.tolerance.setValue(0.5)
        self.tolerance.setSuffix(" mp")
        self.tolerance.setToolTip(self.tr(
            "O porțiune de cel mult atât, de o parte sau de alta a limitei de "
            "intravilan, nu schimbă încadrarea imobilului, astfel încât diferențele "
            "de digitizare de-a lungul limitelor comune nu contează."))
        form.addRow(self.tr("Toleranță:"), self.tolerance)
        self.body.addWidget(analysis)

        out_group = QGroupBox(self.tr("Rezultat"))
        out_form = make_form(out_group)
        self.gpkg_row = PathRow("save", self.tr("Salvați layerul GeoPackage"),
                                self.tr("GeoPackage (*.gpkg)"), ".gpkg")
        self.gpkg_row.edit.setPlaceholderText(
            self.tr("Opțional; fără el, layerul este temporar"))
        self.gpkg_row.picked.connect(lambda path: self._suggest(self.excel_row, path, ".xlsx"))
        out_form.addRow(self.tr("Layer GeoPackage:"), self.gpkg_row)
        self.only_integral = QCheckBox(self.tr("Doar imobilele integral în intravilan"))
        self.only_integral.setToolTip(self.tr(
            "Nebifat, layerul cuprinde toate imobilele, colorate după încadrare."))
        add_field_row(out_form, self.only_integral)
        self.add_to_project = QCheckBox(self.tr("Adaugă layerul în proiect"))
        self.add_to_project.setChecked(True)
        add_field_row(out_form, self.add_to_project)
        self.excel_row = PathRow("save", self.tr("Salvați raportul Excel"),
                                 self.tr("Excel (*.xlsx)"), ".xlsx")
        self.excel_row.edit.setPlaceholderText(self.tr("Opțional"))
        self.excel_row.picked.connect(lambda path: self._suggest(self.gpkg_row, path, ".gpkg"))
        out_form.addRow(self.tr("Raport Excel:"), self.excel_row)
        self.field_list = CheckList(self.tr("Bifați un layer de imobile."), height=90)
        self.field_list.setToolTip(self.tr(
            "Coloanele imobilelor copiate în fiecare foaie cu imobile a raportului, "
            "înaintea suprafețelor calculate."))
        out_form.addRow(self.tr("Coloane în Excel:"), self.field_list)
        self.body.addWidget(out_group)

        self.set_tip(self.tr("Parcelele din intravilan și din extravilan se pot lista și "
                             "direct din fișierele CGXML, cu rapoartele gratuite de pe"),
                     features.WEBSITE_REPORTS, "cadex.ro")
        self.run_btn = self.add_run_buttons(self.tr("Generează"), self._run)

        self.remember("tolerance", self.tolerance)
        self.remember("only_integral", self.only_integral)
        self.remember("add_to_project", self.add_to_project)
        self.remember("gpkg", self.gpkg_row.edit)
        self.remember("excel", self.excel_row.edit)
        self.restore_remembered()
        self._refresh_fields()

    def dispose(self):
        # puts back the layers taken out for a run that the plugin's unloading stops
        self._put_back()
        super().dispose()

    @staticmethod
    def _suggest(row, path, suffix):
        # fills the other, still empty output with the same name
        if path and not row.path():
            row.set_path(os.path.splitext(path)[0] + suffix)

    def _refresh_fields(self):
        layers = self.imobile.checked_layers()
        names = data_field_names(layers)
        fill_field_combo(self.id_combo, names, "fields/id",
                         self.tr("(numărul elementului)"), self.id_combo.currentData())
        fill_field_combo(self.owner_combo, names, "fields/owner",
                         self.tr("(fără statistici de proprietari)"),
                         self.owner_combo.currentData())
        self.field_list.set_names(data_field_names(layers))

    def _run(self):
        imobile = self.imobile.checked_layers()
        intravilan = self.intravilan.checked_layers()
        if not imobile:
            self.warn(self.tr("Bifați cel puțin un layer de imobile."), self.imobile.list)
            return
        if not intravilan:
            self.warn(self.tr("Bifați cel puțin un layer de contur intravilan."),
                      self.intravilan.list)
            return
        shared = {lyr.id() for lyr in imobile} & {lyr.id() for lyr in intravilan}
        if shared:
            self.warn(self.tr("Același layer este bifat și la imobile, și la contur "
                              "intravilan. Debifați-l dintr-una dintre liste."))
            return
        for layer in imobile + intravilan:
            if not self.validate_layer(layer, layer.name()):
                return
        if not self.check_crs(imobile + intravilan):
            return
        id_field = self.id_combo.currentData() or None
        if id_field and not self.confirm_distinct(imobile, id_field):
            return
        selected_only = self.selected_only.isChecked()
        if selected_only and not any(lyr.selectedFeatureCount() for lyr in imobile):
            self.warn(self.tr("Niciun imobil nu este selectat. Selectați imobilele pe "
                              "hartă sau debifați 'Doar imobilele selectate'."))
            return
        gpkg = self.gpkg_row.path()
        excel = self.excel_row.path()
        if not gpkg and not excel and not self.add_to_project.isChecked():
            self.warn(self.tr("Alegeți cel puțin un rezultat: layerul GeoPackage, raportul "
                              "Excel sau layerul în proiect."), self.gpkg_row.edit)
            return
        inputs = self._reading_outputs(gpkg, excel, imobile + intravilan)
        if inputs:
            self.warn(self.tr("Fișierul rezultat este sursa layerului '{0}', bifat ca intrare. "
                              "Alegeți alt fișier rezultat.").format(inputs[0].name()),
                      self.gpkg_row.edit)
            return
        if not self._confirm_outputs(gpkg, excel):
            return
        released = self._release(gpkg, excel)
        if released is None:
            return
        self._released = released

        self.save_remembered()
        self.start_task(intravilan_service.IntravilanTask(
            imobile=[LayerSnapshot(lyr, selected_only=selected_only) for lyr in imobile],
            intravilan=[LayerSnapshot(lyr) for lyr in intravilan],
            excel_fields=self.field_list.checked_texts(),
            id_field=self.id_combo.currentData() or None,
            owner_field=self.owner_combo.currentData() or None,
            tolerance=self.tolerance.value(), gpkg_path=gpkg or None,
            excel_path=excel or None, only_integral=self.only_integral.isChecked(),
            finished_cb=self._on_finished),
            self.tr("Se calculează suprafețele..."))

    # --- outputs ---

    def _confirm_outputs(self, gpkg, excel) -> bool:
        """True when the outputs can be written and the user agrees to replace what exists.
        An existing GeoPackage keeps its other layers: only the report's table is replaced. A
        file with the GeoPackage's name that is not a GeoPackage is replaced whole."""
        paths = [p for p in (gpkg, excel) if p]
        if not gpkg or not os.path.exists(gpkg):
            return self.confirm_overwrite(paths)
        blocked = outputs.blocked(paths)
        if blocked:
            self.fail("\n\n".join(message for _path, message in blocked))
            return False
        try:
            tables = intravilan_service.geopackage_tables(gpkg)
        except OSError as error:
            self.fail(outputs.write_error(gpkg, error))
            return False
        if tables is None:
            return self.confirm_overwrite(paths)
        table = intravilan_service.TABLE
        if excel and os.path.exists(excel):
            question = self.tr(
                "Fișierele {0} și {1} există deja. Raportul Excel se înlocuiește, iar în {1} "
                "raportul se scrie ca layerul '{2}', în locul celui cu același nume, dacă "
                "există; celelalte layere din fișier rămân neschimbate. Continuați?").format(
                    os.path.basename(excel), os.path.basename(gpkg), table)
        else:
            question = self.tr(
                "Fișierul {0} există deja. Raportul se scrie în el ca layerul '{1}', în locul "
                "celui cu același nume, dacă există; celelalte layere din fișier rămân "
                "neschimbate. Continuați?").format(os.path.basename(gpkg), table)
        return self.confirm(self.tr("Suprascriere"), question, self.tr("Continuă"),
                            self.tr("Anulează"))

    def _reading_outputs(self, gpkg, excel, layers=None):
        """The layers, by default every project layer, that read what the run replaces: the
        Excel file and the report's table in the GeoPackage. The other tables of the
        GeoPackage stay as they are."""
        using = self.layers_reading([excel], layers) if excel else []
        if gpkg:
            # a layer whose source names no table reads the first one, maybe the report
            using += [layer for layer in self.layers_reading([gpkg], layers)
                      if _table_of(layer).lower() in ("", intravilan_service.TABLE)]
        return using

    def _release(self, gpkg, excel):
        """Take out of the project, after asking, the layers that read the outputs: the Excel
        file and the report's table in the GeoPackage. Returns them with their places in the
        layer tree, [] when there are none, None when the user declines."""
        using = self._reading_outputs(gpkg, excel)
        if not using:
            return []
        names = ", ".join(f"'{layer.name()}'" for layer in using)
        question = fmt.plural(len(using), self.tr(
            "Rezultatul este deschis în proiect ca layerul {0}. Pentru a-l înlocui, layerul "
            "se scoate din proiect; dacă raportul nu se poate finaliza, revine. Continuați?"),
            self.tr(
                "Rezultatul este deschis în proiect ca layerele {0}. Pentru a-l înlocui, "
                "layerele se scot din proiect; dacă raportul nu se poate finaliza, revin. "
                "Continuați?"))
        if not self.confirm(self.tr("Suprascriere"), question.format(names),
                            self.tr("Elimină și continuă"), self.tr("Anulează")):
            return None
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        taken = []
        for layer in using:
            node = root.findLayer(layer.id())
            group = node.parent() if node is not None else root
            index = group.children().index(node) if node is not None else 0
            taken.append((project.takeMapLayer(layer), group, index))
        return taken

    def _put_back(self):
        """Return the layers taken out for a run to the project, each at its place."""
        project = QgsProject.instance()
        for layer, group, index in reversed(self._released):
            project.addMapLayer(layer, False)
            try:
                group.insertLayer(index, layer)
            except RuntimeError:
                # the group was deleted during the run
                project.layerTreeRoot().insertLayer(0, layer)
        self._released = []

    # --- result ---

    def _result_layer(self, task):
        """The report as a layer, styled: its table in the GeoPackage, else a temporary layer.
        The style and the report marker are saved in the GeoPackage, so the report is not
        preselected as an intravilan contour when the file is opened in another project. In a
        GeoPackage that holds other layers too, the layer is named after the tool, else after
        the file."""
        if not task.gpkg_path:
            layer = QgsMemoryProviderUtils.createMemoryLayer(
                self.tr("Raport intravilan"), task.output_fields,
                intravilan_service.WKB_TYPE, stereo70.crs())
            provider = layer.dataProvider()
            # one at a time: given a list, the provider stops at the first feature it refuses
            for feature in task.output_features():
                provider.addFeature(feature)
            layer.updateExtents()
            self._style(layer, task)
            return layer
        uri = f"{task.gpkg_path}|layername={task.table}"
        if task.other_layers:
            name = self.tr("Raport intravilan")
        else:
            name = os.path.splitext(os.path.basename(task.gpkg_path))[0]
        # the style is saved through a layer of its own, before the result layer opens the
        # table: saving switches the GeoPackage to a write-ahead log, and a layer opened
        # earlier then fails to count its features ("unable to open database file")
        saved = self._open_report(uri, name, task)
        if saved is None:
            return None
        layer_style.store_with_file(saved)
        del saved
        return self._open_report(uri, name, task)

    def _open_report(self, uri, name, task):
        """The report's table as a styled layer marked as a report, None when it cannot be
        opened. The style the table held is not loaded: its CADex markers may belong to an
        earlier result."""
        options = QgsVectorLayer.LayerOptions()
        options.loadDefaultStyle = False
        layer = QgsVectorLayer(uri, name, "ogr", options)
        if not layer.isValid():
            self.log(f"Layer rezultat invalid: {task.gpkg_path}")
            return None
        self._style(layer, task)
        roles.mark_report(layer)
        return layer

    @staticmethod
    def _style(layer, task):
        layer_style.style_categories(
            layer, task.status_field,
            [(s, s, intravilan_service.STATUS_COLORS[s]) for s in intravilan_service.STATUSES])

    def _shortfall(self, written, offered, error):
        """The note for the imobile a result layer refused, "" when it took every one."""
        missing = offered - written
        if missing <= 0:
            return ""
        message = fmt.plural(
            missing, self.tr("{0} din {1} nu a putut fi scris în layerul rezultat."),
            self.tr("{0} din {1} nu au putut fi scrise în layerul rezultat."),
        ).format(fmt.count(missing, self.tr("imobil"), self.tr("imobile")),
                 fmt.number(offered, 0))
        return f"{message} {error}" if error else message

    def _on_finished(self, ok, task):
        self.task_done()
        if task.isCanceled():
            self._put_back()
            self.notice(self.tr("Raportul a fost anulat."))
            return
        if not ok:
            self._put_back()
            self.fail(self.tr("Raportul nu s-a putut finaliza:\n\n{0}").format(
                task.error_msg or self.tr("eroare necunoscută")))
            return
        # the replaced result's layers are stale; dropping them closes their files
        self._released = []
        notes = []
        if task.skipped:
            notes.append(fmt.plural(
                task.skipped, self.tr("{0} fără geometrie sau cu suprafața 0 a fost ignorat."),
                self.tr("{0} fără geometrie sau cu suprafața 0 au fost ignorate."),
            ).format(fmt.count(task.skipped, self.tr("imobil"), self.tr("imobile"))))
        layer, shortfall = None, ""
        if task.gpkg_path or self.add_to_project.isChecked():
            layer = self._result_layer(task)
        if task.gpkg_path:
            shortfall = self._shortfall(task.written, task.expected, task.write_error)
        elif layer is not None:
            errors = layer.dataProvider().errors()
            shortfall = self._shortfall(layer.featureCount(), task.output_count(),
                                        errors[0] if errors else "")
        if shortfall:
            notes.append(shortfall)
        reports = self.report_notes(task)
        notes += reports
        layers = []
        if layer is not None and self.add_to_project.isChecked():
            layers = _ui_helpers.add_layers([layer])
            notes.append(self.tr("Layer adăugat în proiect: {0}.").format(layer.name())
                         + ("" if task.gpkg_path else " " + self.tr(
                             "Este temporar; salvați-l dacă doriți să-l păstrați.")))
        notes += stereo70.notes(task.snapshots())

        rows = task.rows
        counts = [(s, sum(1 for r in rows if r["situatie"] == s),
                   intravilan_service.STATUS_COLORS[s])
                  for s in intravilan_service.STATUSES]
        counts += [None,
                   (self.tr("Suprafață în intravilan"),
                    fmt.area(sum(r["sup_intra"] for r in rows), 0), None),
                   (self.tr("Suprafață în extravilan"),
                    fmt.area(sum(r["sup_extra"] for r in rows), 0), None)]
        # a report that was not written is not offered for opening
        excel = task.excel_path if not task.report_error else None
        self.show_result(
            fmt.plural(len(rows), self.tr("S-a analizat {0}."),
                       self.tr("S-au analizat {0}.")).format(
                fmt.count(len(rows), self.tr("imobil"), self.tr("imobile"))),
            counts=counts, notes=notes, file_path=excel or task.gpkg_path,
            warning=bool(shortfall or reports), layers=layers)
