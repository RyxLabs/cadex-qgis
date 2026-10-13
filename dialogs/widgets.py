# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Widgets shared by the tool dialogs."""

import html
import os
import re
import threading

from qgis.PyQt.QtCore import QObject, QRectF, Qt, QTimer, pyqtSignal  # type: ignore
from qgis.PyQt.QtGui import QPainter, QPalette, QTextOption  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QButtonGroup, QCheckBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)
from qgis.core import Qgis, QgsIconUtils, QgsProject, QgsVectorLayer  # type: ignore

from . import _ui_helpers, theme
from ..i18n import tr as _tr
from ..services import cgxml_sources, fmt, layer_sources, roles, settings_service
from ..services.layer_sources import row_number_fields

DATA_ROLE = Qt.ItemDataRole.UserRole
WORD_JOINER = "\u2060"

# the roles of the layer lists; such layers are the tools' input data, never a contour or a
# sector outline
DATA_ROLES = ("roles/imobile", "roles/constructii", "roles/intravilan")

# how often the GUI thread looks for the result of a file system read on a worker thread,
# in milliseconds
POLL_MS = 50
# seconds to wait for the check of the path a file dialog opens at; past that it opens at its
# default place
PICKER_WAIT = 0.25


def _is_polygon_layer(layer) -> bool:
    return (isinstance(layer, QgsVectorLayer) and layer.isValid()
            and layer.geometryType() == Qgis.GeometryType.Polygon)


def role_layers(role_key, layers, patterns_of=None) -> list:
    """The polygon layers among layers that are preselected for the role, in their order:
    those matching the role's name patterns, CADex report layers left out. For a role of
    DATA_ROLES, of two conversions of the same imobile only the later one
    (layer_sources.current_conversions); for any other role, no layer that matches a role
    of DATA_ROLES. patterns_of(role key) gives the patterns, the saved ones by default."""
    if not role_key:
        return []
    patterns_of = patterns_of or settings_service.patterns

    def matches(layer, key):
        patterns = patterns_of(key)
        return bool(patterns) and roles.layer_matches(layer.name(), patterns)

    found = [layer for layer in layers if _is_polygon_layer(layer)
             and not roles.is_report(layer) and matches(layer, role_key)]
    if role_key in DATA_ROLES:
        return layer_sources.current_conversions(found)
    return [layer for layer in found if not any(matches(layer, key) for key in DATA_ROLES)]


def field_names(layers):
    """The field names of several layers without duplicates, in order of appearance."""
    names = []
    for layer in layers:
        for name in layer.fields().names():
            if name not in names:
                names.append(name)
    return names


def data_field_names(layers):
    """field_names without the columns that only number the rows, and with the first spelling
    only of names that differ in letter case, which the tools read as one column."""
    skip = {name for layer in layers for name in row_number_fields(layer)}
    names, seen = [], set()
    for name in field_names(layers):
        if name not in skip and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def converted_column(layers, key):
    """The field holding a CGXML column in every one of the layers when all come from
    Conversie CGXML and agree, else None."""
    names = {roles.column_of(layer, key) for layer in layers}
    return names.pop() if len(names) == 1 else None


def make_form(parent=None) -> QFormLayout:
    # macOS and KDE styles otherwise right-align the labels or wrap the rows
    form = QFormLayout(parent) if parent is not None else QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    return form


def add_field_row(form: QFormLayout, item):
    """A form row without a label, aligned with the fields. Unlike addRow("", item) it takes
    no space while the item is hidden."""
    row = form.rowCount()
    if isinstance(item, QWidget):
        form.setWidget(row, QFormLayout.ItemRole.FieldRole, item)
    else:
        form.setLayout(row, QFormLayout.ItemRole.FieldRole, item)


# --- checklists ---

class PlaceholderList(QListWidget):
    """A QListWidget that shows a text while it is empty."""

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self._placeholder = placeholder

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() or not self._placeholder:
            return
        painter = QPainter(self.viewport())
        # the text color in italic: the palette's placeholder color is too faint to read
        font = painter.font()
        font.setItalic(True)
        painter.setFont(font)
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        option = QTextOption(Qt.AlignmentFlag.AlignCenter)
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        painter.drawText(QRectF(self.viewport().rect().adjusted(8, 8, -8, -8)),
                         self._placeholder, option)
        painter.end()


