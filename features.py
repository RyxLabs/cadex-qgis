# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Feature catalog read by the toolbar, the menu and the help window."""

from dataclasses import dataclass
from typing import Optional


def QT_TRANSLATE_NOOP(context: str, text: str) -> str:
    # marks the literal for pylupdate without importing Qt, so this module loads in plain Python
    return text


@dataclass(frozen=True)
class Feature:
    method: str                     # plugin method name
    label: str
    icon: str                       # file name in icons/
    hint: str                       # tooltip and help description
    shortcut: Optional[str] = None  # e.g. "Ctrl+Alt+S"


# no default shortcuts: a combination another plugin also claims disables both
CATALOG: tuple = (
    Feature(
        method="convert_cgxml",
        label=QT_TRANSLATE_NOOP("CADex", "Conversie CGXML..."),
        icon="cgxml.svg",
        hint=QT_TRANSLATE_NOOP(
            "CADex",
            "Convertește fișierele CGXML dintr-un folder, dintr-o arhivă ZIP sau "
            "alese unul câte unul, în GeoPackage, Shapefile, DXF sau Excel, cu "
            "imobile, construcții și coloanele alese de dumneavoastră"),
    ),
    Feature(
        method="intravilan_report",
        label=QT_TRANSLATE_NOOP("CADex", "Raport Intravilan / Extravilan..."),
        icon="intravilan.svg",
        hint=QT_TRANSLATE_NOOP(
            "CADex",
            "Calculează pentru fiecare imobil suprafața din intravilan și din "
            "extravilan, cu un layer clasificat și un raport Excel"),
    ),
    Feature(
        method="area_reduction",
        label=QT_TRANSLATE_NOOP("CADex", "Diminuare Suprafețe..."),
        icon="diminuare.svg",
        hint=QT_TRANSLATE_NOOP(
            "CADex",
            "Reduce proporțional suprafețele din acte ale parcelelor dintr-un "
            "tabel Excel sau CSV, astfel încât suma lor să fie egală cu suprafața "
            "măsurată a conturului"),
    ),
    Feature(
        method="compare_layers",
        label=QT_TRANSLATE_NOOP("CADex", "Comparator..."),
        icon="compare.svg",
        hint=QT_TRANSLATE_NOOP(
            "CADex",
            "Compară două versiuni ale acelorași date, două layere sau două seturi "
            "de fișiere CGXML: imobile și construcții apărute, dispărute și modificate, "
            "cu limitele lor, și raport Excel"),
    ),
    Feature(
        method="check_topology",
        label=QT_TRANSLATE_NOOP("CADex", "Verificare Topologică..."),
        icon="verificare.svg",
        hint=QT_TRANSLATE_NOOP(
            "CADex",
            "Verifică sectoarele cadastrale: imobile fără suprapuneri sau goluri, "
            "racordate exact între ele, și construcții în interiorul imobilului lor. "
            "Doar raportează, nu modifică nimic"),
    ),
    Feature(
        method="building_topology",
        label=QT_TRANSLATE_NOOP("CADex", "Topologie Construcții..."),
        icon="constructii.svg",
        hint=QT_TRANSLATE_NOOP(
            "CADex",
            "Taie construcțiile care depășesc limita imobilului pe care se "
            "află, într-un layer nou, cu suprafața pierdută pentru fiecare "
            "construcție"),
    ),
    Feature(
        method="open_settings",
        label=QT_TRANSLATE_NOOP("CADex", "Setări..."),
        icon="settings.svg",
        hint=QT_TRANSLATE_NOOP(
            "CADex",
            "Numele după care se preselectează layerele și câmpurile "
            "dumneavoastră"),
    ),
    Feature(
        method="open_help",
        label=QT_TRANSLATE_NOOP("CADex", "Ajutor..."),
        icon="help.svg",
        hint=QT_TRANSLATE_NOOP(
            "CADex",
            "Descrierea instrumentelor și informații despre plugin"),
    ),
    Feature(
        method="open_website",
        label=QT_TRANSLATE_NOOP("CADex", "CADex Online"),
        icon="cadex.svg",
        hint=QT_TRANSLATE_NOOP(
            "CADex",
            "Deschide cadex.ro: validator, rapoarte și comparare CGXML în "
            "browser, gratuite pentru un fișier"),
    ),
)


# toolbar buttons in display order, None is a separator
TOOLBAR: tuple = (
    "convert_cgxml",
    "intravilan_report",
    "area_reduction",
    "compare_layers",
    "check_topology",
    "building_topology",
    None,
    "open_settings",
    "open_help",
    "open_website",
)


# Vector > CADex menu entries in display order
MENU: tuple = (
    "convert_cgxml",
    "intravilan_report",
    "area_reduction",
    "compare_layers",
    "check_topology",
    "building_topology",
    "open_settings",
    "open_help",
    "open_website",
)


WEBSITE = "https://cadex.ro"
WEBSITE_VALIDATOR = "https://cadex.ro/validator"
WEBSITE_COMPARATOR = "https://cadex.ro/comparator"
WEBSITE_REPORTS = "https://cadex.ro/interogari-predefinite"
VERNIER_URL = "https://plugins.qgis.org/plugins/vernier/"
SOURCE_URL = "https://github.com/ryxlabs/cadex-qgis"
