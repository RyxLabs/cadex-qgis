# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""ANCPI dictionary codes: the Romanian labels the conversion writes for buildings,
addresses and registrations, and the use-category codes."""

# the conversion writes these codes as they are; the comparator finds them inside parcel
# numbers such as "CC12"
USECATEGORY = frozenset(("A", "P", "F", "V", "L", "PD", "CC", "HR", "HB", "DR", "CF", "N",
                         "NA", "ALTELE"))

BUILDINGDESTINATION = {
    "CA": "Construcții anexă",
    "CAS": "Construcții administrative și social-culturale",
    "CIE": "Construcții industriale și edilitare",
    "CL": "Construcții de locuințe",
}

STREETTYPE = {
    "AL": "Aleea",
    "BDUL": "Bulevardul",
    "CALEA": "Calea",
    "CAREU": "Careul",
    "DRUMUL": "Drumul",
    "FND": "Fundătura",
    "FNDC": "Fundacul",
    "INTR": "Intrarea",
    "PREL": "Prelungirea",
    "PSJ": "Pasajul",
    "PIATA": "Piața",
    "SOS": "Șoseaua",
    "SPLAIUL": "Splaiul",
    "STR": "Strada",
    "STRADELA": "Stradela",
    "STRD": "Stradela",
    "ZONA": "Zona",
}

DISTRICTTYPE = {
    "CAR": "Careul",
    "CART": "Cartierul",
    "CVART": "Cvartalul",
    "MICRO": "Microraionul",
}

# written after a person's name, so lower case
RIGHTTYPE = {
    "PROPRIETATE": "proprietate",
    "ADMINISTRARE": "administrare",
    "FOLOSINTA CU TITLU GRATUIT": "folosință cu titlu gratuit",
    "CONCESIUNE": "concesiune",
    "SERVITUTE": "servitute",
    "SUPERFICIE": "superficie",
    "HABITATIE": "abitație",
    "UZ": "uz",
    "UZUFRUCT": "uzufruct",
    "UZUFRUCT VIAGER": "uzufruct viager",
    "IPOTECA": "ipotecă",
    "IPOTECA LEGALA": "ipotecă legală",
    "PRIVILEGIU IMOBILIAR": "privilegiu imobiliar",
    "COMODAT": "comodat",
    "INCHIRIERE": "închiriere",
    "LEASING IMOBILIAR": "leasing imobiliar",
    "FOLOSINTA": "folosință",
    "FOLOSINTA SPECIALA": "folosință specială",
}

REGISTRATIONTYPE = {
    "INTAB": "intabulare",
    "NOTATION": "notare",
    "POSESION_REGISTATION": "posesie",
    "PROVISIONALENTRY": "înscriere provizorie",
}


def label(table: dict, code: str) -> str:
    """The label of a code, or the code itself when unknown."""
    return table.get(code, code) if code else ""