class CheckList(QWidget):
    """A checkable list with the Toate / Niciunul buttons and a count of checked items."""

    changed = pyqtSignal()

    def __init__(self, placeholder="", height=110, parent=None):
        super().__init__(parent)
        # the list has a maximum height; any extra room would show up as gaps around it
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.list = PlaceholderList(placeholder)
        self.list.setMinimumHeight(height)
        self.list.setMaximumHeight(height + 40)
        self.list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list)
        self._count = QLabel("")
        row, _all, _none = _ui_helpers.make_select_row(
            lambda: self.set_all(True), lambda: self.set_all(False),
            extra_buttons=(self._count,))
        layout.addLayout(row)
        # {text: checked} of the items the user ticked or unticked; refills keep these
        self._chosen = {}

    def _sync(self):
        """Bring the items up to date before they are read; LayerChecklist rebuilds here."""

    def _on_item_changed(self, item=None):
        # refills block the signals, so a change seen here is the user's
        if item is not None:
            self._chosen[item.text()] = _ui_helpers.is_checked(item)
        self._update_count()
        self.changed.emit()

    def _update_count(self):
        total = self.list.count()
        self._count.setText(_tr("{0} din {1} bifate").format(len(self.checked_items()), total)
                            if total else "")

    def add_item(self, text, data=None, checked=True, tooltip="", icon=None):
        item = QListWidgetItem(text)
        if data is not None:
            item.setData(DATA_ROLE, data)
        if tooltip:
            item.setToolTip(tooltip)
        if icon is not None:
            item.setIcon(icon)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.list.addItem(item)
        return item

    def set_names(self, names, default_on=True):
        """Refill with names. A name the user ticked or unticked keeps that state. Every other
        name is checked when default_on is True, unchecked when it is False, or, when
        default_on is a set, checked if the name is in it."""
        self.list.blockSignals(True)
        self.list.clear()
        for name in names:
            on = default_on if isinstance(default_on, bool) else name in default_on
            self.add_item(name, checked=self._chosen.get(name, on))
        self.list.blockSignals(False)
        self._update_count()

    def set_items(self, items):
        """Refill with (text, data, checked, tooltip) entries."""
        self.list.blockSignals(True)
        self.list.clear()
        for text, data, checked, tooltip in items:
            self.add_item(text, data=data, checked=checked, tooltip=tooltip)
        self.list.blockSignals(False)
        self._update_count()

    def set_all(self, checked):
        """The Toate / Niciunul buttons: a choice of the user for every listed item."""
        self._sync()
        _ui_helpers.set_all_check_states(self.list, checked)
        self._chosen.update((text, checked) for text in self.texts())
        self._on_item_changed()

    def count(self):
        self._sync()
        return self.list.count()

    def texts(self):
        self._sync()
        return [self.list.item(i).text() for i in range(self.list.count())]

    def checked_items(self):
        self._sync()
        return [self.list.item(i) for i in range(self.list.count())
                if _ui_helpers.is_checked(self.list.item(i))]

    def checked_texts(self):
        return [item.text() for item in self.checked_items()]

    def checked_data(self):
        return [item.data(DATA_ROLE) for item in self.checked_items()]


