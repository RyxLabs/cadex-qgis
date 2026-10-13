# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Output files: whether a run can write them, and the message when it cannot."""

import os

# what a Shapefile output consists of, the .qml being the style CADex saves beside it
SHAPEFILE_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qml")


def parts(path) -> list:
    """The files written for path: every part of a Shapefile, else path itself."""
    stem, extension = os.path.splitext(path)
    if extension.lower() == ".shp":
        return [stem + part for part in SHAPEFILE_PARTS]
    return [path]


def write_error(path, error=None) -> str:
    """The Romanian sentence for an output that cannot be written. Excel shares an open
    workbook for reading only, so writing it raises PermissionError."""
    name = os.path.basename(path) or path
    if isinstance(error, FileNotFoundError):
        return f"Fișierul '{name}' nu poate fi scris: folderul lui nu există."
    if error is None or isinstance(error, PermissionError):
        return (f"Fișierul '{name}' nu poate fi scris: este deschis în alt program, de "
                "exemplu Excel, sau este protejat la scriere. Închideți-l și încercați din nou.")
    reason = getattr(error, "strerror", None) or str(error) or error.__class__.__name__
    return f"Fișierul '{name}' nu poate fi scris: {reason}."


def blocked(paths) -> list:
    """[(path, message)] for the paths whose existing files cannot be opened for writing,
    checked before a run so that a long computation is not lost at its last step. A file
    that does not exist yet is not checked."""
    found = []
    for path in paths:
        if not path:
            continue
        for part in parts(path):
            if not os.path.exists(part):
                continue
            try:
                with open(part, "r+b"):
                    pass
            except OSError as error:
                found.append((path, write_error(part, error)))
                break
    return found
