# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Diminuare Suprafețe window. The result is recomputed on every input change."""

import os
import zipfile
from datetime import datetime

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtGui import QBrush, QColor  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QComboBox, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from qgis.core import QgsMapLayerProxyModel, QgsVectorLayer  # type: ignore
from qgis.gui import QgsMapLayerComboBox  # type: ignore

from . import _ui_helpers
from .base_dialog import SCREEN_SHARE, BaseDialog
from .widgets import (
    ExistingFiles, PathRow, StatusLabel, add_field_row, fill_field_combo, make_form,
    preselect_layer, user_chose,
)
from ..services import (
    dialog_memory, diminuare_service, fmt, outputs, roles, settings_service, xlsx_report,
)

# same row tints as the Excel output
FIXED_TINT = "#" + xlsx_report.NOTE
BAD_TINT = "#" + xlsx_report.BAD
ROW_TEXT = "#" + xlsx_report.BODY
TABLE_MIN_ROWS = 4
DEFAULT_HEIGHT = 900
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class DiminuareDialog(BaseDialog):
    FEATURE = "area_reduction"
    FIT_HEIGHT = False

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setMinimumWidth(660)
        self._header = []
        self._rows = []
        self._numbers = []
        self._hidden = []
        self._result = None
        self._loaded_path = ""
        self._selection_layer = None
        # where the contour area came from, for the Sumar; empty when typed by hand
        self._contour_origin = ""
        self._setting_contour = False
        self._setup_ui()
        # a saved geometry replaces this on show
        wanted = max(DEFAULT_HEIGHT, self.sizeHint().height())
        self.resize(700, min(wanted, int(self._available_height() * SCREEN_SHARE)))

    def _setup_ui(self):
        excel = QGroupBox(self.tr("Tabelul cu Parcele"))
        form = make_form(excel)
        self.file_row = PathRow("open", self.tr("Alegeți tabelul cu parcelele"),
                                self.tr("Excel sau CSV (*.xlsx *.csv);;Toate fișierele (*)"))
        self.file_row.picked.connect(lambda _path: self._load_file())
        self.file_row.edit.editingFinished.connect(self._on_path_typed)
        reload_btn = _ui_helpers.small_button(
            "", self.tr("Recitește tabelul, după ce l-ați modificat în Excel"),
            _ui_helpers.theme_icon("/mActionRefresh.svg"))
        reload_btn.clicked.connect(lambda: self._load_file(keep_sheet=True))
        file_line = QHBoxLayout()
        file_line.setSpacing(4)
        file_line.addWidget(self.file_row, 1)
        file_line.addWidget(reload_btn)
        form.addRow(self.tr("Tabel Excel / CSV:"), file_line)
        self.sheet_combo = QComboBox()
        self.sheet_combo.activated.connect(lambda *_: self._load_file(keep_sheet=True))
        form.addRow(self.tr("Foaie:"), self.sheet_combo)
        self.area_combo = QComboBox()
        self.area_combo.currentIndexChanged.connect(lambda *_: self._calculate())
        form.addRow(self.tr("Coloana suprafeței din acte:"), self.area_combo)
        self.fixed_combo = QComboBox()
        self.fixed_combo.setToolTip(self.tr(
            "Parcelele care au o valoare în această coloană au CF/IE și își păstrează "
            "suprafața. O celulă necompletată sau cu 0, NU, NO, FALSE ori FĂRĂ "
            "înseamnă fără CF/IE."))
        self.fixed_combo.currentIndexChanged.connect(lambda *_: self._calculate())
        form.addRow(self.tr("Coloana CF/IE:"), self.fixed_combo)
        self.rows_label = StatusLabel()
        add_field_row(form, self.rows_label)
        self.body.addWidget(excel)

        contour = QGroupBox(self.tr("Suprafața Conturului (C)"))
        contour_form = make_form(contour)
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        self.layer_combo.setAllowEmptyLayer(True)
        # empty unless a layer matches the contour names or is the active layer
        self.layer_combo.setLayer(None)
        preselect_layer(self.layer_combo, "roles/contur", self.iface)
        self.layer_combo.layerChanged.connect(self._watch_selection)
        contour_form.addRow(self.tr("Layer contur:"), self.layer_combo)
        selection_line = QHBoxLayout()
        self.selected_only = QCheckBox(self.tr("Doar elementele selectate"))
        self.selected_only.setChecked(True)
        self.selected_only.setToolTip(self.tr(
            "Se preselectează când sunt elemente selectate sau când layerul are mai multe "
            "contururi; un layer cu un singur contur se folosește întreg."))
        # set from the layer until the user clicks the box
        self._selection_chosen = False
        self.selected_only.clicked.connect(self._on_selection_chosen)
        selection_line.addWidget(self.selected_only)
        self.selection_label = QLabel("")
        selection_line.addWidget(self.selection_label)
        selection_line.addStretch()
        add_field_row(contour_form, selection_line)
        area_line = QHBoxLayout()
        self.contour_spin = QDoubleSpinBox()
        self.contour_spin.setRange(0.0, 999_999_999.99)
        self.contour_spin.setDecimals(2)
        self.contour_spin.setSuffix(" mp")
        self.contour_spin.setToolTip(self.tr(
            "Preluată din geometrie sau introdusă manual."))
        self.contour_spin.valueChanged.connect(self._on_contour_changed)
        area_line.addWidget(self.contour_spin, 1)
        take_btn = QPushButton(self.tr("Preia din geometrie"))
        take_btn.setToolTip(self.tr("Suma suprafețelor din layerul contur. Cu 'Doar "
                                    "elementele selectate' bifat, doar a celor selectate."))
        take_btn.clicked.connect(self._take_area)
        area_line.addWidget(take_btn)
        contour_form.addRow(self.tr("Suprafață contur:"), area_line)
        self.body.addWidget(contour)

        calc = QGroupBox(self.tr("Calcul"))
        calc_form = make_form(calc)
        formula = QLabel(self.tr(
            "A = suma suprafețelor din acte, B = suma parcelelor cu CF/IE, "
            "C = suprafața conturului\n"
            "Suprafața nouă = suprafața din acte × (C − B) / (A − B), pentru "
            "fiecare parcelă fără CF/IE"))
        formula.setWordWrap(True)
        calc_form.addRow(formula)
        self.round_combo = QComboBox()
        self.round_combo.addItem(self.tr("la metru pătrat"), 0)
        self.round_combo.addItem(self.tr("la două zecimale"), 2)
        self.round_combo.setToolTip(self.tr(
            "Suprafețele noi se rotunjesc astfel încât suma tuturor parcelelor să fie "
            "egală cu suprafața conturului, rotunjită la aceeași precizie."))
        self.round_combo.currentIndexChanged.connect(lambda *_: self._calculate())
        calc_form.addRow(self.tr("Rotunjire:"), self.round_combo)
        self.body.addWidget(calc)

        result = QGroupBox(self.tr("Rezultat"))
        result_layout = QVBoxLayout(result)
        self.status = StatusLabel()
        result_layout.addWidget(self.status)
        self.summary = QLabel("")
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        result_layout.addWidget(self.summary)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            self.tr("Rând"), self.tr("Din acte (mp)"), self.tr("CF/IE"),
            self.tr("Diminuată (mp)")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(
            header.sizeHint().height() + 2 * self.table.frameWidth()
            + TABLE_MIN_ROWS * self.table.verticalHeader().defaultSectionSize())
        # the scroll area sizes the body to its preferred height, so the table's own size hint
        # would add a scroll range; the table takes the free height instead
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        result_layout.addWidget(self.table, 1)
        self.legend = self._legend()
        result_layout.addWidget(self.legend)
        self.body.addWidget(result, 1)

        # not the default button, so Enter in a field does not open the save dialog
        self.save_btn = self.add_button(self.tr("Salvează Excel..."),
                                        QDialogButtonBox.ButtonRole.AcceptRole, self._save,
                                        primary=True)
        self.add_close_button()

        self.remember("file", self.file_row.edit)
        self.remember("rounding", self.round_combo)
        self.restore_remembered()
        self._watch_selection()
        self._calculate()
        # the remembered table can be on a network drive out of reach, where a look at it
        # takes tens of seconds, so it is looked for on a worker thread and read once found
        self._existing = ExistingFiles(self)
        self._existing.found.connect(self._open_remembered)
        if self.file_row.path():
            self.rows_label.set_status(self.tr("Se caută tabelul..."))
            self._existing.check([self.file_row.path()])

    def _legend(self):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        for color, text in (
                (FIXED_TINT, self.tr("are CF/IE, își păstrează suprafața")),
                (BAD_TINT, self.tr("rând de total sau fără o suprafață validă, "
                                   "rămâne neschimbat"))):
            swatch = QLabel()
            swatch.setPixmap(_ui_helpers.swatch_icon(color).pixmap(12, 12))
            row.addWidget(swatch)
            row.addWidget(QLabel(text))
            row.addSpacing(12)
        row.addStretch()
        return box

    def dispose(self):
        self._unwatch_selection()
        super().dispose()

    def done(self, result):
        if self._result is not None:
            self._remember_choices()
        super().done(result)

    # --- inputs ---

    def _open_remembered(self, found):
        if self._loaded_path:
            return
        self.rows_label.set_status("")
        if self.file_row.path() in found:
            self._load_file(quiet=True)

    def _on_path_typed(self):
        path = self.file_row.path()
        if path and path != self._loaded_path:
            self._load_file()

    def _load_file(self, keep_sheet=False, quiet=False):
        path = self.file_row.path()
        if not path:
            return
        if not os.path.isfile(path):
            self._forget_table()
            if not quiet:
                self.warn(self.tr("Fișierul '{0}' nu există.").format(path), self.file_row.edit)
            return
        sheet = self.sheet_combo.currentText() if keep_sheet else None
        try:
            table = diminuare_service.read_sheet(path, sheet)
        except zipfile.BadZipFile:
            self._forget_table()
            # an Excel 97-2003 .xls, or a file renamed to .xlsx
            if not quiet:
                self.warn(self.tr("Fișierul nu este un tabel .xlsx. Deschideți-l în Excel "
                                  "și salvați-l ca 'Registru de lucru Excel (.xlsx)' sau "
                                  "ca CSV, apoi alegeți-l din nou."), self.file_row.edit)
            return
        except Exception as e:
            self._forget_table()
            if not quiet:
                self.fail(self.tr("Tabelul nu poate fi citit:\n\n{0}").format(e))
            return
        first = not self._loaded_path
        self._loaded_path = path
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        self.sheet_combo.addItems(table.names)
        if sheet in table.names:
            self.sheet_combo.setCurrentText(sheet)
        self.sheet_combo.blockSignals(False)
        self._header, self._rows, self._numbers = table.header, table.rows, table.numbers
        self._hidden = table.hidden
        keep_area = self.area_combo.currentData() or self._stored("area_column")
        keep_fixed = self._fixed_choice(first)
        fill_field_combo(self.area_combo, table.header, "fields/area_deed", keep=keep_area)
        fill_field_combo(self.fixed_combo, table.header, None,
                         self.tr("(nicio parcelă nu are CF/IE)"), keep=keep_fixed)
        if keep_fixed == "":
            # the user's choice of no column stays a choice through reloads
            _ui_helpers.apply_widget_state(self.fixed_combo, "")
        elif self.fixed_combo.currentData() != keep_fixed:
            match = roles.pick_field(table.header, settings_service.patterns("fields/ie"))
            if match:
                self.fixed_combo.setCurrentIndex(self.fixed_combo.findData(match))
        self._show_rows(table)
        self._calculate()

    def _show_rows(self, table):
        status = self.tr("{0} în tabel.").format(
            fmt.count(len(table.rows), self.tr("rând"), self.tr("rânduri")))
        if table.header_number > 1:
            status += " " + self.tr("Numele coloanelor sunt pe rândul {0}.").format(
                table.header_number)
        if table.hidden:
            status += " " + fmt.plural(
                len(table.hidden),
                self.tr("{0} ascuns în Excel a fost lăsat deoparte: {1}."),
                self.tr("{0} ascunse în Excel au fost lăsate deoparte: {1}."),
            ).format(fmt.count(len(table.hidden), self.tr("rând"), self.tr("rânduri")),
                     diminuare_service.row_list(table.hidden))
        self.rows_label.set_status(status, "ok" if table.rows and not table.hidden
                                   else "warning")

    def _forget_table(self):
        """Drop the table read before: its result would be saved under the name of the file
        now in the path field."""
        self._loaded_path = ""
        self._header, self._rows, self._numbers, self._hidden = [], [], [], []
        self.sheet_combo.clear()
        self.rows_label.set_status("")
        self._calculate()

    def _fixed_choice(self, first):
        """The CF/IE column to keep selected: "" when the user chose none, in this window or
        at the last run; None when no column was chosen, and one is preselected by name.
        first is True for the first table the window reads."""
        combo = self.fixed_combo
        if user_chose(combo):
            return combo.currentData() or ""
        if combo.currentData():
            return combo.currentData()
        if first:
            name = type(self).__name__
            stored = dialog_memory.load(name, "fixed_column", "") or ""
            # read from the settings file, the value is text
            chosen = str(dialog_memory.load(name, "fixed_column_chosen", False)).lower()
            if stored or chosen in ("true", "1"):
                return stored
        return None

    def _stored(self, key):
        return dialog_memory.load(type(self).__name__, key, "") or None

    def _watch_selection(self, *_args):
        # keeps the selected feature count next to the check box current
        self._unwatch_selection()
        layer = self.layer_combo.currentLayer()
        if isinstance(layer, QgsVectorLayer):
            layer.selectionChanged.connect(self._show_selection)
            self._selection_layer = layer
        self._show_selection()

    def _unwatch_selection(self):
        if self._selection_layer is not None:
            try:
                self._selection_layer.selectionChanged.disconnect(self._show_selection)
            except (TypeError, RuntimeError):
                pass  # layer already deleted
            self._selection_layer = None

    def _show_selection(self, *_args):
        try:
            layer = self._selection_layer
            count = layer.selectedFeatureCount() if layer is not None else 0
            several = layer is not None and layer.featureCount() > 1
        except RuntimeError:
            self._selection_layer = None
            count, several = 0, False
        if count == 0:
            text = self.tr("niciun element selectat")
        else:
            text = fmt.count(count, self.tr("element selectat"), self.tr("elemente selectate"))
        self.selection_label.setText(f"({text})")
        if not self._selection_chosen:
            # all the contours of a layer holding several would add up to the wrong area
            self.selected_only.setChecked(count > 0 or several)

    def _on_selection_chosen(self, *_args):
        self._selection_chosen = True

    def _on_contour_changed(self, *_args):
        if not self._setting_contour:
            self._contour_origin = ""
        self._calculate()

    def _take_area(self):
        layer = self.layer_combo.currentLayer()
        if not self.validate_layer(layer, self.tr("de contur"), focus=self.layer_combo):
            return
        if not self.check_crs([layer]):
            return
        if self.selected_only.isChecked():
            features = list(layer.selectedFeatures())
            if not features:
                self.warn(self.tr("Nu este selectat niciun element în layerul '{0}'. "
                                  "Selectați conturul pe hartă sau debifați 'Doar "
                                  "elementele selectate'.").format(layer.name()))
                return
            used = fmt.count(len(features), "element selectat", "elemente selectate")
        else:
            features = list(layer.getFeatures())
            used = "tot layerul ({0})".format(fmt.count(len(features), "element", "elemente"))
        area = diminuare_service.contour_area(
            (f.geometry() for f in features if f.hasGeometry()), layer.crs(), layer.extent())
        if area <= 0:
            self.warn(self.tr("Geometria aleasă are suprafața 0."))
            return
        self._setting_contour = True
        try:
            self.contour_spin.setValue(round(area, 2))
        finally:
            self._setting_contour = False
        self._contour_origin = f"{layer.name()}, {used}"

    # --- calculation ---

    def _clear_result(self, message="", level="info"):
        self._result = None
        self.save_btn.setEnabled(False)
        self.status.set_status(message, level)
        self.summary.setText("")
        self.summary.setVisible(False)
        self.table.setRowCount(0)
        self.legend.setVisible(False)

    def _calculate(self):
        """Recompute the result; a missing input is reported in the result section."""
        if not self._rows:
            self._clear_result(self.tr("Alegeți tabelul cu parcelele."))
            return
        area_name = self.area_combo.currentData()
        if not area_name:
            self._clear_result(self.tr("Alegeți coloana cu suprafața din acte."))
            return
        contour = self.contour_spin.value()
        if contour <= 0:
            self._clear_result(self.tr("Completați suprafața conturului: preluați-o din "
                                       "geometrie sau introduceți-o manual."))
            return
        area_col = self._header.index(area_name)
        fixed_name = self.fixed_combo.currentData()
        fixed_col = self._header.index(fixed_name) if fixed_name else None
        areas = diminuare_service.parse_column([r[area_col] for r in self._rows])
        flags = [diminuare_service.has_value(r[fixed_col]) if fixed_col is not None
                 else False for r in self._rows]
        decimals = self.round_combo.currentData()
        result = diminuare_service.compute(areas, flags, contour, decimals,
                                           diminuare_service.total_rows(self._rows))
        if result.error:
            self._clear_result(result.error, "warning")
            return
        self._result = (result, area_col, fixed_col)

        notes = []
        level = None
        if result.contour > result.total_deed:
            notes.append(self.tr("Conturul este mai mare decât suma din acte, deci "
                                 "suprafețele cresc."))
            level = "info"
        target = diminuare_service.round_half_up(result.contour, decimals)
        if abs(result.total_new - target) > 1e-6:
            notes.append(self.tr("Parcelele cu CF/IE au mai multe zecimale decât rotunjirea "
                                 "aleasă, deci suma nouă este {0}, nu {1}.").format(
                fmt.area(result.total_new), fmt.area(target)))
            level = "info"
        negative = set(result.negative_rows)
        for indexes, one, many in (
                (result.left_out,
                 self.tr("{0} de total sau subtotal a fost lăsat deoparte: {1}."),
                 self.tr("{0} de total sau subtotal au fost lăsate deoparte: {1}.")),
                ([i for i in result.bad_rows if i not in negative],
                 self.tr("{0} fără o suprafață numerică a rămas neschimbat: {1}."),
                 self.tr("{0} fără o suprafață numerică au rămas neschimbate: {1}.")),
                (result.negative_rows,
                 self.tr("{0} cu suprafața negativă a rămas neschimbat: {1}."),
                 self.tr("{0} cu suprafața negativă au rămas neschimbate: {1}."))):
            if indexes:
                notes.append(fmt.plural(len(indexes), one, many).format(
                    fmt.count(len(indexes), self.tr("rând"), self.tr("rânduri")),
                    diminuare_service.row_list([self._numbers[i] for i in indexes])))
                level = "warning"
        self.status.set_status("\n".join(notes), level)
        factor = fmt.number(result.factor, 6, thousands=False)
        self.summary.setText(
            "<table cellspacing='0' cellpadding='2'><tr>"
            f"<td>A = <b>{fmt.area(result.total_deed)}</b></td><td width='16'></td>"
            f"<td>B = <b>{fmt.area(result.total_fixed)}</b></td><td width='16'></td>"
            f"<td>C = <b>{fmt.area(result.contour)}</b></td></tr><tr>"
            f"<td>{self.tr('Factor')} = <b>{factor}</b></td>"
            "<td></td><td colspan='3'>"
            + self.tr("Diminuare = {0} pentru fiecare parcelă fără CF/IE").format(
                f"<b>{fmt.percent(result.reduction_pct, 4)}</b>")
            + "</td></tr></table>")
        self.summary.setVisible(True)
        self._fill_table(result, areas, fixed_col)
        self.legend.setVisible(bool(result.fixed_rows or result.bad_rows or result.left_out))
        self.save_btn.setEnabled(True)

    def _fill_table(self, result, areas, fixed_col):
        fixed = set(result.fixed_rows)
        bad = set(result.bad_rows) | set(result.left_out)
        self.table.setRowCount(len(areas))
        for i, area in enumerate(areas):
            new = result.new_areas[i]
            cf_ie = (diminuare_service.cell_text(self._rows[i][fixed_col])
                     if fixed_col is not None and i in fixed else "")
            values = [str(self._numbers[i]), fmt.number(area) if area is not None else "?",
                      cf_ie, fmt.number(new) if new is not None else ""]
            tint = BAD_TINT if i in bad else FIXED_TINT if i in fixed else None
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c in (1, 3):
                    item.setTextAlignment(RIGHT)
                if tint:
                    item.setBackground(QBrush(QColor(tint)))
                    item.setForeground(QBrush(QColor(ROW_TEXT)))
                self.table.setItem(i, c, item)

    def _remember_choices(self):
        self.save_remembered()
        name = type(self).__name__
        dialog_memory.save(name, "area_column", self.area_combo.currentData() or "")
        dialog_memory.save(name, "fixed_column", self.fixed_combo.currentData() or "")
        # an empty CF/IE column is kept next time only when the user picked it
        dialog_memory.save(name, "fixed_column_chosen", user_chose(self.fixed_combo))

    def _save(self):
        if self._result is None:
            return
        result, area_col, fixed_col = self._result
        source = self._loaded_path
        start = os.path.splitext(source)[0] + "_diminuat.xlsx" if source else ""
        # the replace question for an existing file comes from confirm_overwrite, with Romanian
        # buttons
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Salvați rezultatul diminuării"), start,
            self.tr("Excel (*.xlsx)"), options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(source)):
            self.warn(self.tr("Alegeți alt nume decât al fișierului sursă, ca "
                              "datele inițiale să rămână nemodificate."))
            return
        if not self.confirm_overwrite([path]) or not self.release_files([path]):
            return
        source_name = os.path.basename(source)
        if not source.lower().endswith(".csv"):
            source_name += f", foaia {self.sheet_combo.currentText()}"
        meta = [
            ("Data", datetime.now().strftime("%d.%m.%Y %H:%M")),
            ("Fișier sursă", source_name),
            ("Coloana suprafeței din acte", self._header[area_col]),
            ("Coloana CF/IE", self._header[fixed_col] if fixed_col is not None else "-"),
            ("Contur", self._contour_origin or "introdus manual"),
            ("Rotunjire", self.round_combo.currentText()),
        ]
        if self._hidden:
            # a filtered table can hide thousands of rows, more than an Excel cell holds
            meta.append(("Rânduri ascunse în Excel, lăsate deoparte",
                         fmt.count(len(self._hidden), "rând", "rânduri") + ": "
                         + diminuare_service.row_list(self._hidden)))
        try:
            diminuare_service.write_excel(path, self._header, self._rows, result, meta,
                                          area_col, fixed_col)
        except OSError as error:
            self.fail(outputs.write_error(path, error))
            return
        except Exception as e:
            self.fail(self.tr("Fișierul nu s-a putut salva:\n\n{0}").format(e))
            return
        self._remember_choices()
        fixed, bad, left_out = (len(result.fixed_rows), len(result.bad_rows),
                                len(result.left_out))
        negative = len(result.negative_rows)
        counts = [(self.tr("Parcele diminuate"), len(self._rows) - fixed - bad - left_out,
                   None)]
        if fixed:
            counts.append((self.tr("Parcele cu CF/IE, neschimbate"), fixed, FIXED_TINT))
        if left_out:
            counts.append((self.tr("Rânduri de total, lăsate deoparte"), left_out, BAD_TINT))
        if bad - negative:
            counts.append((self.tr("Rânduri fără suprafață numerică"), bad - negative,
                           BAD_TINT))
        if negative:
            counts.append((self.tr("Rânduri cu suprafața negativă"), negative, BAD_TINT))
        self.show_result(self.tr("Rezultatul a fost salvat în {0}.").format(
            os.path.basename(path)), counts=counts,
            notes=[self.tr("În fișier, rândurile sunt colorate la fel ca aici.")],
            file_path=path)
