# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Translation helper. Every string of the plugin uses the "CADex" context."""

from qgis.PyQt.QtCore import QCoreApplication  # type: ignore


def tr(text: str) -> str:
    return QCoreApplication.translate("CADex", text)