class LayerChecklist(CheckList):
    """The project's polygon layers, kept in sync with the project: layers added, removed,
    renamed, or given other fields or another source.

    Until the user changes a check state, the layers role_layers() gives for the role are
    checked; after that, the user's choice is kept for the layers still in the project.

    The list is rebuilt once for a burst of project changes, on the next pass of the event
    loop, and not at all while it is hidden or while QGIS reads a project: a hidden list is
    rebuilt when it is shown. Reading the list rebuilds it first when a change is pending.
    """

    def __init__(self, role_key=None, parent=None, height=110):
        super().__init__(_tr("Proiectul nu conține niciun layer poligonal."), height, parent)
        self._role_key = role_key
        self._auto = True
        # project vector layers whose signals reach _on_project_changed, by id
        self._watched = {}
        self._dirty = False
        self._loading = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._on_timer)
        for signal, slot in self._project_signals():
            signal.connect(slot)
        self._connected = True
        self._rebuild()

    def _project_signals(self):
        project = QgsProject.instance()
        return ((project.layersAdded, self._on_project_changed),
                (project.layersRemoved, self._on_project_changed),
                (project.layerLoaded, self._on_layer_loaded),
                (project.readProject, self._on_loading_done),
                (project.cleared, self._on_loading_done))

    def _layer_signals(self, layer):
        # Processing adds its outputs to the project before it marks them as reports
        return ((layer.nameChanged, self._on_project_changed),
                (layer.updatedFields, self._on_project_changed),
                (layer.dataSourceChanged, self._on_project_changed),
                (layer.customPropertyChanged, self._on_property_changed))

    def disconnect_project(self):
        """Call before the dialog is destroyed."""
        if not self._connected:
            return
        try:
            self._timer.stop()
        except RuntimeError:
            pass
        for signal, slot in self._project_signals():
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        for layer in self._watched.values():
            try:
                for signal, slot in self._layer_signals(layer):
                    signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._watched = {}
        self._connected = False

    def _watch(self, layers):
        # a layer removed from the project is deleted, and its connections with it
        current = {layer.id(): layer for layer in layers}
        self._watched = {i: lyr for i, lyr in self._watched.items() if i in current}
        for layer_id, layer in current.items():
            if layer_id not in self._watched:
                for signal, slot in self._layer_signals(layer):
                    signal.connect(slot)
                self._watched[layer_id] = layer

    def _on_project_changed(self, *_args):
        self._dirty = True
        try:
            if not self._loading and self.isVisible():
                self._timer.start()
        except RuntimeError:
            self.disconnect_project()

    def _on_layer_loaded(self, done, total):
        # QGIS reads a project's layers one at a time and can process events in between
        self._loading = done < total
        if not self._loading:
            self._on_project_changed()

    def _on_loading_done(self, *_args):
        self._loading = False
        self._on_project_changed()

    def _on_property_changed(self, key):
        # QGIS keeps other custom properties on every layer, changed while it draws
        if str(key).startswith(roles.PROPERTY_PREFIX):
            self._on_project_changed()

    def _on_timer(self):
        if not self._loading and self.isVisible():
            self._sync()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync()

    def _sync(self):
        if not self._dirty:
            return
        try:
            self._rebuild()
        except RuntimeError:
            self.disconnect_project()

    def _on_item_changed(self, item=None):
        self._auto = False
        super()._on_item_changed(item)

    def _rebuild(self):
        self._dirty = False
        checked = set(self.checked_ids())
        vector = [layer for layer in QgsProject.instance().mapLayers().values()
                  if isinstance(layer, QgsVectorLayer)]
        if self._connected:
            # every vector layer: a broken one turns into a polygon layer when its source is
            # repaired
            self._watch(vector)
        layers = sorted((layer for layer in vector if _is_polygon_layer(layer)),
                        key=lambda lyr: roles.fold(lyr.name()))
        preselected = set()
        if self._auto:
            preselected = {layer.id() for layer in role_layers(self._role_key, layers)}
        self.list.blockSignals(True)
        self.list.clear()
        for layer in layers:
            if self._auto:
                tick = layer.id() in preselected
            else:
                tick = layer.id() in checked
            # publicSource() leaves out a password stored in a database connection
            self.add_item(layer.name(), data=layer.id(), checked=tick,
                          tooltip=f"{layer.name()}\n{layer.crs().authid()}\n"
                                  f"{layer.publicSource()}",
                          icon=QgsIconUtils.iconForLayer(layer))
        self.list.blockSignals(False)
        if not layers:
            # with no layers in the project, the list preselects by name again
            self._auto = True
        self._update_count()
        self.changed.emit()

    def checked_ids(self):
        return [item.data(DATA_ROLE) for item in self.checked_items()]

    def checked_layers(self):
        project = QgsProject.instance()
        return [layer for layer in (project.mapLayer(i) for i in self.checked_ids())
                if layer is not None]

    def set_checked_ids(self, ids):
        self._sync()
        wanted = set(ids or [])
        self._auto = False
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.data(DATA_ROLE) in wanted
                               else Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self._update_count()
        self.changed.emit()


# --- layer and field pickers ---

def preselect_layer(combo, role_key, iface=None):
    """Select in a QgsMapLayerComboBox the first of its layers that role_layers() gives for
    the role, else the active layer. True when a layer was selected."""
    listed = [layer for layer in (combo.layer(i) for i in range(combo.count()))
              if layer is not None]
    found = role_layers(role_key, listed)
    if found:
        combo.setLayer(found[0])
        return True
    active = iface.activeLayer() if iface else None
    if active is not None and any(layer.id() == active.id() for layer in listed):
        combo.setLayer(active)
        return True
    return False


def user_chose(combo) -> bool:
    """True when the combo box's entry was picked by the user or restored from the
    remembered values, rather than preselected by name."""
    return bool(combo.property(_ui_helpers.CHOSEN_PROPERTY))


