# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings window: the name patterns used to preselect layers and fields."""

from qgis.PyQt.QtCore import QEvent  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QDialogButtonBox, QLineEdit, QTabWidget, QWidget,
)
from qgis.core import QgsProject  # type: ignore

from .base_dialog import BaseDialog
from .widgets import (
    DATA_ROLES, StatusLabel, add_field_row, hint_label, make_form, role_layers,
)
from ..features import QT_TRANSLATE_NOOP
from ..services import roles, settings_service

LAYER_LABELS = {
    "roles/imobile": QT_TRANSLATE_NOOP("CADex", "Imobile"),
    "roles/constructii": QT_TRANSLATE_NOOP("CADex", "Construcții"),
    "roles/intravilan": QT_TRANSLATE_NOOP("CADex", "Contur intravilan"),
    "roles/contur": QT_TRANSLATE_NOOP("CADex", "Contur (diminuare)"),
    "roles/sectoare": QT_TRANSLATE_NOOP("CADex", "Limite de sector"),
}
FIELD_LABELS = {
    "fields/id": QT_TRANSLATE_NOOP("CADex", "ID imobil"),
    "fields/ie": QT_TRANSLATE_NOOP("CADex", "CF/IE"),
    "fields/sector": QT_TRANSLATE_NOOP("CADex", "Sector cadastral"),
    "fields/owner": QT_TRANSLATE_NOOP("CADex", "Proprietar"),
    "fields/area_deed": QT_TRANSLATE_NOOP("CADex", "Suprafața din acte (Excel)"),
}


class SettingsDialog(BaseDialog):
    FEATURE = "open_settings"

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Setări CADex"))
        self.setMinimumWidth(640)
        self._edits = {}
        self._matches = {}
        self._following = False
        self._setup_ui()

    def _setup_ui(self):
        self.body.addWidget(hint_label(self.tr(
            "Layerele și câmpurile se preselectează după numele lor. Scrieți "
            "fragmentele de nume folosite în firma dumneavoastră, separate prin "
            "virgulă. Majusculele și diacriticele nu contează, iar '*' "
            "ține locul oricărui text. În fiecare instrument puteți alege oricând "
            "și manual.")))

        tabs = QTabWidget()
        tabs.addTab(self._tab(settings_service.LAYER_ROLES, LAYER_LABELS, True),
                    self.tr("Layere"))
        tabs.addTab(self._tab(settings_service.FIELD_ROLES, FIELD_LABELS, False),
                    self.tr("Câmpuri"))
        self.body.addWidget(tabs)

        reset = self.add_button(self.tr("Implicite"), QDialogButtonBox.ButtonRole.ResetRole,
                                self._reset)
        reset.setToolTip(self.tr("Revine la numele implicite"))
        self.add_button(self.tr("Salvează"), QDialogButtonBox.ButtonRole.AcceptRole,
                        self._save, default=True, primary=True)
        self.add_close_button()

    def _tab(self, keys, labels, layers):
        tab = QWidget()
        form = make_form(tab)
        for key in keys:
            edit = QLineEdit(settings_service.get(key))
            self._edits[key] = edit
            form.addRow(self.tr(labels[key]) + ":", edit)
            if layers:
                match = StatusLabel()
                self._matches[key] = match
                add_field_row(form, match)
                edit.textChanged.connect(lambda _text, k=key: self._on_edited(k))
                self._show_matches(key)
        if not layers:
            form.addRow(hint_label(self.tr(
                "Pentru câmpuri, un nume întreg se potrivește întotdeauna. Un "
                "fragment de cel puțin 4 litere se potrivește și în interiorul "
                "unui nume mai lung.")))
        return tab

    def _on_edited(self, key):
        self._show_matches(key)
        if key in DATA_ROLES:
            # the contour and sector previews leave out the layers matching these names
            for other in self._matches:
                if other not in DATA_ROLES:
                    self._show_matches(other)

    def _patterns(self, key):
        """The patterns as typed in this window, the saved ones for a field not built yet."""
        edit = self._edits.get(key)
        if edit is None:
            return settings_service.patterns(key)
        return roles.parse_patterns(edit.text())

    def showEvent(self, event):
        # the window is not modal, so the project can change while it is open: the previews
        # are redone when layers are added or removed, and when the window is activated again
        # after work in another window, where layers can be renamed
        super().showEvent(event)
        if not self._following:
            project = QgsProject.instance()
            project.layersAdded.connect(self._show_all_matches)
            project.layersRemoved.connect(self._show_all_matches)
            self._following = True
        self._show_all_matches()

    def hideEvent(self, event):
        if self._following:
            project = QgsProject.instance()
            for signal in (project.layersAdded, project.layersRemoved):
                try:
                    signal.disconnect(self._show_all_matches)
                except (TypeError, RuntimeError):
                    pass
            self._following = False
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._show_all_matches()

    def _show_all_matches(self, *_args):
        for key in self._matches:
            self._show_matches(key)

    def _show_matches(self, key):
        # the layers the tools would preselect, by the names typed here
        layers = list(QgsProject.instance().mapLayers().values())
        names = sorted(layer.name() for layer in role_layers(key, layers, self._patterns))
        if names:
            self._matches[key].set_status(
                self.tr("În proiectul deschis: {0}").format(", ".join(names)), "ok")
        else:
            self._matches[key].set_status(
                self.tr("Niciun layer din proiectul deschis nu se potrivește."))

    def _reset(self):
        for key, edit in self._edits.items():
            edit.setText(settings_service.DEFAULTS[key])

    def _save(self):
        for key, edit in self._edits.items():
            # stored as typed, parsed on read; an empty value preselects nothing
            settings_service.set_(key, edit.text().strip())
        self.show_success(self.tr("Setările au fost salvate."))
        self.accept()
