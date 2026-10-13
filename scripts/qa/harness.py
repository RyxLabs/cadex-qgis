# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
# QA driver loaded with qgis --code. Runs ctl/cmd/*.py on the GUI thread; u_*.py also run on a
# second timer, so they work while another command waits in a modal dialog.
import glob
import io
import os
import sys
import time
import traceback

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import QApplication, QAbstractButton, QLabel
from qgis.PyQt.QtTest import QTest
from qgis.core import QgsApplication
import qgis.utils

CTL = os.path.join(os.environ["TEMP"], "cdxqa", "ctl")
CMD = os.path.join(CTL, "cmd")
OUT = os.path.join(CTL, "out")
SHOTS = os.path.join(CTL, "shots")
MSGLOG = os.path.join(CTL, "messages.log")


def _append(text):
    with open(MSGLOG, "a", encoding="utf-8") as fh:
        fh.write(text)


QgsApplication.messageLog().messageReceived.connect(
    lambda m, tag, lvl: _append(f"{time.strftime('%H:%M:%S')} [{tag}] L{int(lvl)}: {m}\n"))
_orig_hook = sys.excepthook


def _hook(etype, value, tb):
    _append("[EXCEPTHOOK] " + "".join(traceback.format_exception(etype, value, tb)) + "\n")
    _orig_hook(etype, value, tb)


sys.excepthook = _hook


def plugin():
    return qgis.utils.plugins.get("cadex")


def shot(widget, name):
    path = os.path.join(SHOTS, name + ".png")
    widget.grab().save(path)
    print("shot:", path)


def click(w):
    QTest.mouseClick(w, Qt.MouseButton.LeftButton)


def later(fn, ms=0):
    QTimer.singleShot(ms, fn)


def modal():
    return QApplication.activeModalWidget()


def pump(ms=200):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def button(root, text):
    return [b for b in root.findChildren(QAbstractButton) if b.text() == text][0]


def labels(root):
    return [lab.text() for lab in root.findChildren(QLabel) if lab.text()]


_state = {"busy": False}
NS = dict(globals())
NS["iface"] = qgis.utils.iface
NS["NS"] = NS


def _run_file(f):
    run = f + ".run"
    try:
        os.replace(f, run)
    except OSError:
        return
    name = os.path.splitext(os.path.basename(f))[0]
    buf = io.StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    nested = _state["busy"]
    _state["busy"] = True
    t0 = time.time()
    try:
        with open(run, encoding="utf-8") as fh:
            exec(compile(fh.read(), name, "exec"), NS)
    except BaseException:
        traceback.print_exc(file=buf)
    finally:
        sys.stdout, sys.stderr = old
        _state["busy"] = nested
    buf.write(f"\n[done in {time.time() - t0:.2f}s]\n")
    tmp = os.path.join(OUT, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    os.replace(tmp, os.path.join(OUT, name + ".txt"))
    os.remove(run)


def _poll(urgent_only):
    try:
        files = sorted(glob.glob(os.path.join(CMD, "*.py")))
        if urgent_only or _state["busy"]:
            files = [f for f in files if os.path.basename(f).startswith("u_")]
        for f in files:
            _run_file(f)
    except Exception:
        _append("[HARNESS] " + traceback.format_exc())


_timer = QTimer()
_timer.timeout.connect(lambda: _poll(False))
_timer.start(250)
_urgent = QTimer()
_urgent.timeout.connect(lambda: _poll(True))
_urgent.start(300)
qgis.utils._cdxqa_timers = (_timer, _urgent)
qgis.utils.iface.mainWindow().showMaximized()
_append(f"{time.strftime('%H:%M:%S')} harness ready\n")
