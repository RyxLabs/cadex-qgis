# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build data/siruta.db from a full SIRUTA database: the three tables the plugin reads.

Usage: python scripts/build_siruta.py <source siruta.db>

The source must have names with diacritics ("Iași", not "Iasi").
"""

import os
import sqlite3
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "data", "siruta.db")

SCHEMA = """
CREATE TABLE counties (
    county_id   INTEGER PRIMARY KEY,
    county_code TEXT NOT NULL,
    name        TEXT NOT NULL
);
CREATE TABLE admin_units (
    siruta    INTEGER PRIMARY KEY,
    county_id INTEGER NOT NULL,
    name      TEXT NOT NULL
);
CREATE TABLE localities (
    siruta INTEGER PRIMARY KEY,
    uat    INTEGER NOT NULL,
    name   TEXT NOT NULL
);
"""


def read_only_uri(path: str) -> str:
    """The SQLite URI that opens path read-only, built like services/siruta.read_only_uri:
    an empty authority, so a network (UNC) path keeps both its leading slashes, and "#", "?"
    and "%" in folder names escaped."""
    path = os.path.abspath(path).replace(os.sep, "/")
    if not path.startswith("/"):
        path = "/" + path
    return "file://" + urllib.parse.quote(path, safe="/:") + "?mode=ro"


def build(source: str, target: str = TARGET) -> dict:
    uri = read_only_uri(source)
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    # written beside the target and moved onto it, so a failed run keeps the previous file.
    # The move fails on Windows while another program has the target open; the partial file
    # is removed then too, because build_zip stops on any file it does not ship.
    partial = target + ".partial"
    if os.path.exists(partial):
        os.remove(partial)
    try:
        counts = copy_tables(uri, partial)
        os.replace(partial, target)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    return counts


def copy_tables(uri: str, path: str) -> dict:
    src = sqlite3.connect(uri, uri=True)
    dst = sqlite3.connect(path)
    try:
        dst.executescript(SCHEMA)
        dst.executemany(
            "INSERT INTO counties VALUES (?, ?, ?)",
            src.execute("SELECT county_id, county_code, name FROM counties"))
        dst.executemany(
            "INSERT INTO admin_units VALUES (?, ?, ?)",
            src.execute("SELECT siruta, county_id, name FROM admin_units"))
        # localities reference their UAT by admin_unit_id; "Neidentificata" is SIRUTA's
        # placeholder for an unknown village
        dst.executemany(
            "INSERT OR IGNORE INTO localities VALUES (?, ?, ?)",
            src.execute(
                "SELECT l.siruta, a.siruta, l.name FROM localities l "
                "JOIN admin_units a ON a.admin_unit_id = l.admin_unit_id "
                "WHERE l.name != 'Neidentificata'"))
        dst.commit()
        counts = {}
        for table in ("counties", "admin_units", "localities"):
            query = f"SELECT COUNT(*) FROM {table}"  # nosec B608 - fixed table names
            counts[table] = dst.execute(query).fetchone()[0]
        dst.execute("VACUUM")
        return counts
    finally:
        dst.close()
        src.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    result = build(sys.argv[1])
    size_kb = os.path.getsize(TARGET) / 1024
    print(f"{TARGET}: {result}, {size_kb:.0f} KB")
