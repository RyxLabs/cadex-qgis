# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Base class of the tool windows: frame, messages, input checks, background tasks and
remembered values."""

import html
import json
import os
import time
import traceback
from typing import Optional

from qgis.PyQt.QtCore import (  # type: ignore
    QEvent, QObject, QSettings, QSize, Qt, QTimer, pyqtSignal,
)
from qgis.PyQt.QtWidgets import (  # type: ignore
    QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QSizePolicy, QStackedWidget, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)
from qgis.core import Qgis, QgsApplication, QgsProject, QgsVectorLayer  # type: ignore
from qgis.gui import (  # type: ignore
    QgsCollapsibleGroupBox, QgsMessageBar, QgsMessageBarItem, QgsScrollArea,
)

from . import _ui_helpers, theme
from .widgets import (
    POLL_MS, CgxmlSource, CheckList, LayerChecklist, PathEdit, PathRow, forget_field_choices,
    link_label,
)
from .. import features
from ..i18n import tr as _tr
from ..services import dialog_memory, fmt, layer_sources, outputs, stereo70

# seconds a warning stays in the dialog's message bar
WARNING_DURATION = 10
# the most of the screen height a dialog takes before its body scrolls
SCREEN_SHARE = 0.9
MARGIN = 16
# the widgets a label can name and give the keyboard focus to; not a button, which the
# mnemonic of its label would press
INPUTS = (QLineEdit, QComboBox, QAbstractSpinBox, QAbstractItemView)


def _feature(method):
    for feat in features.CATALOG:
        if feat.method == method:
            return feat
    return None


def elapsed_text(seconds: float) -> str:
    """"0:07", "2:15", "1:02:03"."""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def hairline() -> QFrame:
    line = QFrame()
    line.setObjectName("cadexHairline")
    return line


def _plain(text) -> str:
    """A label's text as a name: without its mnemonic mark and its closing colon."""
    return (text or "").replace("&", "").strip().rstrip(":").strip()


def _first_input(item):
    """The first widget that takes input in a layout item, None when it holds none."""
    widget = item.widget()
    if widget is not None:
        if isinstance(widget, (PathRow, CgxmlSource)):
            return widget.edit
        if isinstance(widget, INPUTS):
            return widget
        return next(iter(widget.findChildren(INPUTS)), None)
    layout = item.layout()
    for i in range(layout.count() if layout is not None else 0):
        found = _first_input(layout.itemAt(i))
        if found is not None:
            return found
    return None


def _with_mnemonic(text, used) -> str:
    """text with '&' before its first letter that no other mnemonic of the window uses, a
    first letter of a word when one is free; text itself when every letter is taken. Only
    a to z: the keys of ă, î, ș and ț differ between keyboard layouts. used is updated."""
    def free(i):
        return text[i].isascii() and text[i].isalpha() and text[i].lower() not in used
    starts = [i for i in range(len(text)) if i == 0 or not text[i - 1].isalnum()]
    for i in starts + list(range(len(text))):
        if free(i):
            used.add(text[i].lower())
            return text[:i] + "&" + text[i:]
    return text


def screen_of(window):
    screen = window.screen() if hasattr(window, "screen") else None
    return screen if screen is not None else QApplication.primaryScreen()


def keep_on_screen(window):
    """Shrink the window to the screen's free area when it is larger, then move it up or left
    until its frame is inside that area."""
    try:
        screen = screen_of(window)
        if screen is None:
            return
        free = screen.availableGeometry()
        frame = window.frameGeometry()
        # the title bar and borders lie outside the window's own size
        extra_w, extra_h = frame.width() - window.width(), frame.height() - window.height()
        if frame.width() > free.width() or frame.height() > free.height():
            window.resize(min(window.width(), free.width() - extra_w),
                          min(window.height(), free.height() - extra_h))
            frame = window.frameGeometry()
        x = max(free.left(), min(frame.left(), free.right() - frame.width() + 1))
        y = max(free.top(), min(frame.top(), free.bottom() - frame.height() + 1))
        if (x, y) != (frame.left(), frame.top()):
            window.move(x, y)
    except RuntimeError:
        pass  # closed and destroyed before the timer fired


def fit_to_screen(window, scroll=None):
    """Resize the window to its content's height at the current width, at most SCREEN_SHARE
    of the screen, the rest scrolling in scroll, and keep it on the screen. Call it on the
    event loop pass after a show: Qt places the window before word-wrapped text is measured."""
    try:
        if scroll is not None:
            scroll.updateGeometry()
        width = window.width()
        # word-wrapped labels make the height depend on the width
        wanted = window.heightForWidth(width) if window.hasHeightForWidth() else -1
        if wanted <= 0:
            wanted = window.sizeHint().height()
        wanted = max(wanted, window.minimumSizeHint().height())
        screen = screen_of(window)
        available = screen.availableGeometry().height() if screen is not None else 10000
        window.resize(width, min(wanted, int(available * SCREEN_SHARE)))
        keep_on_screen(window)
    except RuntimeError:
        pass  # closed and destroyed before the timer fired