def _note_choices(combo):
    # activated is emitted for the user's picks only, never for setCurrentIndex()
    if combo.property("cadexChoicesNoted"):
        return
    combo.setProperty("cadexChoicesNoted", True)
    combo.activated.connect(
        lambda _index, box=combo: box.setProperty(_ui_helpers.CHOSEN_PROPERTY, True))


def fill_field_combo(combo, names, role_key=None, none_text=None, keep=None):
    """Fill a combo box with field names as item data.

    none_text adds a first entry with empty data. keep stays selected while it is listed,
    and so does the empty entry once the user picked it or it was restored from the
    remembered values (user_chose). Otherwise the best match for the role's name patterns
    is selected, and an earlier choice of the user no longer counts.
    """
    _note_choices(combo)
    combo.blockSignals(True)
    combo.clear()
    if none_text is not None:
        combo.addItem(none_text, "")
    for name in names:
        combo.addItem(name, name)
    if keep is not None and (keep in names or (keep == "" and none_text is not None
                                               and user_chose(combo))):
        target = keep
    else:
        combo.setProperty(_ui_helpers.CHOSEN_PROPERTY, False)
        target = roles.pick_field(names, settings_service.patterns(role_key)) \
            if role_key else None
    if target:
        combo.setCurrentIndex(combo.findData(target))
    combo.blockSignals(False)


# --- paths ---

def _unquoted(text) -> str:
    """text without surrounding spaces and one pair of surrounding double quotes, which
    Windows' 'Copy as path' adds."""
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    return text


def _outside_quotes(text) -> bool:
    """True when text holds quoted paths and other text besides them, which
    cgxml_sources.split_paths() leaves out."""
    text = (text or "").strip()
    return '"' in text and bool(re.sub(r'"[^"]*"', "", text).strip())


class PathEdit(QLineEdit):
    """A path field that accepts files and folders dropped from the file manager.

    accept(path) filters the drops. With multiple, several paths are written quoted, and a
    drop keeps the paths accept() takes when there is at least one; ignored is the number
    of paths the last drop left out.
    """

    dropped = pyqtSignal(str)

    def __init__(self, accept=None, parent=None, multiple=False):
        super().__init__(parent)
        self._accept = accept or (lambda _path: True)
        self._multiple = multiple
        self._dragged = None
        self.ignored = 0

    def _dropped_paths(self, event):
        """(paths to write, number left out) for the dragged files, ([], 0) when the drop is
        refused."""
        urls = [url for url in event.mimeData().urls() if url.isLocalFile()]
        if not urls or (len(urls) > 1 and not self._multiple):
            return [], 0
        paths = [os.path.normpath(url.toLocalFile()) for url in urls]
        accepted = [path for path in paths if self._accept(path)]
        if not self._multiple and len(accepted) < len(paths):
            return [], 0
        return accepted, len(paths) - len(accepted)

    def dragEnterEvent(self, event):
        if not event.mimeData().hasUrls():
            super().dragEnterEvent(event)
            return
        # dragMoveEvent comes with every move of the mouse and reuses this answer, so the
        # file system is read once per drag
        self._dragged = bool(self._dropped_paths(event)[0])
        if self._dragged:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not event.mimeData().hasUrls():
            super().dragMoveEvent(event)
            return
        if self._dragged is None:
            self._dragged = bool(self._dropped_paths(event)[0])
        if self._dragged:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._dragged = None
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._dragged = None
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        paths, ignored = self._dropped_paths(event)
        if not paths:
            event.ignore()
            return
        self.ignored = ignored
        path = cgxml_sources.join_paths(paths)
        self.setText(path)
        event.acceptProposedAction()
        self.dropped.emit(path)

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        # Enter confirms the path; passed on, the dialog would press its run button
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()


def _filter_extensions(file_filter):
    """The extensions a file dialog filter allows, None when it allows every file."""
    if "(*)" in file_filter:
        return None
    return {"." + ext.lower() for ext in re.findall(r"\*\.(\w+)", file_filter)}


