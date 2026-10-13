# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plugin settings in the CADex group of QgsSettings, typed after their defaults."""

from qgis.core import QgsSettings  # type: ignore

from . import roles

GROUP = "CADex"

# every key with its default; get() returns values of the default's type
DEFAULTS = {
    "general/welcome_shown": False,
    # layer name patterns, see roles.py
    "roles/imobile": "imobil, teren, parcel, land",
    "roles/constructii": "constructi, cladir, building, corp",
    "roles/intravilan": "intravilan",
    "roles/contur": "contur, perimetru, hotar, tarla",
    # not "sector" alone, which would also match "Imobile sector 2"
    "roles/sectoare": "sectoare, limita sector, limite sector, sector cadastral",
    # field name patterns, tried in order
    "fields/id": "id, id_imobil, id_proviz, cadgenno, nr_cad, nr_cadastral",
    "fields/ie": "ie, nr_ie, cf, nr_cf, cf_ie, ie_cf, e2identifier, identificator, "
                 "id_electr, carte funciara",
    # "propr" last: as a fragment it also matches "tip_proprietate"
    "fields/owner": "proprietar, proprietari, propr, titular, posesor, "
                    "detinator, owner",
    # "suprafata" last: as a fragment it also matches the measured area
    "fields/area_deed": "suprafata act, sup_act, sup_acte, supr_act, s_act, "
                        "suprafata acte, suprafata din acte, suprafata, supr, mp",
    "fields/sector": "sector, cadsector, nr_sector, sect",
    # Conversie CGXML column layouts as JSON, "" for the default layout
    "cgxml/columns_imobile": "",
    "cgxml/columns_constructii": "",
    "cgxml/layer_imobile": "Imobile",
    "cgxml/layer_constructii": "Construcții",
}

# role keys in the order the Settings window lists them
LAYER_ROLES = ("roles/imobile", "roles/constructii", "roles/intravilan",
               "roles/contur", "roles/sectoare")
FIELD_ROLES = ("fields/id", "fields/ie", "fields/sector", "fields/owner",
               "fields/area_deed")


def get(key: str):
    default = DEFAULTS[key]
    value = QgsSettings().value(f"{GROUP}/{key}", default)
    if isinstance(default, bool):
        # QgsSettings reads a bool stored in an ini file as text
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    return "" if value is None else str(value)


def set_(key: str, value):
    if key not in DEFAULTS:
        raise KeyError(f"Unknown CADex setting: {key}")
    QgsSettings().setValue(f"{GROUP}/{key}", value)


def reset_all():
    """Remove every stored CADex setting, the remembered dialog values included."""
    settings = QgsSettings()
    settings.beginGroup(GROUP)
    settings.remove("")
    settings.endGroup()


def patterns(key: str) -> list:
    """The parsed name patterns of a roles/ or fields/ key."""
    return roles.parse_patterns(get(key))
