# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Help window: a page per tool, the web tools and the about page."""

import html

from qgis.PyQt.QtCore import QSize, Qt  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import _ui_helpers, theme
from .base_dialog import SCREEN_SHARE, BaseDialog, screen_of
from .. import features, help_texts, plugin_version
from ..features import QT_TRANSLATE_NOOP

# the size of a help window opened for the first time, when the screen has room for it
WIDTH, HEIGHT = 940, 660
# pages without the paragraph on Stereo 70: Conversie CGXML reads only CGXML files, whose
# coordinates are always in Stereo 70, and the other entries are not tools
WITHOUT_STEREO70 = ("convert_cgxml", "open_settings", "open_help", "open_website")

# (name, path on the website, description)
WEB_TOOLS = (
    (QT_TRANSLATE_NOOP("CADex", "Validator CGXML"), "/validator",
     QT_TRANSLATE_NOOP(
         "CADex",
         "Verifică fișierele după schema ANCPI și după regulile de validare din "
         "Generare CG: câmpuri obligatorii lipsă, CNP sau CUI invalid, cote de "
         "proprietate care depășesc întregul, diferențe între suprafețe, valori "
         "neadmise de ANCPI. Raportul arată, pentru fiecare fișier, ce este greșit "
         "și ce este de făcut.")),
    (QT_TRANSLATE_NOOP("CADex", "Comparator"), "/comparator",
     QT_TRANSLATE_NOOP(
         "CADex",
         "Compară o sursă veche cu una nouă: două livrări, două exporturi eTerra sau "
         "un export eTerra cu o livrare. Diferențele sunt listate pentru fiecare "
         "imobil, pe parcele, construcții, persoane și părțile a II-a și a III-a ale "
         "cărții funciare, marcate 'Adăugat', 'Modificat' sau 'Șters'.")),
    (QT_TRANSLATE_NOOP("CADex", "Interogări predefinite"), "/interogari-predefinite",
     QT_TRANSLATE_NOOP(
         "CADex",
         "Listează imobilele, parcelele, construcțiile, persoanele, actele și "
         "înscrierile din fișiere, de exemplu parcelele din intravilan și din "
         "extravilan, construcțiile fără acte sau proprietarii neidentificați. "
         "Orice tabel se poate exporta în Excel.")),
    (QT_TRANSLATE_NOOP("CADex", "Interogări personalizate"), "/interogari-personalizate",
     QT_TRANSLATE_NOOP(
         "CADex",
         "Construiți interogarea: ce se listează, condițiile legate prin ȘI "
         "sau SAU și coloanele. Disponibile doar cu licență.")),
)

ROLE = Qt.ItemDataRole.UserRole


