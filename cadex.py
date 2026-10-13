# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The plugin class: toolbar, menu and actions, built from the catalog in features.py."""

from qgis.PyQt.QtCore import QCoreApplication  # type: ignore
from qgis.core import Qgis, QgsApplication, QgsMessageLog  # type: ignore

from . import features
from .services import settings_service

PLUGIN_NAME = "CADex"

# tool windows that do not preselect by the Setări name patterns; saving new patterns does
# not rebuild them
WITHOUT_PATTERNS = ("convert_cgxml", "open_help")


def _name_patterns() -> tuple:
    return tuple(settings_service.get(key)
                 for key in settings_service.LAYER_ROLES + settings_service.FIELD_ROLES)


class Cadex:

    def __init__(self, iface):
        # iface is None when qgis_process loads the plugin for its algorithms only
        self.iface = iface
        self.actions = {}
        self.toolbar = None
        self.menu_name = "&CADex"
        self.provider = None
        # one non-modal window per tool, keyed by catalog method
        self._dialogs = {}
        # the name patterns each window was built with, keyed by catalog method
        self._built_with = {}
        # the Setări window while it is open
        self._settings = None
        self._tooltips = {}

    def tr(self, text: str) -> str:
        return QCoreApplication.translate("CADex", text)

    # --- processing ---

    def initProcessing(self):
        # qgis_process calls this on its own, the desktop through initGui
        if self.provider is not None:
            return
        from .processing_provider.provider import CadexProvider
        self.provider = CadexProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    # --- gui lifecycle ---

    def initGui(self):
        # widget imports stay out of module level, qgis_process loads this class without a GUI
        from qgis.PyQt.QtWidgets import QAction  # type: ignore
        from .dialogs import _ui_helpers

        self.initProcessing()
        self.toolbar = self.iface.addToolBar(PLUGIN_NAME)
        self.toolbar.setObjectName("CADexToolbar")

        for feat in features.CATALOG:
            handler = getattr(self, feat.method, None)
            if not callable(handler):
                QgsMessageLog.logMessage(
                    f"Catalog entry '{feat.method}' has no implementation - skipped.",
                    PLUGIN_NAME, level=Qgis.MessageLevel.Warning)
                continue
            label = self.tr(feat.label)
            action = QAction(_ui_helpers.plugin_icon(feat.icon), label,
                             self.iface.mainWindow())
            tooltip = f"<b>{label.rstrip('.')}</b>"
            if feat.shortcut:
                tooltip += f" ({feat.shortcut})"
            tooltip += f"<br>{self.tr(feat.hint)}"
            action.setToolTip(tooltip)
            if feat.shortcut:
                action.setShortcut(feat.shortcut)
            action.triggered.connect(handler)
            self.actions[feat.method] = action

        for method in features.TOOLBAR:
            if method is None:
                self.toolbar.addSeparator()
            elif method in self.actions:
                self.toolbar.addAction(self.actions[method])

        for method in features.MENU:
            if method in self.actions:
                self.iface.addPluginToVectorMenu(self.menu_name, self.actions[method])

        # QGIS registers the main window's actions for its shortcut list some time after the
        # plugins are loaded, which wraps each tooltip in <b>; ours is put back on any change
        self._tooltips = {method: action.toolTip() for method, action in self.actions.items()}
        for action in self.actions.values():
            action.changed.connect(self._restore_tooltips)

        if not settings_service.get("general/welcome_shown"):
            settings_service.set_("general/welcome_shown", True)
            self._show_welcome()

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        for dialog in list(self._dialogs.values()) + [self._settings]:
            try:
                if dialog is not None:
                    dialog.dispose()
            except RuntimeError:
                pass  # already destroyed
        self._dialogs.clear()
        self._built_with.clear()
        self._settings = None
        for action in self.actions.values():
            self.iface.removePluginVectorMenu(self.menu_name, action)
            # parented to the main window, so without an explicit delete every plugin reload
            # leaves the old actions behind
            try:
                action.triggered.disconnect()
            except TypeError:
                pass
            action.setParent(None)
            action.deleteLater()
        self.actions.clear()
        if self.toolbar is not None:
            try:
                self.iface.mainWindow().removeToolBar(self.toolbar)
                self.toolbar.deleteLater()
            except RuntimeError:
                pass
            self.toolbar = None

    def _restore_tooltips(self):
        for method, action in self.actions.items():
            try:
                # setting the same text again emits nothing, so this does not loop
                if action.toolTip() != self._tooltips[method]:
                    action.setToolTip(self._tooltips[method])
            except (KeyError, RuntimeError):
                pass  # unloaded in the meantime

    def _show_welcome(self):
        from qgis.PyQt.QtWidgets import QPushButton  # type: ignore
        try:
            bar = self.iface.messageBar()
            widget = bar.createMessage(
                PLUGIN_NAME,
                self.tr("CADex este instalat. Instrumentele sunt în bara CADex și "
                        "în meniul Vector > CADex."))
            button = QPushButton(self.tr("CADex Online"))
            button.clicked.connect(self.open_website)
            widget.layout().addWidget(button)
            bar.pushWidget(widget, Qgis.MessageLevel.Info, 15)
        except RuntimeError:
            pass

    def _show_tool(self, method, dialog_class):
        """Show the tool's window, reusing the open one. Closing only hides it."""
        dialog = self._dialogs.get(method)
        if dialog is not None:
            try:
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
                return dialog
            except RuntimeError:
                self._dialogs.pop(method, None)
        dialog = dialog_class(iface=self.iface, parent=self.iface.mainWindow())
        dialog.help_requested.connect(self.show_help)
        self._dialogs[method] = dialog
        self._built_with[method] = _name_patterns()
        dialog.show()
        return dialog

    # --- tools ---

    def convert_cgxml(self):
        from .dialogs.cgxml_dialog import CgxmlDialog
        return self._show_tool("convert_cgxml", CgxmlDialog)

    def intravilan_report(self):
        from .dialogs.intravilan_dialog import IntravilanDialog
        return self._show_tool("intravilan_report", IntravilanDialog)

    def area_reduction(self):
        from .dialogs.diminuare_dialog import DiminuareDialog
        return self._show_tool("area_reduction", DiminuareDialog)

    def compare_layers(self):
        from .dialogs.compare_dialog import CompareDialog
        return self._show_tool("compare_layers", CompareDialog)

    def check_topology(self):
        from .dialogs.topology_check_dialog import TopologyCheckDialog
        return self._show_tool("check_topology", TopologyCheckDialog)

    def building_topology(self):
        from .dialogs.building_topology_dialog import BuildingTopologyDialog
        return self._show_tool("building_topology", BuildingTopologyDialog)

    def open_settings(self):
        """The Setări window, non-modal like the tool windows; while it is open, opening it
        again raises it. Each opening reads the saved names."""
        from .dialogs.settings_dialog import SettingsDialog
        dialog = self._settings
        if dialog is not None:
            try:
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
                return dialog
            except RuntimeError:
                self._settings = None
        dialog = SettingsDialog(iface=self.iface, parent=self.iface.mainWindow())
        dialog.help_requested.connect(self.show_help)
        dialog.accepted.connect(self._settings_saved)
        dialog.finished.connect(self._settings_closed)
        self._settings = dialog
        dialog.show()
        return dialog

    def _settings_closed(self):
        # parented to the main window, so an undeleted window stays in memory until QGIS closes
        dialog, self._settings = self._settings, None
        if dialog is not None:
            dialog.deleteLater()

    def _settings_saved(self):
        """A tool preselects layers and fields by the name patterns when its window is built,
        so a hidden window built with other patterns is dropped and built again when it next
        opens. Every other window keeps what the user filled in."""
        patterns = _name_patterns()
        for method, dialog in list(self._dialogs.items()):
            if method in WITHOUT_PATTERNS or self._built_with.get(method) == patterns:
                continue
            try:
                if dialog.isVisible() or getattr(dialog, "_task", None) is not None:
                    continue
                dialog.dispose()
            except RuntimeError:
                pass  # already destroyed
            del self._dialogs[method]
            self._built_with.pop(method, None)

    def open_help(self):
        return self.show_help("")

    def show_help(self, method=""):
        """The help window, at the page of the given catalog method."""
        from .dialogs.help_dialog import HelpDialog
        dialog = self._show_tool("open_help", HelpDialog)
        if method:
            dialog.select_tool(method)
        return dialog

    def open_website(self):
        from .dialogs import _ui_helpers
        _ui_helpers.open_url(features.WEBSITE)
