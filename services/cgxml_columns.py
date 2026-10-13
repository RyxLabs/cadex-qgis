# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Output columns of Conversie CGXML and the user's column layout (selection, order, field
names). The field names are used by GeoPackage and Shapefile; Excel heads its columns with the
labels."""

import json
import re
from dataclasses import dataclass

# dBase limits: 10 characters per field name, 254 bytes per text value
SHP_NAME_LIMIT = 10
SHP_TEXT_LIMIT = 254

_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# the column GDAL creates for the feature number and the one holding the geometry; a field of
# the same name is refused or merged with it
GEOPACKAGE_RESERVED = {"fid": "numărul elementului", "geom": "geometrie"}


@dataclass(frozen=True)
class Column:
    key: str            # record key
    name: str           # default field name, at most 10 characters for Shapefile
    kind: str           # "int", "real" or "text"
    label: str          # shown in the dialog and as the Excel header
    on: bool = True     # written by default
    hint: str = ""      # tooltip: the CGXML element or the possible values


IMOBILE = (
    Column("id", "id", "int", "ID imobil", hint="CADGENNO"),
    Column("ie", "ie", "int", "Identificator electronic", hint="E2IDENTIFIER"),
    Column("sector", "sector", "int", "Sector cadastral", hint="CADSECTOR"),
    Column("judet", "judet", "text", "Județ", on=False),
    Column("uat", "uat", "text", "UAT"),
    Column("localitate", "localitate", "text", "Localitate"),
    Column("adresa", "adresa", "text", "Adresa completă"),
    Column("desc_adresa", "desc_adr", "text", "Descrierea adresei", on=False),
    Column("nr_postal", "nr_postal", "text", "Nr. poștal"),
    Column("tip_teren", "tip_teren", "text", "Intravilan / Extravilan",
           hint="Intravilan, Extravilan sau Mixt"),
    Column("nr_tarla", "nr_tarla", "text", "Nr. tarla"),
    Column("nr_parcele", "nr_parcele", "text", "Nr. parcelă"),
    Column("categ_fol", "categ_fol", "text", "Categorii de folosință",
           hint="Codurile categoriilor, de exemplu CC, A"),
    Column("nr_titlu", "nr_titlu", "text", "Nr. titlu de proprietate"),
    Column("imprej", "imprej", "text", "Împrejmuit", hint="Da sau Nu"),
    Column("sup_mas", "sup_mas", "real", "Suprafața măsurată (mp)"),
    Column("sup_acte", "sup_acte", "real", "Suprafața din acte (mp)"),
    Column("propr", "propr", "text", "Proprietari și posesori"),
    Column("alte_pers", "alte_pers", "text", "Alte persoane", hint=(
        "Celelalte persoane din fișier, fiecare cu dreptul sau înscrierea ei, de exemplu "
        "ipotecă, administrare sau notare, și cu locul, dacă nu sunt legate de teren")),
    Column("nr_cad_vechi", "cad_vechi", "text", "Nr. cadastral vechi", hint="PAPERCADNO"),
    Column("nr_cf_vechi", "cf_vechi", "text", "Nr. CF vechi", hint="PAPERLBNO"),
    Column("note", "note", "text", "Note"),
)

CONSTRUCTII = (
    Column("id", "id", "int", "ID imobil", hint="CADGENNO"),
    Column("ie_imobil", "ie_imobil", "int", "Identificator electronic al imobilului", on=False),
    Column("sector", "sector", "int", "Sector cadastral", hint="CADSECTOR"),
    Column("nr_constr", "nr_constr", "text", "Nr. construcție", hint="C1, C2..."),
    Column("ie", "ie", "text", "Identificator electronic al construcției", hint="E2IDENTIFIER"),
    Column("tip_teren", "tip_teren", "text", "Intravilan / Extravilan",
           hint="Intravilan, Extravilan sau Mixt"),
    Column("destinatie", "destinatie", "text", "Destinația (cod)", hint="CL, CA, CIE sau CAS"),
    Column("destinatie_text", "dest_text", "text", "Destinația (denumire)", on=False),
    Column("etaje", "etaje", "int", "Nr. niveluri"),
    Column("sup_mas", "sup_mas", "real", "Suprafața construită la sol (mp)"),
    Column("sup_tot", "sup_tot", "real", "Suprafața construită desfășurată (mp)"),
    Column("are_acte", "are_acte", "text", "Are acte", hint="Da sau Nu"),
    Column("propr", "propr", "text", "Proprietari"),
    Column("alte_pers", "alte_pers", "text", "Alte persoane", hint=(
        "Celelalte persoane legate de construcție sau de unitățile ei individuale, fiecare "
        "cu dreptul sau înscrierea ei")),
    Column("note", "note", "text", "Note"),
)

SPECS = {"imobile": IMOBILE, "constructii": CONSTRUCTII}


def default_layout(which: str) -> list:
    """[{"key", "name", "on"}] in catalog order."""
    return [{"key": c.key, "name": c.name, "on": c.on} for c in SPECS[which]]


def layout_from_json(which: str, text: str) -> list:
    """A stored layout checked against the catalog.

    Unknown and repeated keys are dropped and missing columns appended with their defaults.
    Unreadable JSON gives the default layout.
    """
    spec = {c.key: c for c in SPECS[which]}
    try:
        stored = json.loads(text) if text else None
    except (TypeError, ValueError):
        stored = None
    if not isinstance(stored, list):
        return default_layout(which)
    layout = []
    seen = set()
    for item in stored:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if key not in spec or key in seen:
            continue
        name = str(item.get("name") or spec[key].name).strip()
        layout.append({"key": key, "name": name, "on": bool(item.get("on"))})
        seen.add(key)
    for column in SPECS[which]:
        if column.key not in seen:
            layout.append({"key": column.key, "name": column.name,
                           "on": column.on})
    return layout


def layout_to_json(layout: list) -> str:
    return json.dumps([{"key": c["key"], "name": c["name"], "on": bool(c["on"])}
                       for c in layout], ensure_ascii=False)


def kind_of(which: str, key: str) -> str:
    for column in SPECS[which]:
        if column.key == key:
            return column.kind
    return "text"


def label_of(which: str, key: str) -> str:
    for column in SPECS[which]:
        if column.key == key:
            return column.label
    return key


def hint_of(which: str, key: str) -> str:
    for column in SPECS[which]:
        if column.key == key:
            return column.hint
    return ""


def output_names(layout: list, shapefile: bool) -> list:
    """[(key, field name)] of the enabled columns, cut to 10 characters for Shapefile."""
    result = []
    for column in layout:
        if not column["on"]:
            continue
        name = column["name"].strip()
        if shapefile:
            name = name[:SHP_NAME_LIMIT]
        result.append((column["key"], name))
    return result


def layout_problems(layout: list, shapefile: bool, which: str = "",
                    geopackage: bool = False, any_name: bool = False) -> list:
    """Romanian sentences for the user, empty when the layout can be written. With which,
    the columns are named by their labels, as the window lists them. geopackage refuses the
    field names GeoPackage keeps for the feature number and the geometry. any_name is for an
    output that takes any field name, such as a temporary layer or GeoJSON: only a column
    without a name and a name used twice are refused."""
    def label(key):
        return label_of(which, key) if which else key

    problems = []
    names = output_names(layout, shapefile)
    if not names and not any_name:
        problems.append("Nu este bifată nicio coloană.")
    seen = {}
    for key, name in names:
        if not name:
            problems.append(f"Coloana '{label(key)}' nu are nume.")
            continue
        if not any_name and not _VALID_NAME.match(name):
            problems.append(
                f"Numele '{name}' poate conține doar litere fără diacritice, "
                "cifre și '_', și nu poate începe cu o cifră.")
        folded = name.lower()
        if geopackage and folded in GEOPACKAGE_RESERVED:
            problems.append(
                f"Numele '{name}' al coloanei '{label(key)}' este folosit de GeoPackage "
                f"pentru {GEOPACKAGE_RESERVED[folded]}.")
        if folded in seen:
            if shapefile:
                problems.append(
                    f"Coloanele '{label(seen[folded])}' și '{label(key)}' ajung la același "
                    f"nume, '{name}', după scurtarea la 10 caractere cerută "
                    "de Shapefile.")
            else:
                problems.append(f"Numele '{name}' apare de două ori.")
        else:
            seen[folded] = key
    return problems
