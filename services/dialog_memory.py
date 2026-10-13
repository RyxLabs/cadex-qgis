# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The values a dialog was last run with, per dialog and widget."""

from qgis.core import QgsSettings  # type: ignore

# inside settings_service.GROUP, so reset_all() clears it too
GROUP = "CADex/dialog_state"


def _key(dialog_name: str, name: str) -> str:
    return f"{GROUP}/{dialog_name}/{name}"


def load(dialog_name: str, name: str, default=None):
    return QgsSettings().value(_key(dialog_name, name), default)


def save(dialog_name: str, name: str, value):
    QgsSettings().setValue(_key(dialog_name, name), value)