class PathRow(QWidget):
    """A path field with a browse button. mode is "open", "save" or "folder". picked is
    emitted when a path is browsed or dropped, not while typing."""

    picked = pyqtSignal(str)

    def __init__(self, mode, title, file_filter="", default_suffix="", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._title = title
        self._filter = file_filter
        self._suffix = default_suffix
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.edit = PathEdit(self._accepts)
        self.edit.dropped.connect(lambda _path: self.picked.emit(self.path()))
        layout.addWidget(self.edit)
        button = QToolButton()
        button.setText("...")
        button.setToolTip(title)
        button.clicked.connect(self._browse)
        layout.addWidget(button)

    def _accepts(self, path):
        if self._mode == "folder":
            return os.path.isdir(path)
        if os.path.isdir(path):
            return False
        extensions = _filter_extensions(self._filter)
        return extensions is None or os.path.splitext(path)[1].lower() in extensions

    def _browse(self):
        start = _unquoted(self.edit.text())
        if self._mode == "folder":
            path = QFileDialog.getExistingDirectory(self, self._title, start)
        elif self._mode == "open":
            path, _ = QFileDialog.getOpenFileName(self, self._title, start, self._filter)
        else:
            # BaseDialog.confirm_overwrite shows the question about an existing file when the
            # tool runs, with Romanian buttons and for a typed path too
            path, _ = QFileDialog.getSaveFileName(
                self, self._title, start, self._filter,
                options=QFileDialog.Option.DontConfirmOverwrite)
            if path and self._suffix and not path.lower().endswith(self._suffix):
                path += self._suffix
        if path:
            self.edit.setText(os.path.normpath(path))
            self.picked.emit(self.path())

    def path(self) -> str:
        text = _unquoted(self.edit.text())
        if text and self._mode == "save" and self._suffix \
                and not text.lower().endswith(self._suffix):
            text += self._suffix
        return text

    def set_path(self, path: str):
        self.edit.setText(path or "")


def _locate(paths):
    """(the paths that do not exist, the paths that are folders). Reads the file system, so
    it runs on a worker thread."""
    missing = [p for p in paths if not os.path.exists(p)]
    return missing, [p for p in paths if p not in missing and os.path.isdir(p)]


def _count_files(paths, recursive, located=None):
    """(number of .cgxml files or None, problem, paths concerned, the paths that are folders,
    details for the log, files found) for a source; problem is None, "missing", "zip",
    "in_use", "denied" or "unreadable". The number is None when part of the source is missing
    or cannot be read; files found counts the .cgxml files in the parts that can be read.
    located is _locate(paths) when the caller has it. Reads the file system, so it runs on a
    worker thread."""
    if not paths:
        return None, None, [], [], "", 0
    missing, folders = located if located is not None else _locate(paths)
    if missing:
        return None, "missing", missing, folders, "", 0
    try:
        sources = cgxml_sources.list_sources(paths, recursive)
        unread = [s for s in sources if isinstance(s, cgxml_sources.Unreadable)]
        found = len(sources) - len(unread)
        if unread:
            problems = {s.problem for s in unread}
            if problems == {cgxml_sources.DAMAGED_ARCHIVE}:
                kind = "zip"
            elif problems == {cgxml_sources.ARCHIVE_IN_USE}:
                kind = "in_use"
            elif cgxml_sources.ARCHIVE_IN_USE not in problems \
                    and all(isinstance(s.error, PermissionError) for s in unread):
                kind = "denied"
            else:
                kind = "unreadable"
            return (None, kind, [s[1] for s in unread], folders,
                    "; ".join(f"{s[1]}: {s.error!r}" for s in unread), found)
        return found, None, [], folders, "", found
    except PermissionError as error:
        return None, "denied", [error.filename] if error.filename else [], folders, "", 0
    except Exception as error:
        named = getattr(error, "filename", None)
        return (None, "unreadable", [named] if named else [], folders, f"{paths}: {error!r}",
                0)


class _Count:
    """One count of a source's .cgxml files. Listing a folder on a network drive can take
    seconds, and failing to reach an offline one tens of seconds, so run() is called on a
    worker thread; it uses no Qt object."""

    def __init__(self, text, recursive):
        self.text = text
        self.paths = cgxml_sources.split_paths(text)
        self.recursive = recursive
        # _locate() of the paths once checked is set, None if it failed; it is known before
        # the files are listed, which in a large folder takes much longer
        self.located = None
        self.checked = threading.Event()
        self.result = None
        self.applied = False
        self.done = threading.Event()

    def run(self):
        try:
            self.located = _locate(self.paths)
            self.checked.set()
            self.result = _count_files(self.paths, self.recursive, self.located)
        except Exception as error:
            self.result = (None, "unreadable", [], [], repr(error), 0)
        finally:
            self.checked.set()
            self.done.set()


class _Existing:
    """The paths of a list that exist, found by run() on a worker thread."""

    def __init__(self, paths):
        self.paths = list(paths)
        self.existing = []
        self.done = threading.Event()

    def run(self):
        try:
            self.existing = [p for p in self.paths if os.path.exists(p)]
        finally:
            self.done.set()


class ExistingFiles(QObject):
    """Finds which of some paths exist, on a worker thread, since each check on an
    unreachable network drive takes tens of seconds. found is emitted with the paths that
    exist, for the last check() only."""

    found = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._job = None
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._on_poll)

    def check(self, paths):
        """Look for paths. An empty list only drops the check in progress."""
        self._job = None
        self._poll.stop()
        if not paths:
            return
        job = _Existing(paths)
        self._job = job
        threading.Thread(target=job.run, name="CADex file check", daemon=True).start()
        self._poll.start()

    def _on_poll(self):
        job = self._job
        if job is not None and not job.done.is_set():
            return
        self._poll.stop()
        self._job = None
        if job is not None:
            self.found.emit(job.existing)