def _rich(text) -> QLabel:
    label = QLabel(theme.colored_links(text))
    label.setOpenExternalLinks(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    label.setWordWrap(True)
    return label


def _scrolling(label) -> QScrollArea:
    """label at the top of a scroll area, for a window made shorter than its text."""
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(label)
    layout.addStretch()
    area = QScrollArea()
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


class HelpDialog(BaseDialog):
    FEATURE = "open_help"
    FIT_HEIGHT = False
    HELP_BUTTON = False
    SCROLL = False

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Ajutor CADex"))
        self.setMinimumSize(640, 460)
        # a saved geometry replaces this on show
        screen = screen_of(self)
        if screen is not None:
            free = screen.availableGeometry()
            self.resize(min(WIDTH, int(free.width() * SCREEN_SHARE)),
                        min(HEIGHT, int(free.height() * SCREEN_SHARE)))
        else:
            self.resize(WIDTH, HEIGHT)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tools_tab(), self.tr("Instrumente"))
        self.tabs.addTab(self._web_tab(), self.tr("CADex Online"))
        self.tabs.addTab(self._about_tab(), self.tr("Despre"))
        self.body.addWidget(self.tabs, 1)
        self.add_close_button().setDefault(True)
        self.tools.setCurrentRow(0)

    def _tools_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tools = QListWidget()
        self.tools.setIconSize(QSize(22, 22))
        self.tools.setSpacing(2)
        for feat in features.CATALOG:
            item = QListWidgetItem(_ui_helpers.plugin_icon(feat.icon),
                                   self.tr(feat.label).rstrip("."))
            item.setData(ROLE, feat.method)
            item.setToolTip(self.tr(feat.hint))
            self.tools.addItem(item)
        self.tools.currentItemChanged.connect(lambda *_: self._show_page())
        splitter.addWidget(self.tools)
        self.page = QTextBrowser()
        self.page.setOpenExternalLinks(True)
        splitter.addWidget(self.page)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 660])
        layout.addWidget(splitter, 1)
        return tab

    def page_html(self, method) -> str:
        feat = next(f for f in features.CATALOG if f.method == method)
        parts = [f"<h2>{html.escape(self.tr(feat.label).rstrip('.'))}</h2>",
                 self.tr(help_texts.TOOLS.get(method, ""))]
        if method in help_texts.PROCESSING:
            group, names = help_texts.PROCESSING[method]
            listed = ", ".join(f"<b>{html.escape(self.tr(n))}</b>" for n in names)
            parts.append("<h4>" + self.tr("În Processing Toolbox") + "</h4><p>"
                         + self.tr("CADex, grupul {0}: {1}. De acolo instrumentul se poate "
                                   "folosi în modele, în rulări în lot și din consola "
                                   "Python.").format(html.escape(self.tr(group)), listed)
                         + "</p>")
        if method not in WITHOUT_STEREO70:
            parts.append("<p>" + html.escape(self.tr(help_texts.STEREO70)) + "</p>")
        return "".join(parts)

    def _show_page(self):
        item = self.tools.currentItem()
        if item is not None:
            self.page.setHtml(self.page_html(item.data(ROLE)))

    def select_tool(self, method):
        """Open the Instrumente tab at the page of the given catalog method."""
        self.tabs.setCurrentIndex(0)
        for i in range(self.tools.count()):
            if self.tools.item(i).data(ROLE) == method:
                self.tools.setCurrentRow(i)
                return

    def _web_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        parts = [
            "<h3>" + self.tr("CADex Online") + "</h3>",
            "<p>" + self.tr(
                "Pe cadex.ro lucrați direct cu fișierele CGXML, în browser, fără "
                "instalare. Fișierele se citesc în browser și rămân pe calculatorul "
                "dumneavoastră. Validatorul și interogările predefinite sunt gratuite "
                "când lucrați cu câte un fișier, iar comparatorul, când comparați un "
                "fișier vechi cu unul nou.") + "</p>",
        ]
        for name, path, text in WEB_TOOLS:
            parts.append(f"<p><b><a href='{features.WEBSITE}{path}'>{self.tr(name)}</a></b><br>"
                         f"{self.tr(text)}</p>")
        parts.append("<p>" + self.tr(
            "Cu licență lucrați pe o livrare întreagă, dintr-un folder sau dintr-o "
            "arhivă ZIP, și folosiți interogările personalizate.")
            + f" <a href='{features.WEBSITE}/preturi'>"
            + self.tr("Prețuri") + "</a></p>")
        layout.addWidget(_scrolling(_rich("".join(parts))), 1)
        row = QHBoxLayout()
        row.addStretch()
        open_btn = QPushButton(self.tr("Deschide cadex.ro"))
        open_btn.setObjectName("cadexPrimary")
        open_btn.clicked.connect(lambda: _ui_helpers.open_url(features.WEBSITE))
        row.addWidget(open_btn)
        layout.addLayout(row)
        return tab

    def _about_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(_scrolling(_rich(
            f"<h2>CADex {plugin_version()}</h2>"
            + "<p>" + self.tr("Instrumente pentru cadastrul sistematic și fișierele "
                              "CGXML, în QGIS.") + "</p>"
            + "<p>" + html.escape(self.tr(help_texts.STEREO70)) + "</p>"
            + "<p>" + self.tr("Instrumentele sunt și în Processing Toolbox, la CADex.")
            + "</p>"
            + "<p>" + self.tr("Autor") + ": RyxLabs "
            "(<a href='https://ryxlabs.dev'>ryxlabs.dev</a>)<br>"
            + self.tr("Suport") + ": <a href='mailto:suport@cadex.ro'>suport@cadex.ro</a></p>"
            + "<p>" + self.tr("Licență: GNU GPL, versiunea 2 sau mai nouă.") + "<br>"
            + f"<a href='{features.SOURCE_URL}'>"
            + self.tr("Codul sursă și raportarea problemelor") + "</a></p>"
            + "<h3>" + self.tr("Tot de la RyxLabs") + "</h3>"
            + f"<p><b><a href='{features.VERNIER_URL}'>Vernier</a></b>: "
            + self.tr("instrumente de editare vectorială pentru QGIS, utile și în "
                      "lucrările de cadastru: acroșare într-un clic, validator "
                      "topologic cu layere de erori, împărțirea unui poligon în "
                      "suprafețe exacte, import DXF/DWG, export DXF și KMZ, copii de "
                      "siguranță automate, cu versiuni. Interfața lui Vernier este în "
                      "limba engleză.") + "</p>")), 1)
        return tab
