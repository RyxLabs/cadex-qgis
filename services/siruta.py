# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""UAT, county and locality names by SIRUTA code, from data/siruta.db.

The tables are loaded into dicts once, so worker threads never share a sqlite connection.
A database that cannot be read leaves every name empty and problem() returns the sentence
for the user, until the next run calls retry().
"""

import os
import sqlite3
import threading
import urllib.parse

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "siruta.db")

UNREADABLE = ("Baza de date SIRUTA a pluginului nu a putut fi citită, deci denumirile de UAT, "
              "județ și localitate lipsesc din rezultat.")

_lock = threading.Lock()
_tables = None
_problem = ""


def read_only_uri(path: str) -> str:
    """The SQLite URI that opens path read-only, for a drive, network (UNC) or POSIX path.

    SQLite refuses a server name as the URI's authority, so the authority stays empty and a
    network path keeps both its leading slashes in the URI's path. Quoting escapes the "#",
    "?" and "%" a folder name can hold.
    """
    path = os.path.abspath(path).replace(os.sep, "/")
    if not path.startswith("/"):
        path = "/" + path
    return "file://" + urllib.parse.quote(path, safe="/:") + "?mode=ro"


def _read(path):
    """(uats, localities) of the database at path."""
    conn = sqlite3.connect(read_only_uri(path), uri=True)
    try:
        counties = dict(conn.execute("SELECT county_id, name FROM counties"))
        uats = {}
        for siruta, county_id, name in conn.execute(
                "SELECT siruta, county_id, name FROM admin_units"):
            uats[siruta] = (name, counties.get(county_id, ""))
        localities = dict(conn.execute("SELECT siruta, name FROM localities"))
    finally:
        conn.close()
    return uats, localities


def _load():
    global _tables, _problem
    with _lock:
        if _tables is not None:
            return _tables
        try:
            _tables, _problem = _read(DB_PATH), ""
        except sqlite3.Error:
            _tables, _problem = ({}, {}), UNREADABLE
        return _tables


def retry():
    """After a failed read, make the next lookup read the database again. A run calls it
    before its first lookup, so a database that could not be read during one run (on a
    network share that was unreachable for a moment) is read again by the next run, while
    the lookups of one run make a single attempt."""
    global _tables, _problem
    with _lock:
        if _problem:
            _tables, _problem = None, ""


def problem() -> str:
    """UNREADABLE when the last attempt to read the database failed, else empty."""
    return _problem


def _code(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def uat_name(siruta) -> str:
    """UAT name of a SIRSUP code, empty when unknown."""
    code = _code(siruta)
    if code is None:
        return ""
    return _load()[0].get(code, ("", ""))[0]


def county_name(siruta) -> str:
    """County of a UAT given by SIRSUP code, empty when unknown."""
    code = _code(siruta)
    if code is None:
        return ""
    return _load()[0].get(code, ("", ""))[1]


def locality_name(siruta) -> str:
    """Locality name of a SIRUTA code, empty when unknown. Falls back to the UAT table, since
    an address in the UAT seat may use the UAT's code."""
    code = _code(siruta)
    if code is None:
        return ""
    uats, localities = _load()
    if code in localities:
        return localities[code]
    return uats.get(code, ("", ""))[0]