def _named(paths) -> str:
    # a folder typed with a trailing separator has an empty base name
    return ", ".join(f"'{os.path.basename(p.rstrip(os.sep + '/')) or p}'" for p in paths)


class CgxmlSource(QWidget):
    """A folder, ZIP archive or .cgxml files, with a count of the CGXML files found.

    The files are counted on a worker thread, and not while the field is hidden, for example
    on the page of another mode: refresh() then defers the count until the field is shown.
    changed is emitted when refresh() is called and again when the count is known.
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.edit = PathEdit(cgxml_sources.is_source, multiple=True)
        self.edit.setPlaceholderText(_tr("Folderul, arhiva ZIP sau fișierele .cgxml"))
        self.edit.setToolTip(_tr("Scrieți calea, alegeți-o cu butoanele alăturate sau "
                                 "trageți aici un folder, o arhivă ZIP ori fișiere .cgxml. "
                                 "Mai multe căi se scriu între ghilimele."))
        self.edit.returnPressed.connect(self.refresh)
        self.edit.editingFinished.connect(self._on_edited)
        self.edit.dropped.connect(self._on_dropped)
        row.addWidget(self.edit, 1)
        for text, tooltip, icon, slot in (
                (_tr("Folder..."), _tr("Alegeți folderul cu fișiere CGXML"),
                 "/mIconFolderOpen.svg", self._browse_folder),
                (_tr("ZIP..."), _tr("Alegeți arhiva ZIP cu fișiere CGXML"),
                 "/mIconZip.svg", self._browse_zip),
                (_tr("Fișiere..."), _tr("Alegeți unul sau mai multe fișiere .cgxml"),
                 "/mIconFile.svg", self._browse_files)):
            button = _ui_helpers.small_button(text, tooltip, _ui_helpers.theme_icon(icon))
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)

        line = QHBoxLayout()
        self.recursive = QCheckBox(_tr("Include și subfolderele"))
        self.recursive.setChecked(True)
        self.recursive.toggled.connect(lambda _on: self.refresh())
        line.addWidget(self.recursive)
        line.addSpacing(12)
        self.status = StatusLabel()
        line.addWidget(self.status, 1)
        layout.addLayout(line)
        self._count = None
        self._found = 0
        # the count in progress or the last one shown
        self._job = None
        # refresh() was called while the field was hidden
        self._waiting = False
        # the status text about the files the last drop left out
        self._note = ""
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._check_count)

    def _start(self, suffix=""):
        """Where a picker opens: the first path of the field when it is a folder or ends in
        suffix, else its folder, so a picker never shows a name it cannot pick. A file dialog
        reads the path it opens at, and an unreachable network path takes tens of seconds to
        fail, so whether the path exists and is a folder comes from the count of the field's
        text. A path the count did not find, or did not check within PICKER_WAIT seconds,
        opens the picker at its default place."""
        # the buttons take no focus, so a text typed without Return has not been counted
        self._on_edited()
        job = self._job
        if job is None or not job.paths or not job.checked.wait(PICKER_WAIT) or not job.located:
            return ""
        missing, folders = job.located
        first = job.paths[0]
        if first in missing:
            return ""
        if first in folders or (suffix and first.lower().endswith(suffix)):
            return first
        return os.path.dirname(first)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, _tr("Alegeți folderul cu fișiere CGXML"), self._start())
        if folder:
            self.set_paths([os.path.normpath(folder)])
            self.refresh()

    def _browse_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, _tr("Alegeți arhiva ZIP cu fișiere CGXML"), self._start(".zip"),
            _tr("Arhive ZIP (*.zip);;Toate fișierele (*)"))
        if path:
            self.set_paths([os.path.normpath(path)])
            self.refresh()

    def _browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, _tr("Alegeți fișierele CGXML"), self._start(".cgxml"),
            _tr("Fișiere CGXML (*.cgxml);;Arhive ZIP (*.zip);;Toate fișierele (*)"))
        if paths:
            self.set_paths([os.path.normpath(p) for p in paths])
            self.refresh()

    def paths(self) -> list:
        return cgxml_sources.split_paths(self.edit.text())

    def set_paths(self, paths):
        self.edit.setText(cgxml_sources.join_paths(paths))

    def count(self):
        """The .cgxml files found, None for a missing or unreadable source. Waits for a count
        in progress, and counts at once when the field was hidden at the last refresh()."""
        self._wait()
        return self._count

    def found(self):
        """The .cgxml files found in the parts of the source that can be read, 0 for a
        missing source. Waits like count()."""
        self._wait()
        return self._found

    def _wait(self):
        if self._waiting:
            self._start_count()
        job = self._job
        if job is not None and not job.applied:
            job.done.wait()
            self._apply(job)

    def missing(self):
        """The paths in the field that the last count did not find; None while the files are
        being counted, before the first count, or when the text has changed since. Unlike
        count(), it never waits."""
        job = self._job
        if job is None or not job.applied or job.text != self.edit.text():
            return None
        _count, problem, names, _folders, _details, _found = job.result
        return list(names) if problem == "missing" else []

    def refresh(self):
        """Count the files again."""
        self._note = ""
        self._refresh()

    def _refresh(self):
        if self.isVisible():
            self._start_count()
        else:
            self._waiting = True
            self._job = None
            self._count = None
            self._found = 0
        self.changed.emit()

    def _on_edited(self):
        # Qt 5 also reports leaving the field as an edit, so an unchanged field is not
        # counted again; Return always counts, through returnPressed
        job = self._job
        if job is None or (job.text, job.recursive) != (self.edit.text(),
                                                        self.recursive.isChecked()):
            self.refresh()

    def _on_dropped(self, _path):
        ignored = self.edit.ignored
        self._note = fmt.plural(
            ignored, _tr("{0} care nu este .cgxml sau .zip a fost ignorat."),
            _tr("{0} care nu sunt .cgxml sau .zip au fost ignorate.")).format(
                fmt.count(ignored, _tr("fișier"), _tr("fișiere"))) if ignored else ""
        self._refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self._waiting:
            self._start_count()

    def _start_count(self):
        self._waiting = False
        job = _Count(self.edit.text(), self.recursive.isChecked())
        self._job = job
        self._count = None
        self._found = 0
        if not job.paths:
            job.run()
            self._apply(job)
            return
        self.status.set_status(_tr("Se caută fișierele..."), "info")
        threading.Thread(target=job.run, name="CADex CGXML count", daemon=True).start()
        self._poll.start()

    def _check_count(self):
        job = self._job
        if job is None or job.applied:
            self._poll.stop()
        elif job.done.is_set():
            self._poll.stop()
            self._apply(job)

    def _apply(self, job):
        job.applied = True
        count, problem, names, folders, details, found = job.result
        self._count = count
        self._found = found
        # archives and single files have no subfolders
        self.recursive.setEnabled(not job.paths or bool(folders))
        if details:
            _ui_helpers.log_message(details, Qgis.MessageLevel.Warning)
        self.status.set_status(*self._status_of(job, count, problem, names))
        self.changed.emit()

    def _status_of(self, job, count, problem, names):
        """(text, level) of the status line for a finished count."""
        if not job.paths:
            return "", None
        if problem == "missing":
            if len(job.paths) == 1:
                return _tr("Calea aleasă nu există."), "warning"
            return fmt.plural(len(names), _tr("Calea {0} nu există."),
                              _tr("Căile {0} nu există.")).format(_named(names)), "warning"
        if problem == "zip":
            return fmt.plural(
                len(names), _tr("Arhiva ZIP {0} este deteriorată sau incompletă."),
                _tr("Arhivele ZIP {0} sunt deteriorate sau incomplete.")).format(
                    _named(names)), "error"
        if problem == "in_use":
            return fmt.plural(
                len(names), _tr("Arhiva ZIP {0} nu poate fi deschisă: este folosită de alt "
                                "program sau nu aveți drept de acces la ea."),
                _tr("Arhivele ZIP {0} nu pot fi deschise: sunt folosite de alt program sau nu "
                    "aveți drept de acces la ele.")).format(_named(names)), "error"
        if problem == "denied":
            if names:
                return _tr("Sursa {0} nu poate fi citită: acces refuzat.").format(
                    _named(names)), "error"
            return _tr("Sursa nu poate fi citită: acces refuzat."), "error"
        if problem:
            if names:
                return _tr("Sursa {0} nu poate fi citită.").format(_named(names)), "error"
            return _tr("Sursa nu poate fi citită."), "error"
        if count == 0:
            text, level = _tr("Nu s-a găsit niciun fișier .cgxml."), "warning"
        else:
            text, level = fmt.plural(count, _tr("S-a găsit {0}."), _tr("S-au găsit {0}.")).format(
                fmt.count(count, _tr("fișier CGXML"), _tr("fișiere CGXML"))), "ok"
        if _outside_quotes(job.text):
            text += " " + _tr("Textul din afara ghilimelelor nu se folosește: scrieți fiecare "
                              "cale între ghilimele.")
            level = "warning"
        if self._note:
            text += " " + self._note
        return text, level


class PageStack(QWidget):
    """Pages shown one at a time, as tall as the current page.

    QStackedWidget sizes itself to its tallest page, which leaves a gap under a short one.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._pages = []
        self._current = None

    def addWidget(self, page):
        self._pages.append(page)
        self._layout.addWidget(page)
        if self._current is None:
            self._current = page
        else:
            page.hide()

    def setCurrentWidget(self, page):
        self._current = page
        for other in self._pages:
            other.setVisible(other is page)

    def currentWidget(self):
        return self._current


