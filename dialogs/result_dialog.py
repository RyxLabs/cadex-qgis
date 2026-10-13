# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""End-of-run window: summary, counts, notes, and buttons to open the file or zoom to the
result layers."""

import math
import os

from qgis.PyQt.QtCore import Qt, QTimer  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QToolButton, QVBoxLayout, QWidget,
)
from qgis.core import (  # type: ignore
    QgsCoordinateTransform, QgsCsException, QgsProject, QgsRectangle,
)

from . import _ui_helpers, theme
from .base_dialog import BodyScroll, fit_to_screen
from ..i18n import tr as _tr

# margin around a result too small to zoom to, such as a single point
ZOOM_MARGIN_M = 20.0
# results QGIS has loaded already; Windows usually has no program associated with them, so
# their folder is opened instead
GIS_FILES = (".gpkg", ".shp", ".sqlite", ".geojson")


def layers_extent(layers, target_crs):
    """The combined extent of the layers in target_crs, None when none has an extent."""
    project = QgsProject.instance()
    extent = None
    for layer in layers:
        try:
            box = layer.extent()
            crs = layer.crs()
        except RuntimeError:
            continue  # layer already deleted
        corners = (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())
        if box.isNull() or not all(math.isfinite(v) for v in corners):
            continue
        if crs.isValid() and target_crs.isValid() and crs != target_crs:
            try:
                box = QgsCoordinateTransform(crs, target_crs, project).transformBoundingBox(box)
            except QgsCsException:
                continue
        if extent is None:
            extent = QgsRectangle(box)
        else:
            extent.combineExtentWith(box)
    return extent


class ResultDialog(QDialog):

    def __init__(self, parent, title, headline, *, counts=(), notes=(), file_path=None,
                 details=None, warning=False, layers=(), iface=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(theme.dialog_stylesheet(theme.tokens()))
        self.setMinimumWidth(460)
        self._file_path = file_path
        self._layers = [layer for layer in layers if layer is not None]
        self._iface = iface

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("cadexBrandBar")
        outer.addWidget(bar)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(18, 14, 18, 8)
        body.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(_ui_helpers.theme_icon(
            "/mIconWarning.svg" if warning else "/mIconSuccess.svg").pixmap(28, 28))
        top.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        self.headline = QLabel(headline)
        self.headline.setWordWrap(True)
        font = self.headline.font()
        font.setBold(True)
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() + 2)
        self.headline.setFont(font)
        texts.addWidget(self.headline)
        text = QLabel(_ui_helpers.result_html("", counts, notes))
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        texts.addWidget(text)
        top.addLayout(texts, 1)
        body.addLayout(top)
        self.text = text

        self.details = None
        if details:
            toggle = QToolButton()
            toggle.setText(_tr("Afișează detaliile"))
            toggle.setCheckable(True)
            toggle.setAutoRaise(True)
            toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            toggle.setArrowType(Qt.ArrowType.RightArrow)
            self.details = QPlainTextEdit(details)
            self.details.setReadOnly(True)
            self.details.setMinimumHeight(140)
            self.details.setVisible(False)

            def flip(on):
                toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
                toggle.setText(_tr("Ascunde detaliile") if on else _tr("Afișează detaliile"))
                self.details.setVisible(on)
                QTimer.singleShot(0, self._fit)

            toggle.toggled.connect(flip)
            body.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
            body.addWidget(self.details, 1)
        # a long list of notes on a small or scaled screen scrolls, the buttons stay in view
        self._scroll = BodyScroll()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(content)
        outer.addWidget(self._scroll, 1)

        buttons = QDialogButtonBox()
        buttons.setContentsMargins(18, 4, 18, 14)
        self.open_button = None
        if file_path and os.path.exists(file_path):
            target = file_path
            if os.path.isfile(file_path) and file_path.lower().endswith(GIS_FILES):
                target = os.path.dirname(file_path)
            label = (_tr("Deschide fișierul") if os.path.isfile(target)
                     else _tr("Deschide folderul"))
            self.open_button = buttons.addButton(label, QDialogButtonBox.ButtonRole.ActionRole)
            self.open_button.setIcon(_ui_helpers.theme_icon("/mActionFileOpen.svg"))
            self.open_button.clicked.connect(lambda: _ui_helpers.open_path(target))
        self.zoom_button = None
        if self._layers and iface is not None:
            self.zoom_button = buttons.addButton(_tr("Arată pe hartă"),
                                                 QDialogButtonBox.ButtonRole.ActionRole)
            self.zoom_button.setIcon(_ui_helpers.theme_icon("/mActionZoomToLayer.svg"))
            self.zoom_button.setToolTip(_tr("Încadrează rezultatele pe hartă și închide "
                                            "fereastra"))
            self.zoom_button.clicked.connect(self._zoom)
        # the reject role puts Închide last on every platform and binds it to Escape
        close = QPushButton(_tr("Închide"))
        buttons.addButton(close, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.accept)
        outer.addWidget(buttons)
        # Enter closes the window: the action buttons never take the default when they get
        # the focus, and the default is set once the button is in the dialog, or it is lost
        for action in (self.open_button, self.zoom_button):
            if action is not None:
                action.setAutoDefault(False)
        close.setDefault(True)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit)

    def _fit(self):
        fit_to_screen(self, self._scroll)

    def _zoom(self):
        canvas = self._iface.mapCanvas()
        target = canvas.mapSettings().destinationCrs()
        extent = layers_extent(self._layers, target)
        if extent is not None:
            # about 20 m on a canvas in degrees
            margin = ZOOM_MARGIN_M / 111000.0 if target.isGeographic() else ZOOM_MARGIN_M
            if extent.width() < margin and extent.height() < margin:
                extent.grow(margin)
            else:
                extent.scale(1.1)
            canvas.setExtent(extent)
            canvas.refresh()
        self.accept()
