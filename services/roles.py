# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Name matching that preselects layers and fields by role (imobile, owner, ...)."""

import fnmatch
import re
import unicodedata

# shorter patterns only match whole field names: as fragments "ie" and "id" match too much
MIN_FRAGMENT = 4

_NON_ALNUM = re.compile(r"[^0-9a-z*]+")


def fold(text) -> str:
    """Lower case without diacritics. Romanian data mixes ș/ş and ț/ţ; NFD splits both forms
    into s or t and a mark, which is dropped."""
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    return "".join(c for c in decomposed
                   if unicodedata.category(c) != "Mn").lower()


def normalize(text) -> str:
    """fold() with separators as single underscores: "Suprafata act" -> "suprafata_act"."""
    return _NON_ALNUM.sub("_", fold(text)).strip("_")


def parse_patterns(text) -> list:
    """Normalized patterns from a comma or semicolon separated list, in order."""
    parts = re.split(r"[,;\n]", str(text or ""))
    result = []
    for part in parts:
        norm = normalize(part)
        if norm and norm not in result:
            result.append(norm)
    return result


def _matches(name: str, pattern: str, allow_fragment: bool) -> bool:
    if "*" in pattern:
        return fnmatch.fnmatchcase(name, pattern)
    if name == pattern:
        return True
    return allow_fragment and pattern in name


def layer_matches(layer_name, patterns) -> bool:
    """True when the layer name matches a pattern, also as a fragment."""
    name = normalize(layer_name)
    return any(_matches(name, p, True) for p in patterns)


def pick_field(field_names, patterns):
    """The best matching field or None. Whole-name matches come before fragment matches,
    each in pattern order."""
    normalized = [(normalize(f), f) for f in field_names]
    for pattern in patterns:
        for norm, original in normalized:
            if _matches(norm, pattern, False):
                return original
    for pattern in patterns:
        if len(pattern) < MIN_FRAGMENT and "*" not in pattern:
            continue
        for norm, original in normalized:
            if _matches(norm, pattern, True):
                return original
    return None


# the start of every layer custom property CADex sets
PROPERTY_PREFIX = "cadex/"

# set on the layers a CADex run adds as a report; they never take a role by name
REPORT_PROPERTY = "cadex/report"


def mark_report(layer):
    layer.setCustomProperty(REPORT_PROPERTY, True)


def is_report(layer) -> bool:
    # read from a project file, the value is text
    return str(layer.customProperty(REPORT_PROPERTY, False)).lower() in ("true", "1")


# set on the layers of one Conversie CGXML run, a text that sorts by time; the windows do not
# preselect a run when a later run holds a newer version of its imobile
# (layer_sources.current_conversions)
BATCH_PROPERTY = "cadex/batch"


def new_batch() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


def mark_batch(layer, batch: str):
    layer.setCustomProperty(BATCH_PROPERTY, batch)


def batch_of(layer) -> str:
    return str(layer.customProperty(BATCH_PROPERTY, "") or "")


# set on a converted layer: which output field holds which CGXML column, since the user can
# rename them, e.g. {"id": "id", "nr_constr": "NR_C"}
COLUMNS_PROPERTY = "cadex/columns"


def mark_columns(layer, names):
    """names is [(column key, output field name)]."""
    import json
    layer.setCustomProperty(COLUMNS_PROPERTY, json.dumps(dict(names)))


def _stored_columns(layer) -> dict:
    import json
    try:
        names = json.loads(str(layer.customProperty(COLUMNS_PROPERTY, "") or "{}"))
    except ValueError:
        return {}
    return names if isinstance(names, dict) else {}


def column_of(layer, key):
    """The field holding a CGXML column in a converted layer, None for other layers."""
    name = _stored_columns(layer).get(key)
    return name if name and layer.fields().indexOf(name) >= 0 else None


def columns_of(layer) -> dict:
    """{field: CGXML column} of a converted layer, {} for other layers."""
    fields = layer.fields()
    return {name: key for key, name in _stored_columns(layer).items()
            if name and fields.indexOf(name) >= 0}
