# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Field type constants and NULL checks that work on Qt5 and Qt6 builds of QGIS."""

from qgis.core import Qgis  # type: ignore
from qgis.PyQt.QtCore import QMetaType  # type: ignore

# QgsField takes QMetaType.Type from 3.38 and deprecates QVariant.Type there; 3.34 only has
# QVariant.Type and Qt6 has no QVariant.Type at all. Both enums use the same numbers.
if Qgis.QGIS_VERSION_INT >= 33800:
    FIELD_STRING = QMetaType.Type.QString
    FIELD_INT = QMetaType.Type.Int
    FIELD_LONGLONG = QMetaType.Type.LongLong
    FIELD_DOUBLE = QMetaType.Type.Double
else:
    from qgis.PyQt.QtCore import QVariant  # type: ignore

    FIELD_STRING = QVariant.String
    FIELD_INT = QVariant.Int
    FIELD_LONGLONG = QVariant.LongLong
    FIELD_DOUBLE = QVariant.Double


def is_null(value) -> bool:
    """True for None and for the NULL QVariant that Qt5 builds return for empty attributes."""
    if value is None:
        return True
    is_null_attr = getattr(value, "isNull", None)
    if callable(is_null_attr) and type(value).__name__ == "QVariant":
        return bool(is_null_attr())
    return False


__all__ = ["FIELD_DOUBLE", "FIELD_INT", "FIELD_LONGLONG", "FIELD_STRING", "is_null"]