class SegmentedControl(QWidget):
    """A row of joined, mutually exclusive buttons for switching a tool's input mode."""

    changed = pyqtSignal(int)

    def __init__(self, labels, tooltips=(), parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons = []
        for i, label in enumerate(labels):
            button = QToolButton()
            button.setObjectName("cadexSegment")
            button.setText(label)
            button.setCheckable(True)
            button.setAutoRaise(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            position = "first" if i == 0 else "last" if i == len(labels) - 1 else "middle"
            button.setProperty("position", position)
            if i < len(tooltips) and tooltips[i]:
                button.setToolTip(tooltips[i])
            self._group.addButton(button, i)
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch()
        self.buttons[0].setChecked(True)
        self._group.idToggled.connect(self._on_toggled)

    def _on_toggled(self, index, checked):
        if checked:
            self.changed.emit(index)

    def index(self) -> int:
        return self._group.checkedId()

    def set_index(self, index):
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)

    # used by BaseDialog.remember()
    def remembered_value(self):
        return self.index()

    def restore_value(self, value):
        self.set_index(int(float(value)))


# --- labels ---

class StatusLabel(QWidget):
    """A one-line status with a theme icon for its level. Hidden while the text is empty."""

    ICONS = {"ok": "/mIconSuccess.svg", "info": "/mIconInfo.svg",
             "warning": "/mIconWarning.svg", "error": "/mIconCritical.svg"}

    def __init__(self, text="", level=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        # fixed icon slot, so the text stays aligned with and without an icon
        self._icon = QLabel()
        self._icon.setFixedSize(16, 16)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._label = QLabel()
        self._label.setWordWrap(True)
        layout.addWidget(self._label, 1)
        self.set_status(text, level)

    def set_status(self, text, level=None):
        self._label.setText(text)
        icon = self.ICONS.get(level)
        if icon:
            self._icon.setPixmap(_ui_helpers.theme_icon(icon).pixmap(16, 16))
        else:
            self._icon.clear()
        self.setVisible(bool(text))

    def text(self):
        return self._label.text()


def link_text_html(text: str, url: str, link_text: str) -> str:
    # word joiners keep the link text on one line; Qt rich text ignores <nobr> in wrapping labels
    shown = html.escape(link_text).replace("/", "/" + WORD_JOINER)
    return theme.colored_links(f"{html.escape(text)} <a href='{url}'>{shown}</a>")


def link_label(text: str, url: str, link_text: str) -> QWidget:
    """A framed note with the CADex mark, a sentence and a link."""
    card = QFrame()
    card.setObjectName("cadexCard")
    layout = QHBoxLayout(card)
    layout.setContentsMargins(10, 7, 10, 7)
    layout.setSpacing(8)
    icon = QLabel()
    icon.setPixmap(_ui_helpers.plugin_icon("cadex.svg").pixmap(18, 18))
    layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
    label = QLabel(link_text_html(text, url, link_text))
    label.setOpenExternalLinks(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    label.setWordWrap(True)
    layout.addWidget(label, 1)
    return card


def hint_label(text: str) -> QLabel:
    # normal text color: the palette's muted grey is about 2.5:1 on a light theme
    label = QLabel(text)
    label.setWordWrap(True)
    return label