class BodyScroll(QgsScrollArea):
    """Scrolling dialog body whose size hint is the full height of its content.

    QScrollArea's own size hint stops at about 24 lines. QgsScrollArea keeps a combo box under
    the mouse wheel from changing while the page scrolls.
    """

    def sizeHint(self):
        widget = self.widget()
        if widget is None:
            return super().sizeHint()
        hint = widget.sizeHint()
        width = self.viewport().width() if self.isVisible() else hint.width()
        # word-wrapped labels make the height depend on the width
        height = widget.heightForWidth(width) if widget.hasHeightForWidth() else -1
        if height <= 0:
            height = hint.height()
        frame = 2 * self.frameWidth()
        return QSize(hint.width() + frame, height + frame)


class WheelGuard(QObject):
    """Passes the mouse wheel over a combo box or spin box without the keyboard focus on to
    the scrolling body, so scrolling the window changes no value under the pointer. A click
    in the box gives it the focus, and the wheel works on it again."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            # an ignored wheel event goes on to the parent widgets
            event.ignore()
            return True
        return False

    def guard(self, root):
        for kind in (QComboBox, QAbstractSpinBox):
            for widget in root.findChildren(kind):
                if widget.property("cadexWheelGuard"):
                    continue
                # with the default WheelFocus the wheel itself would give the box the focus
                widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                widget.installEventFilter(self)
                widget.setProperty("cadexWheelGuard", True)


class BaseDialog(QDialog):
    # catalog method of the tool: window title, icon and header come from its entry
    FEATURE = None
    # the height follows the content; False keeps the height the user gave the window
    FIT_HEIGHT = True
    HELP_BUTTON = True
    # the body scrolls when the screen is too short for it
    SCROLL = True

    # the plugin opens the help window at this tool; without a listener the dialog opens its own
    help_requested = pyqtSignal(str)

    def tr(self, text: str) -> str:
        # QObject.tr would use the class name as the translation context
        return _tr(text)

    def __init__(self, iface=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        # QgsCollapsibleGroupBox keys its saved state on the window's object name
        self.setObjectName(type(self).__name__)
        self.tokens = theme.tokens()
        self.setStyleSheet(theme.dialog_stylesheet(self.tokens))
        self._geometry_key = f"CADex/{type(self).__name__}/geometry"
        self._processing = False
        self._remembered = []
        self._task = None
        # the project was closed or replaced while the task ran: its results stay out of the
        # project open when it ends
        self._project_replaced = False
        # while the finished callback of such a run is called: the layers it passed to
        # _ui_helpers.add_layers(), one list per call; None for a run in its own project
        self._withheld = None
        # a run that waits for the files of its CGXML sources to be counted: (the sources,
        # the call that runs it); None when no run waits
        self._awaited = None
        # while _check_counts() calls the run again: the sources just counted, which
        # _sources_counted() must not count again
        self._just_counted = ()
        self._run_message = ""
        self._started = 0.0
        self._run_btn = None
        self._close_btn = None
        self._help_owned = None
        self._header_icon = None
        self._labelled = False

        feature = _feature(self.FEATURE) if self.FEATURE else None
        self._feature = feature
        if feature is not None:
            self.setWindowTitle(self.tr(feature.label).rstrip("."))
            self.setWindowIcon(_ui_helpers.plugin_icon(feature.icon))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("cadexBrandBar")
        outer.addWidget(bar)
        if feature is not None:
            outer.addWidget(self._make_header(feature))
            outer.addWidget(hairline())
        self.message_bar = QgsMessageBar()
        bar_box = QWidget()
        bar_layout = QVBoxLayout(bar_box)
        bar_layout.setContentsMargins(MARGIN, 6, MARGIN, 0)
        bar_layout.addWidget(self.message_bar)
        outer.addWidget(bar_box)

        self._body_widget = QWidget()
        self.body = QVBoxLayout(self._body_widget)
        self.body.setContentsMargins(MARGIN, 4, MARGIN, 8)
        self.body.setSpacing(8)
        self._scroll = None
        self._wheel_guard = None
        if self.SCROLL:
            self._wheel_guard = WheelGuard(self)
            self._scroll = BodyScroll()
            self._scroll.setFrameShape(QFrame.Shape.NoFrame)
            self._scroll.setWidgetResizable(True)
            self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._scroll.setWidget(self._body_widget)
            outer.addWidget(self._scroll, 1)
        else:
            outer.addWidget(self._body_widget, 1)

        footer = QWidget()
        self._footer = QVBoxLayout(footer)
        self._footer.setContentsMargins(MARGIN, 4, MARGIN, 14)
        self._footer.setSpacing(10)
        self._footer.addWidget(self._progress_row())
        self.button_box = QDialogButtonBox()
        self._footer.addWidget(self.button_box)
        outer.addWidget(footer)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._show_progress_text)
        self._count_timer = QTimer(self)
        self._count_timer.setInterval(POLL_MS)
        self._count_timer.timeout.connect(self._check_counts)

        # connected before this window's own slots, so dialog_memory drops the values of the
        # project just closed before the window restores its own for the new project
        dialog_memory.watch_project()
        for signal in self._project_signals():
            signal.connect(self._on_project_replaced)

    # --- frame ---

    def _make_header(self, feature):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(MARGIN, 12, 10, 10)
        row.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(_ui_helpers.icon_pixmap(_ui_helpers.plugin_icon(feature.icon), 36))
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        self._header_icon = icon
        texts = QVBoxLayout()
        texts.setSpacing(3)
        title = QLabel(self.tr(feature.label).rstrip("."))
        title.setObjectName("cadexTitle")
        font = title.font()
        font.setBold(True)
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() + 3)
        title.setFont(font)
        texts.addWidget(title)
        hint = QLabel(self.tr(feature.hint) + ".")
        hint.setWordWrap(True)
        texts.addWidget(hint)
        row.addLayout(texts, 1)
        if self.HELP_BUTTON:
            help_btn = QToolButton()
            help_btn.setIcon(_ui_helpers.theme_icon("/mActionHelpContents.svg"))
            help_btn.setIconSize(QSize(20, 20))
            help_btn.setAutoRaise(True)
            help_btn.setToolTip(self.tr("Cum se folosește acest instrument"))
            help_btn.clicked.connect(self._on_help)
            row.addWidget(help_btn, 0, Qt.AlignmentFlag.AlignTop)
        return box

    def _on_help(self):
        if self.receivers(self.help_requested) > 0:
            self.help_requested.emit(self.FEATURE or "")
            return
        from .help_dialog import HelpDialog
        if self._help_owned is None:
            self._help_owned = HelpDialog(iface=self.iface, parent=self)
        self._help_owned.select_tool(self.FEATURE)
        self._help_owned.show()
        self._help_owned.raise_()

    def _progress_row(self):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        self._progress_label = QLabel("")
        row.addWidget(self._progress_label)
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%p%")
        row.addWidget(self._progress_bar, 1)
        self._cancel_btn = QPushButton(self.tr("Anulează"))
        self._cancel_btn.clicked.connect(self.cancel_task)
        row.addWidget(self._cancel_btn)
        box.setVisible(False)
        self._progress_box = box
        return box

    def set_tip(self, text: str, url: str, link_text: str):
        """The note with a web link above the buttons."""
        self._footer.insertWidget(0, link_label(text, url, link_text))

    def add_button(self, text: str, role, slot, default: bool = False,
                   primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        # the object name has to be set before the button box polishes the button, or the
        # style sheet rule for it never applies
        if primary:
            button.setObjectName("cadexPrimary")
        self.button_box.addButton(button, role)
        # the lambda discards the checked argument that clicked passes
        button.clicked.connect(lambda: slot())
        button.setDefault(default)
        button.setAutoDefault(default)
        return button

    def add_run_buttons(self, run_text: str, slot) -> QPushButton:
        """The run button and Închide; start_task() disables both while a task runs."""
        self._run_btn = self.add_button(run_text, QDialogButtonBox.ButtonRole.AcceptRole,
                                        slot, default=True, primary=True)
        self._close_btn = self.add_button(self.tr("Închide"),
                                          QDialogButtonBox.ButtonRole.RejectRole, self.reject)
        return self._run_btn

    def add_close_button(self) -> QPushButton:
        self._close_btn = self.add_button(self.tr("Închide"),
                                          QDialogButtonBox.ButtonRole.RejectRole, self.reject)
        return self._close_btn

    def dispose(self):
        """On plugin unload: cancel a running task, disconnect from the project, destroy."""
        if self._task is not None:
            self._task.finished_cb = None
            try:
                self._task.cancel()
            except RuntimeError:
                pass
            self._task = None
        self._awaited = None
        self._count_timer.stop()
        self._processing = False
        self._timer.stop()
        for signal in self._project_signals():
            try:
                signal.disconnect(self._on_project_replaced)
            except (TypeError, RuntimeError):
                pass
        for checklist in self.findChildren(LayerChecklist):
            checklist.disconnect_project()
        self.hide()
        self.deleteLater()

    # --- the project ---

    @staticmethod
    def _project_signals():
        project = QgsProject.instance()
        return project.cleared, project.readProject

    def _on_project_replaced(self, *_args):
        try:
            if self._task is not None:
                self._project_replaced = True
            if self._awaited is not None:
                # the run was asked for in the project that closed
                self._end_wait()
                self.notice(self.tr(
                    "Rularea nu a pornit, deoarece proiectul s-a schimbat între timp."))
            self.project_changed()
        except RuntimeError:
            pass  # the window is being destroyed

    def project_changed(self):
        """Called when the project is closed and when another one has been read, whether the
        window is shown or not. What the user chose for the layers of the previous project
        does not apply to this one: field combo boxes and ticked lists return to their
        preselection, and the fields kept per project take the values of the open project, or
        none. A window resets its own choices in an override, which calls this one first."""
        forget_field_choices(self)
        for checklist in self.findChildren(CheckList):
            checklist.forget_choices()
        for key, widget, column, per_project in self._remembered:
            self._restore(key, widget, column, per_project, replaced=True)

    # --- window ---

    def showEvent(self, event):
        super().showEvent(event)
        self._follow_palette()
        if not self._labelled:
            self._labelled = True
            self._label_inputs()
        if self._wheel_guard is not None:
            self._wheel_guard.guard(self._body_widget)
        geom = QSettings().value(self._geometry_key)
        if geom:
            self.restoreGeometry(geom)
        # QgsCollapsibleGroupBox ignores setCollapsed() while hidden after its first show
        for box in self.findChildren(QgsCollapsibleGroupBox):
            box.setCollapsed(box.isCollapsed())
        if self.FIT_HEIGHT:
            self.fit_height()
        else:
            # a geometry saved on another screen or at another scaling can lie off this one
            QTimer.singleShot(0, self._keep_on_screen)

    def _save_geometry(self):
        try:
            QSettings().setValue(self._geometry_key, self.saveGeometry())
        except RuntimeError:
            pass  # widget already destroyed

    def done(self, result):
        # accept() and reject() end here without a QCloseEvent
        self._save_geometry()
        super().done(result)

    def reject(self):
        # Escape closes nothing while a task runs
        if self._processing:
            self._show_running()
            return
        super().reject()

    def closeEvent(self, event):
        if self._processing:
            event.ignore()
            self._show_running()
            return
        self._save_geometry()
        super().closeEvent(event)

    def _show_running(self):
        self._push_message("", self.tr(
            "Fereastra nu se închide cât timp rularea este în curs. Rularea se poate anula "
            "cu butonul 'Anulează'."), Qgis.MessageLevel.Info, WARNING_DURATION)

    def changeEvent(self, event):
        super().changeEvent(event)
        # a hidden window is restyled when it is shown
        if event.type() == QEvent.Type.PaletteChange and self.isVisible():
            self._follow_palette()

    def _follow_palette(self):
        """Take the colors and icon variants of the palette in use: the QGIS theme and, on
        Qt6, the system's light or dark mode can change while the window exists."""
        tokens = theme.tokens()
        if tokens == self.tokens:
            return
        self.tokens = tokens
        self.setStyleSheet(theme.dialog_stylesheet(self.tokens))
        self._set_icons()

    def _set_icons(self):
        """Set the icons that have a light and a dark variant."""
        if self._feature is None:
            return
        icon = _ui_helpers.plugin_icon(self._feature.icon)
        self.setWindowIcon(icon)
        if self._header_icon is not None:
            self._header_icon.setPixmap(_ui_helpers.icon_pixmap(icon, 36))

    def _label_inputs(self):
        """Name the inputs for screen readers and give the run and close buttons and the
        form labels a mnemonic, each letter once in the window. A form label names the first
        input of its row and becomes its buddy; an input of a grid is named after the labels
        that head its row and its column; a button showing only an icon, after its tooltip."""
        labels = []
        for form in self.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                label = label_item.widget() if label_item is not None else None
                if not isinstance(label, QLabel) or not _plain(label.text()) \
                        or field_item is None:
                    continue
                target = _first_input(field_item)
                if target is None:
                    continue
                label.setBuddy(target)
                labels.append(label)
                if isinstance(target, PathEdit) or not target.accessibleName():
                    target.setAccessibleName(_plain(label.text()))
        for grid in self.findChildren(QGridLayout):
            self._name_grid(grid)
        for button in self.findChildren(QAbstractButton):
            if not button.accessibleName() and button.toolTip() \
                    and not any(c.isalnum() for c in button.text()):
                button.setAccessibleName(button.toolTip())
        buttons = [button for button in self.button_box.buttons()
                   if button is self._close_btn or button.objectName() == "cadexPrimary"]
        used = set()
        for widget in buttons + labels:
            if "&" not in widget.text():
                widget.setText(_with_mnemonic(widget.text(), used))

    @staticmethod
    def _name_grid(grid):
        def text_at(row, column):
            item = grid.itemAtPosition(row, column)
            widget = item.widget() if item is not None else None
            return _plain(widget.text()) if isinstance(widget, QLabel) else ""

        for row in range(1, grid.rowCount()):
            for column in range(1, grid.columnCount()):
                item = grid.itemAtPosition(row, column)
                widget = item.widget() if item is not None else None
                if isinstance(widget, INPUTS) and not widget.accessibleName():
                    name = ", ".join(t for t in (text_at(row, 0), text_at(0, column)) if t)
                    if name:
                        widget.setAccessibleName(name)

    def fit_height(self):
        """Resize to the content's height at the current width, at most SCREEN_SHARE of the
        screen. Deferred to the next event loop pass, after the layout has updated."""
        QTimer.singleShot(0, self._fit_height_now)

    def _screen(self):
        return screen_of(self)

    def _available_height(self):
        screen = self._screen()
        return screen.availableGeometry().height() if screen is not None else 10000

    def _fit_height_now(self):
        fit_to_screen(self, self._scroll)

    def _keep_on_screen(self):
        keep_on_screen(self)

    # --- remembered widget values ---

    def remember(self, key: str, widget, column: bool = False, per_project: bool = False):
        """Keep the widget's value between sessions, for the QGIS profile or, with
        per_project, for the project it was used in (dialog_memory), as a path is. column is
        for a combo box filled with field names: its column is kept for the project, and only
        as the user's choice, never as the preselection."""
        self._remembered.append((key, widget, column, per_project))

    def restore_remembered(self):
        """Apply the stored values; call once the combo boxes and lists are filled."""
        for key, widget, column, per_project in self._remembered:
            self._restore(key, widget, column, per_project)

    def _restore(self, key, widget, column, per_project, replaced=False):
        """Apply the stored value of one widget. replaced is true when the project has just
        been replaced: only what is kept per project is applied then, and a path of the
        previous project is emptied when this one has none."""
        name = type(self).__name__
        try:
            if column:
                self._restore_column(name, key, widget)
            elif per_project:
                self._restore_text(name, key, widget, replaced)
            elif not replaced:
                stored = dialog_memory.load(name, key)
                if stored is not None:
                    _ui_helpers.apply_widget_state(widget, stored)
        except (TypeError, ValueError, RuntimeError):
            pass  # stored value does not fit this widget

    @staticmethod
    def _restore_column(name, key, combo):
        # a box not filled yet may still be set to take the column chosen in the project
        # just closed
        combo.setProperty(_ui_helpers.WANTED_PROPERTY, None)
        stored = dialog_memory.load(name, key, project=True)
        if stored is not None:
            choice = json.loads(str(stored))
            if isinstance(choice, dict) and choice.get("chosen"):
                _ui_helpers.choose_field(combo, str(choice.get("data") or ""))
            return
        # CADex 0.2.0 stored the text shown, for the whole profile, preselections included, so
        # only an entry that names a column counts as a choice
        text = dialog_memory.load(name, key)
        index = combo.findText(str(text)) if text is not None else -1
        if index >= 0 and combo.itemData(index):
            _ui_helpers.choose_field(combo, combo.itemData(index))

    @staticmethod
    def _restore_text(name, key, edit, replaced):
        # the path CADex 0.2.0 stored for the whole profile is not offered in any project
        text = dialog_memory.load(name, key, project=True)
        if text is None:
            if not replaced:
                return
            text = ""
        if edit.text() == str(text):
            return
        edit.setText(str(text))
        source = edit.parentWidget()
        if replaced and isinstance(source, CgxmlSource):
            source.refresh()

    def save_remembered(self):
        """Store the current values; called when the tool runs, not when the window closes.
        A value stored for the project removes the one that settings written by CADex 0.2.0
        hold for the profile."""
        name = type(self).__name__
        for key, widget, column, per_project in self._remembered:
            try:
                if column:
                    data, chosen = _ui_helpers.field_choice(widget)
                    dialog_memory.save(name, key, json.dumps({"data": data, "chosen": chosen}),
                                       project=True)
                    dialog_memory.remove(name, key)
                    continue
                value = _ui_helpers.read_widget_state(widget)
            except RuntimeError:
                continue
            if value is None:
                continue
            if per_project:
                dialog_memory.save(name, key, value, project=True)
                dialog_memory.remove(name, key)
            else:
                dialog_memory.save(name, key, value)

    # --- messages ---

    def show_error(self, title: str, message: str, log: bool = True):
        _ui_helpers.show_error(self, title, message, log=log)

    def show_success(self, message: str):
        _ui_helpers.show_success(message, iface=self.iface)

    def show_result(self, headline: str, *, counts=(), notes=(),
                    file_path: Optional[str] = None, details: Optional[str] = None,
                    warning: bool = False, layers=()):
        if self._withheld is not None:
            notes, layers, warning = self._notes_without_layers(notes, file_path), (), True
        _ui_helpers.show_result(self, self.windowTitle(), headline, counts=counts,
                                notes=notes, file_path=file_path, details=details,
                                warning=warning, layers=layers, iface=self.iface)

    def _notes_without_layers(self, notes, file_path) -> list:
        """The notes of a run whose project was closed before it ended: first that the
        project changed and which results stayed out of the project open now, then notes.
        add_layers() returned no layer to the run, so notes name none."""
        held = sum(len(layers) for layers in self._withheld)
        text = self.tr("Proiectul s-a schimbat în timpul rulării.")
        if held:
            text += " " + fmt.plural(held, self.tr(
                "Layerul rezultat nu a fost adăugat în proiectul deschis acum."), self.tr(
                "Layerele rezultate nu au fost adăugate în proiectul deschis acum."))
        if file_path:
            text += " " + self.tr("Fișierele scrise au rămas la locul lor.")
        return [text] + list(notes)

    @staticmethod
    def report_notes(task) -> list:
        """The result window's note for a report the task could not write: [] when it wrote
        its report or had none to write."""
        error = getattr(task, "report_error", None)
        return [error] if error else []

    def warn(self, message: str, focus=None):
        """A warning in the dialog's message bar. focus is the widget to correct: the tab or
        section holding it opens and it takes the keyboard focus."""
        self._push_message(self.tr("Atenție"), message, Qgis.MessageLevel.Warning,
                           WARNING_DURATION)
        if focus is not None:
            self._reveal(focus)

    def notice(self, message: str):
        """An information message in the dialog's message bar, e.g. a canceled run."""
        _ui_helpers.log_message(message)
        self._push_message("", message, Qgis.MessageLevel.Info, 5)

    def _push_message(self, title, message, level, duration):
        # a message bar item shows its text on one line and scrolls the rest; in a tool
        # window most warnings take two lines
        self.message_bar.clearWidgets()
        text = html.escape(message, quote=False).replace("\n", "<br>")
        if title:
            text = f"<b>{html.escape(title, quote=False)}:</b> {text}"
        # created with a parent, or Python deletes it when this method returns
        label = QLabel(text, self.message_bar)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.message_bar.pushItem(QgsMessageBarItem(label, level, duration))

    def fail(self, message: str):
        self.show_error(self.tr("Eroare"), message)

    def confirm(self, title: str, message: str, yes_text: str = "", no_text: str = "") -> bool:
        return _ui_helpers.ask(self, title, message, yes_text or self.tr("Da"),
                               no_text or self.tr("Nu"))

    def _reveal(self, widget):
        """Show the tab, page or collapsed section holding widget, scroll to it, focus it."""
        child = widget
        parent = child.parentWidget()
        while parent is not None and child is not self:
            if isinstance(parent, QStackedWidget):
                tabs = parent.parentWidget()
                if isinstance(tabs, QTabWidget):
                    tabs.setCurrentWidget(child)
                else:
                    parent.setCurrentWidget(child)
            if isinstance(parent, QgsCollapsibleGroupBox) and parent.isCollapsed():
                parent.setCollapsed(False)
            child, parent = parent, parent.parentWidget()
        if self._scroll is not None:
            self._scroll.ensureWidgetVisible(widget)
        widget.setFocus()

    # --- layers ---

    def validate_layer(self, layer: Optional[QgsVectorLayer], role: str,
                       check_features: bool = True, focus=None) -> bool:
        """True for a readable vector layer; otherwise shows the warning and returns False."""
        if not isinstance(layer, QgsVectorLayer):
            self.warn(self.tr("Alegeți layerul {0}.").format(role), focus)
            return False
        if not layer.isValid():
            self.warn(self.tr("Layerul '{0}' nu poate fi citit. Verificați dacă "
                              "fișierul sau conexiunea lui este disponibilă.")
                      .format(layer.name()), focus)
            return False
        if check_features and layer.featureCount() == 0:
            self.warn(self.tr("Layerul '{0}' nu conține niciun element.")
                      .format(layer.name()), focus)
            return False
        return True

    def check_crs(self, layers) -> bool:
        """False, with a warning, for the first layer stereo70.unusable_reason refuses: one
        whose coordinates do not fit its CRS or do not lie in Romania."""
        for layer in layers:
            reason = stereo70.unusable_reason(layer.name(), layer.crs(), layer.extent())
            if reason:
                self.warn(reason)
                return False
        return True

    @staticmethod
    def layers_reading(paths, layers=None):
        """The layers, by default every project layer, whose source file is one of paths."""
        wanted = {os.path.normcase(os.path.abspath(p)) for p in paths if p}
        if layers is None:
            layers = QgsProject.instance().mapLayers().values()
        using = []
        for layer in layers:
            source = layer.source().split("|")[0]
            if source and os.path.normcase(os.path.abspath(source)) in wanted:
                using.append(layer)
        return using

    def release_files(self, paths) -> bool:
        """Remove the project layers reading any of paths, after asking. Windows cannot
        replace a file QGIS has open. False when the user declines."""
        using = self.layers_reading(paths)
        if not using:
            return True
        names = ", ".join(f"'{layer.name()}'" for layer in using)
        files = {os.path.normcase(os.path.abspath(layer.source().split("|")[0]))
                 for layer in using}
        if len(files) > 1:
            question = self.tr(
                "Fișierele rezultate sunt deschise în proiect ca layerele {0}. Pentru a le "
                "înlocui, layerele trebuie eliminate din proiect. Continuați?")
        else:
            question = fmt.plural(len(using), self.tr(
                "Fișierul rezultat este deschis în proiect ca layerul {0}. Pentru a-l "
                "înlocui, layerul trebuie eliminat din proiect. Continuați?"), self.tr(
                "Fișierul rezultat este deschis în proiect ca layerele {0}. Pentru a-l "
                "înlocui, layerele trebuie eliminate din proiect. Continuați?"))
        if not self.confirm(self.tr("Suprascriere"), question.format(names),
                            self.tr("Elimină și continuă"), self.tr("Anulează")):
            return False
        for layer in using:
            # drawing or reading a layer leaves connections to its file in the OGR connection
            # pool; removing the layer closes them only when a later event loop pass deletes
            # their pool group, while reloading the provider closes them at once
            if layer.dataProvider() is not None:
                layer.dataProvider().reloadData()
        QgsProject.instance().removeMapLayers([layer.id() for layer in using])
        return True

    def confirm_overwrite(self, paths) -> bool:
        """True when every one of paths can be written and the user agrees to replace those
        that exist. An existing file that cannot be opened for writing, such as a report open
        in Excel, is reported before anything is asked, so the run does not start."""
        blocked = outputs.blocked(paths)
        if blocked:
            self.fail("\n\n".join(message for _path, message in blocked))
            return False
        existing = [p for p in paths if p and os.path.exists(p)]
        if not existing:
            return True
        # with the folder: the reports of two projects often have the same name
        names = ", ".join(f"'{os.path.normpath(p)}'" for p in existing)
        if len(existing) == 1:
            question = self.tr("Fișierul {0} există deja. Doriți să fie înlocuit?")
        else:
            question = self.tr("Fișierele {0} există deja. Doriți să fie înlocuite?")
        return self.confirm(self.tr("Suprascriere"), question.format(names),
                            self.tr("Înlocuiește"), self.tr("Anulează"))

    def confirm_distinct(self, layers, field) -> bool:
        """True unless two of the layers are versions of the same data by
        layer_sources.shared_keys() and the user cancels: the imobile they share would be
        counted twice."""
        shared = layer_sources.shared_keys(layers, field)
        if shared is None:
            return True
        first, second, keys, total = shared
        sample = ", ".join(keys)
        if total > len(keys):
            sample += self.tr(" și încă {0}").format(total - len(keys))

        def label(layer, other):
            # two conversions add layers with the same name, so the label adds the file name
            if layer.name() != other.name():
                return f"'{layer.name()}'"
            source = os.path.basename(layer.source().split("|")[0])
            return f"'{layer.name()}' ({source})" if source else f"'{layer.name()}'"

        question = fmt.plural(
            total,
            self.tr("{0} apare în ambele layere, {1} și {2} (câmpul '{3}': {4}), și s-ar lua "
                    "în calcul de două ori. Continuați cu ambele layere?"),
            self.tr("{0} apar în ambele layere, {1} și {2} (câmpul '{3}': {4}), și s-ar lua "
                    "în calcul de două ori. Continuați cu ambele layere?"))
        return self.confirm(self.tr("Atenție"), question.format(
            fmt.count(total, self.tr("imobil"), self.tr("imobile")),
            label(first, second), label(second, first), field, sample),
            self.tr("Continuă cu ambele"), self.tr("Anulează"))

    # --- the files of a run's CGXML sources ---

    def sources_ready(self, named, again, whole=False) -> bool:
        """True when each CgxmlSource of named, [(source, its name in a warning, or "" for
        the window's only source)], is counted and has .cgxml files to read, in every part of
        it with whole. Otherwise False: after a warning on the first source that falls short,
        or while the window waits for the counts, after which again() is called."""
        for source, name in named:
            if not source.paths():
                self.warn(self._choose_source(name), source.edit)
                return False
        if not self._sources_counted([source for source, _name in named], again, whole):
            return False
        for source, name in named:
            found, problem, unreadable = source.outcome()
            if problem == "missing":
                self.warn(self._choose_source(name), source.edit)
                return False
            if problem and (whole or not found):
                self.warn(self._unreadable_source(source, name, found, unreadable),
                          source.edit)
                return False
            if not found:
                self.warn(self.tr("Nu s-a găsit niciun fișier .cgxml în {0}.").format(
                    name or self.tr("sursa aleasă")), source.edit)
                return False
        return True

    def _choose_source(self, name) -> str:
        if not name:
            return self.tr("Alegeți folderul, arhiva ZIP sau fișierele CGXML.")
        return self.tr("Alegeți {0}: un folder, o arhivă ZIP sau fișiere .cgxml.").format(name)

    def _unreadable_source(self, source, name, found, unreadable) -> str:
        if not unreadable:
            # the count holds no reason for the part; the status line under the field names it
            return source.status.text()
        subject = name or self.tr("sursa aleasă")
        subject = subject[:1].upper() + subject[1:]
        if found:
            return self.tr("{0} nu poate fi citită în întregime: {1}.").format(
                subject, unreadable)
        return self.tr("{0} nu poate fi citită: {1}.").format(subject, unreadable)

    def _sources_counted(self, sources, again, whole) -> bool:
        """True when every CgxmlSource in sources has a finished count of its current text,
        so the run reads CgxmlSource.outcome() and makes no file system call: one on a
        network drive out of reach blocks for tens of seconds.

        Otherwise starts the counts that are missing, and a new count of a source whose last
        count found no file or, with whole, a part that cannot be read, since the user may
        have fixed it since; disables the window as for a task, with the Anulează button, and
        returns False. again() is called once every count is in, unless the wait is canceled
        or the project is replaced."""
        for source in sources:
            if source.counted() and source not in self._just_counted \
                    and not self._has_files(source, whole):
                source.refresh()
        # a list, so that every count not started yet starts now
        if all([source.counted() for source in sources]):
            return True
        self._awaited = (list(sources), again)
        self._run_message = self.tr("Se caută fișierele...")
        self._started = time.monotonic()
        self.message_bar.clearWidgets()
        self._set_running(True, busy=True)
        self._count_timer.start()
        return False

    @staticmethod
    def _has_files(source, whole) -> bool:
        files, problem, _unreadable = source.outcome()
        return files > 0 and not (whole and problem)

    def _check_counts(self):
        """Call what waits for the counts once they are all in. The window is disabled during
        the wait, so the sources keep the text the run was asked for."""
        if self._awaited is None:
            self._count_timer.stop()
            return
        sources, again = self._awaited
        if not all([source.counted() for source in sources]):
            return
        self._end_wait()
        self._just_counted = tuple(sources)
        try:
            again()
        finally:
            self._just_counted = ()

    def _end_wait(self):
        self._awaited = None
        self._count_timer.stop()
        self._set_running(False)

    # --- background tasks ---

    def start_task(self, task, message: str = ""):
        """Run a CadexTask in the task manager, the dialog disabled and the progress row shown
        until it finishes. The dialog keeps the task until task_done(), so cancel_task() and
        dispose() can reach it while it runs."""
        self._task = task
        self._project_replaced = False
        self._run_message = message
        self._started = time.monotonic()
        task.progressChanged.connect(self._on_task_progress)
        task.finished_cb = self._guarded(task.finished_cb)
        self.message_bar.clearWidgets()
        self._set_running(True)
        QgsApplication.taskManager().addTask(task)

    def _guarded(self, callback):
        """Wrap the finished callback so an exception while showing results is reported as a
        failed run. The callback runs on the main thread, outside the task's own try/except.

        A task belongs to the project it started in. When that project was closed meanwhile,
        the callback runs with _ui_helpers.add_layers() adding nothing to the open project,
        and show_result() adds a note on it; what the run wrote to files stays."""
        if callback is None:
            return None

        def guarded(ok, task):
            self._withheld = [] if self._project_replaced else None
            previous = _ui_helpers.withhold(self._withheld)
            try:
                callback(ok, task)
            except Exception as error:
                self.task_done()
                self.log(traceback.format_exc(), Qgis.MessageLevel.Critical)
                self.fail(self.tr(
                    "Prelucrarea s-a încheiat, dar rezultatele nu au putut fi afișate:\n\n{0}\n\n"
                    "Detaliile tehnice sunt în jurnalul de mesaje al QGIS, în fila CADex.").format(
                        str(error) or error.__class__.__name__))
            finally:
                # the result window's exec() runs a nested event loop, in which the finished
                # callback of another window can run; the withhold() setting found is put back
                _ui_helpers.withhold(previous)
                self._withheld = None
                self._project_replaced = False
        return guarded

    def cancel_task(self):
        if self._awaited is not None:
            self._end_wait()
            self.notice(self.tr("Rularea a fost anulată."))
        elif self._task is not None:
            self._task.cancel()
            self._cancel_btn.setEnabled(False)
            self._run_message = self.tr("Se anulează...")
            self._show_progress_text()

    def task_done(self):
        """Re-enable the dialog; the first call in every finished callback."""
        self._set_running(False)
        self._task = None

    def _on_task_progress(self, value):
        self._progress_bar.setValue(int(value))

    def _show_progress_text(self):
        elapsed = elapsed_text(time.monotonic() - self._started)
        self._progress_label.setText(f"{self._run_message}  {elapsed}".strip())

    def _set_running(self, running, busy=False):
        """Disable the inputs and show the progress row while a task runs, or give the window
        back. busy shows the bar without a percentage, for a wait of unknown length."""
        self._processing = running
        # the finished callbacks read some settings back, so they must not change mid-run
        self._body_widget.setEnabled(not running)
        for button in (self._run_btn, self._close_btn):
            if button is not None:
                button.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        self._progress_box.setVisible(running)
        if running:
            self._progress_bar.setRange(0, 0 if busy else 100)
            self._progress_bar.setValue(0)
            self._show_progress_text()
            self._timer.start()
        else:
            self._timer.stop()

    def log(self, message: str, level=Qgis.MessageLevel.Info):
        _ui_helpers.log_message(message, level)
