# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""CADex - Romanian cadastre and CGXML tools for QGIS."""

import configparser
import os


def plugin_version() -> str:
    """The version in metadata.txt, "?" when it cannot be read."""
    parser = configparser.ConfigParser()
    try:
        parser.read(os.path.join(os.path.dirname(__file__), "metadata.txt"), encoding="utf-8")
        return parser.get("general", "version", fallback="?")
    except (configparser.Error, OSError):
        return "?"


def classFactory(iface):
    # the optional packages (ezdxf) are installed in a pip --target folder that has to be on
    # sys.path before any module imports them
    from .services import deps
    deps.bootstrap_sys_path()
    from .cadex import Cadex
    return Cadex(iface)
