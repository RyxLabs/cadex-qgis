# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The CADex Processing provider."""

from qgis.core import QgsProcessingProvider  # type: ignore

from .analysis import AreaReductionAlgorithm, IntravilanAlgorithm
from .compare import CompareCgxmlAlgorithm, CompareLayersAlgorithm
from .convert import ConvertCgxmlAlgorithm
from .topology import (
    BuildingTopologyAlgorithm, TopologyCheckAlgorithm, TopologyCheckCgxmlAlgorithm,
)
from .. import icons, plugin_version

ALGORITHMS = (ConvertCgxmlAlgorithm, CompareLayersAlgorithm, CompareCgxmlAlgorithm,
              TopologyCheckAlgorithm, TopologyCheckCgxmlAlgorithm, BuildingTopologyAlgorithm,
              IntravilanAlgorithm, AreaReductionAlgorithm)


class CadexProvider(QgsProcessingProvider):

    def id(self):
        return "cadex"

    def name(self):
        return "CADex"

    def longName(self):
        return "CADex - cadastru și CGXML"

    def icon(self):
        return icons.icon("cadex.svg")

    def versionInfo(self):
        return plugin_version()

    def loadAlgorithms(self):
        for algorithm in ALGORITHMS:
            self.addAlgorithm(algorithm())
