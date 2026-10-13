# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""UI helpers shared by the dialogs: icons, messages, result window, remembered values."""

import html
from typing import Optional

from qgis.PyQt.QtCore import QSize, Qt, QUrl  # type: ignore
from qgis.PyQt.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLineEdit, QMessageBox,
    QRadioButton, QSpinBox, QTabWidget, QToolButton, QTreeWidget, QWidget,
)
from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsProject  # type: ignore

from .. import icons
from ..i18n import tr as _tr
from ..services import fmt, roles

PLUGIN_NAME = "CADex"

# set on a combo box whose entry the user picked or that was restored from the remembered
# values; widgets.fill_field_combo keeps that entry when it fills the box again
CHOSEN_PROPERTY = "cadexChosen"


def log_message(message: str, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(message, PLUGIN_NAME, level=level)


# --- icons ---

dark_ui = icons.dark_ui
plugin_icon = icons.icon


def theme_icon(name: str) -> QIcon:
    """An icon of the running QGIS theme, e.g. "/mIconWarning.svg"."""
    return QgsApplication.getThemeIcon(name)


def swatch_icon(color: str, size: int = 12) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # the darker outline keeps pale tints visible on a light background
    painter.setPen(QColor(color).darker(170))
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(0, 0, size - 1, size - 1, 2, 2)
    painter.end()
    return QIcon(pixmap)


# --- messages ---

def show_error(parent: QWidget, title: str, message: str, *, log: bool = True):
    QMessageBox.critical(parent, title, message)
    if log:
        log_message(message, Qgis.MessageLevel.Critical)


def show_warning(parent: QWidget, title: str, message: str, *, log: bool = True):
    QMessageBox.warning(parent, title, message)
    if log:
        log_message(message, Qgis.MessageLevel.Warning)


def ask(parent: QWidget, title: str, message: str, yes_text: str, no_text: str,
        default_yes: bool = False) -> bool:
    """A question with buttons named after the action. The standard Yes and No would come in
    the language of QGIS, not of the message. Closing the window counts as no."""
    box = QMessageBox(QMessageBox.Icon.Question, title, message,
                      QMessageBox.StandardButton.NoButton, parent)
    yes = box.addButton(yes_text, QMessageBox.ButtonRole.YesRole)
    no = box.addButton(no_text, QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(yes if default_yes else no)
    box.setEscapeButton(no)
    try:
        box.exec()
        return box.clickedButton() is yes
    finally:
        # a tool window is hidden on close and kept for the session, so its questions are
        # deleted here rather than with it
        box.deleteLater()


def show_success(message: str, *, iface=None):
    """Success bar on the map window."""
    if iface:
        iface.messageBar().pushMessage(_tr("Succes"), message,
                                       level=Qgis.MessageLevel.Success, duration=5)


def open_path(path: str):
    """Open a file or folder with the system's default application."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def open_url(url: str):
    QDesktopServices.openUrl(QUrl(url))


def result_html(headline: str, counts=(), notes=()) -> str:
    """The end-of-run text as rich text, everything escaped.

    counts holds (label, value, color) rows, color being the category's map color or None;
    a None entry leaves a gap between two groups of rows, and a label's leading spaces indent
    it under the row above. notes are plain paragraphs.
    """
    parts = [f"<p><b>{html.escape(headline)}</b></p>"] if headline else []
    rows = []
    for entry in counts:
        if entry is None:
            rows.append("<tr><td colspan='3' style='font-size:4pt'>&nbsp;</td></tr>")
            continue
        label, value, color = entry
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = fmt.number(value, 0)
        swatch = (f"<td bgcolor='{html.escape(color)}' width='12'>&nbsp;</td>" if color
                  else "<td width='12'></td>")
        indent = "&nbsp;" * 2 * (len(label) - len(label.lstrip(" ")))
        shown = html.escape(str(value))
        rows.append(f"<tr>{swatch}<td>&nbsp;{indent}{html.escape(label.lstrip(' '))}</td>"
                    f"<td align='right'>&nbsp;&nbsp;&nbsp;<b>{shown}</b></td></tr>")
    if rows:
        parts.append("<table cellspacing='2' cellpadding='1'>" + "".join(rows) + "</table>")
    for note in notes:
        parts.append("<p>" + html.escape(note).replace("\n", "<br>") + "</p>")
    return "".join(parts)


def result_box(parent: QWidget, title: str, headline: str, *, counts=(), notes=(),
               file_path: Optional[str] = None, details: Optional[str] = None,
               warning: bool = False, layers=(), iface=None):
    """The end-of-run window, not shown yet.

    file_path is a file or folder to open, details a long text shown on request, layers the
    result layers the zoom button frames. counts and notes as in result_html().
    """
    from .result_dialog import ResultDialog
    return ResultDialog(parent, title, headline, counts=counts, notes=notes,
                        file_path=file_path, details=details, warning=warning,
                        layers=layers, iface=iface)


def show_result(parent: QWidget, title: str, headline: str, **kwargs):
    dialog = result_box(parent, title, headline, **kwargs)
    try:
        dialog.exec()
    finally:
        dialog.deleteLater()


def add_layers(layers, group_title=None, report=True):
    """Add a run's result layers to the project, two or more in a new group at the top.
    report=False for layers that are input data for the other tools, such as converted CGXML."""
    project = QgsProject.instance()
    layers = [layer for layer in layers if layer is not None]
    if report:
        for layer in layers:
            roles.mark_report(layer)
    if len(layers) < 2 or not group_title:
        for layer in layers:
            project.addMapLayer(layer)
        return layers
    root = project.layerTreeRoot()
    # a rerun gets its own group, numbered, so its layers are not taken for the earlier ones
    title, n = group_title, 2
    while root.findGroup(title) is not None:
        title = f"{group_title} ({n})"
        n += 1
    group = root.insertGroup(0, title)
    for layer in layers:
        project.addMapLayer(layer, False)
        group.addLayer(layer)
    return layers


def add_features(layer, features):
    """Add features to a memory layer: (how many it refused, the provider's error)."""
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    missing = len(features) - layer.featureCount()
    return max(missing, 0), (layer.dataProvider().lastError() if missing > 0 else "")


# --- selection rows ---

def small_button(text: str, tooltip: str = "", icon: Optional[QIcon] = None) -> QToolButton:
    button = QToolButton()
    button.setText(text)
    if icon is not None:
        button.setIcon(icon)
        button.setIconSize(QSize(16, 16))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if not text
                                  else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def make_select_row(on_all, on_none, extra_buttons=()):
    """The Toate / Niciunul row under a checkable list, as (layout, all_btn, none_btn)."""
    layout = QHBoxLayout()
    layout.setSpacing(4)
    all_btn = small_button(_tr("Toate"), _tr("Bifează toate"))
    none_btn = small_button(_tr("Niciunul"), _tr("Debifează toate"))
    # the lambdas discard the checked argument that clicked passes
    all_btn.clicked.connect(lambda: on_all())
    none_btn.clicked.connect(lambda: on_none())
    layout.addWidget(all_btn)
    layout.addWidget(none_btn)
    layout.addStretch()
    for button in extra_buttons:
        layout.addWidget(button)
    return layout, all_btn, none_btn


def set_all_check_states(widget, checked: bool, column: int = 0):
    """Check or uncheck every top-level item of a QTreeWidget or QListWidget, signals blocked."""
    state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    widget.blockSignals(True)
    try:
        if isinstance(widget, QTreeWidget):
            for i in range(widget.topLevelItemCount()):
                widget.topLevelItem(i).setCheckState(column, state)
        else:
            for i in range(widget.count()):
                widget.item(i).setCheckState(state)
    finally:
        widget.blockSignals(False)


def is_checked(item, column=None) -> bool:
    # Qt5 returns the state as an int, Qt6 as an enum; comparing to the enum works on both
    state = item.checkState(column) if column is not None else item.checkState()
    return state == Qt.CheckState.Checked


# --- remembered widget values ---

def _as_bool(value) -> bool:
    # QSettings returns booleans as strings on some platforms
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def read_widget_state(widget, by_text: bool = False):
    """A storable value for the widget, None for a widget type not handled here.

    Custom widgets provide remembered_value() and restore_value().
    """
    if hasattr(widget, "remembered_value"):
        return widget.remembered_value()
    if isinstance(widget, (QCheckBox, QRadioButton, QGroupBox)):
        return bool(widget.isChecked())
    if isinstance(widget, QSpinBox):
        return int(widget.value())
    if isinstance(widget, QDoubleSpinBox):
        return float(widget.value())
    if isinstance(widget, QComboBox):
        if by_text:
            return widget.currentText()
        data = widget.currentData()
        return data if isinstance(data, str) else int(widget.currentIndex())
    if isinstance(widget, QLineEdit):
        return widget.text()
    if isinstance(widget, QTabWidget):
        return int(widget.currentIndex())
    return None


def apply_widget_state(widget, value, by_text: bool = False):
    """Restore a stored value. A value the widget cannot take is ignored."""
    if hasattr(widget, "restore_value"):
        widget.restore_value(value)
    elif isinstance(widget, (QCheckBox, QRadioButton, QGroupBox)):
        widget.setChecked(_as_bool(value))
    elif isinstance(widget, QSpinBox):
        widget.setValue(int(float(value)))
    elif isinstance(widget, QDoubleSpinBox):
        widget.setValue(float(value))
    elif isinstance(widget, QComboBox):
        if by_text:
            index = widget.findText(str(value))
        else:
            index = widget.findData(value) if isinstance(value, str) else -1
            if index < 0:
                try:
                    index = int(float(value))
                except (TypeError, ValueError):
                    index = -1
        if 0 <= index < widget.count():
            widget.setCurrentIndex(index)
            widget.setProperty(CHOSEN_PROPERTY, True)
    elif isinstance(widget, QLineEdit):
        widget.setText(str(value))
    elif isinstance(widget, QTabWidget):
        index = int(float(value))
        if 0 <= index < widget.count():
            widget.setCurrentIndex(index)
