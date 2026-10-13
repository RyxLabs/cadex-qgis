# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Optional Python packages, installed with pip into a folder of the QGIS profile after the
user agrees."""

# ezdxf depends on numpy, which QGIS ships; letting pip resolve it can replace QGIS's numpy
# and break startup. So ezdxf goes in with --no-deps plus its pure-Python dependencies, and
# the folder is appended to sys.path so QGIS's own packages take precedence.

import importlib
import os
import sys

from qgis.core import Qgis, QgsApplication, QgsMessageLog  # type: ignore

from ..i18n import tr as _tr

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_NAME = "CADex"


def _libs_dir() -> str:
    # <QGIS profile>/cadex_libs, because plugin updates replace the plugin folder; the
    # plugin's libs/ is only the fallback outside a running QGIS
    profile = QgsApplication.qgisSettingsDirPath() or ""
    if not profile or not os.path.isdir(profile):
        return os.path.join(PLUGIN_DIR, "libs")
    return os.path.join(profile, "cadex_libs")


LIBS_DIR = _libs_dir()

# import name -> (pip name, --no-deps, pure-Python dependencies)
PACKAGES = {
    "ezdxf": ("ezdxf", True, ("pyparsing", "fonttools", "typing_extensions")),
}

# the versions CADex was tested with, raised deliberately with a release; --no-deps skips
# pip's own requirement check, so these have to satisfy ezdxf's requirements
SPECS = {
    "ezdxf": "ezdxf==1.4.3",
    "pyparsing": "pyparsing==3.3.2",
    "fonttools": "fonttools==4.61.1",
    "typing_extensions": "typing_extensions==4.15.0",
}


def _log(message: str, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(message, PLUGIN_NAME, level=level)


def bootstrap_sys_path():
    """Append the package folders to sys.path, once."""
    for path in (LIBS_DIR, os.path.join(PLUGIN_DIR, "libs")):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)


def find_python():
    """The Python interpreter of this QGIS installation, None when not found. On Windows
    sys.executable is qgis.exe, and in the macOS application bundle it is the QGIS binary."""
    from pathlib import Path

    if sys.platform != "win32":
        for candidate in (Path(sys.executable), Path(sys.prefix) / "bin" / "python3"):
            try:
                if candidate.name.lower().startswith("python") and candidate.is_file():
                    return str(candidate)
            except OSError:
                pass
        return None

    exe_dir = Path(sys.executable).parent
    prefix = Path(sys.prefix)
    for candidate in (exe_dir / "python.exe", exe_dir / "python3.exe", prefix / "python.exe",
                      prefix / "python3.exe", prefix / "Scripts" / "python.exe"):
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            pass

    # OSGeo4W and the standalone installer: <root>\bin\qgis-bin.exe with
    # <root>\apps\Python312\python.exe; a build in <root>\apps\qgis\bin\qgis.exe. The folder
    # above <root> (often C:\Program Files) can hold other Pythons, whose wheels would not
    # load in QGIS
    folders = [exe_dir.parent / "apps"]
    if exe_dir.parent.parent.name.lower() == "apps":
        folders.append(exe_dir.parent.parent)
    for apps in folders:
        try:
            if not apps.is_dir():
                continue
            # newest first: Python312 before Python39
            for child in sorted(apps.iterdir(), key=lambda p: (len(p.name), p.name.lower()),
                                reverse=True):
                python = child / "python.exe"
                if child.name.lower().startswith("python3") and python.is_file():
                    return str(python)
        except OSError:
            pass
    # "qgis.exe -m pip" would start a second QGIS instead of pip
    return None


def is_installed(module: str) -> bool:
    return not import_error(module)


def import_error(module: str) -> str:
    """Why module cannot be imported, "" when it can."""
    try:
        importlib.import_module(module)
        return ""
    except Exception as error:
        return f"{error.__class__.__name__}: {error}"


def _run_pip(args, timeout=180):
    """Run pip without a console window on Windows."""
    # runs only pip, with an argument list and no shell (see _pip_install)
    import subprocess  # nosec B404
    # without an explicit encoding, text=True uses the locale codec, and a stray byte in pip's
    # output would raise UnicodeDecodeError after a successful install
    kwargs: dict = dict(capture_output=True, text=True, timeout=timeout,
                        encoding="utf-8", errors="replace")
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = si
    # args is the pip command built in _pip_install from SPECS; no user input reaches it
    return subprocess.run(args, **kwargs)  # nosec B603


def _pip_install(python_exe, pip_name, no_deps, companions):
    """pip install --target into LIBS_DIR. Returns (ok, error message)."""
    base = [python_exe, "-m", "pip", "install", "--quiet",
            "--no-warn-script-location", "--disable-pip-version-check",
            # the GUI waits for pip, so an offline machine fails in seconds
            "--retries", "1", "--timeout", "15",
            # without it pip keeps a package folder already in the target, also a damaged one
            "--upgrade"]
    extra = ["--no-deps"] if no_deps else []
    pkgs = [SPECS.get(n, n) for n in [pip_name] + list(companions)]
    try:
        # an unwritable folder ends in the manual install message, not a traceback
        os.makedirs(LIBS_DIR, exist_ok=True)
        r = _run_pip(base + ["--target", LIBS_DIR] + extra + pkgs)
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or r.stdout or "").strip()
    except Exception as e:
        err = str(e)
    _log(f"pip --target install failed: {err[:200]}", Qgis.MessageLevel.Warning)
    return False, err


def ensure(module: str, parent=None) -> bool:
    """True when module can be imported, after offering to install it if missing."""
    bootstrap_sys_path()
    if is_installed(module):
        return True

    # widget imports stay out of module level, qgis_process loads this module without a GUI
    from qgis.PyQt.QtCore import Qt  # type: ignore
    from qgis.PyQt.QtWidgets import QApplication  # type: ignore
    from ..dialogs import _ui_helpers

    pip_name, no_deps, companions = PACKAGES.get(module, (module, False, ()))
    if companions:
        question = _tr("Doriți să fie instalat din PyPI, împreună cu dependențele lui "
                       "({0}), într-un folder propriu CADex din profilul QGIS?").format(
                           ", ".join(companions))
    else:
        question = _tr("Doriți să fie instalat din PyPI, într-un folder propriu CADex "
                       "din profilul QGIS?")
    if not _ui_helpers.ask(
            parent, _tr("Instalare"),
            _tr("Această funcție are nevoie de pachetul Python '{0}', care nu "
                "este instalat.").format(pip_name)
            + "\n\n" + question + "\n\n"
            + _tr("Instalarea are nevoie de internet și nu modifică instalarea QGIS. "
                  "QGIS rămâne ocupat până se termină descărcarea."),
            _tr("Instalează"), _tr("Anulează")):
        return False

    python_exe = find_python()
    if python_exe is None:
        _log("no QGIS python interpreter found for pip install",
             Qgis.MessageLevel.Warning)
        ok = False
        err = _tr("Nu s-a găsit interpretorul Python al acestei instalări "
                  "QGIS.")
    else:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, err = _pip_install(python_exe, pip_name, no_deps, companions)
        finally:
            QApplication.restoreOverrideCursor()

    bootstrap_sys_path()
    importlib.invalidate_caches()

    problem = import_error(module) if ok else ""
    if ok and not problem:
        return True
    if ok:
        _ui_helpers.show_warning(
            parent, _tr("Eroare"),
            _tr("Pachetul '{0}' a fost instalat în '{1}', dar nu poate fi încărcat:\n\n{2}")
            .format(pip_name, LIBS_DIR, problem[:300]))
        return False
    if sys.platform == "win32":
        hint = _tr("Îl puteți instala manual din OSGeo4W Shell:")
    else:
        hint = _tr("Îl puteți instala manual dintr-un terminal în care "
                   "este activ mediul Python al QGIS:")
    _ui_helpers.show_warning(
        parent, _tr("Eroare"),
        _tr("Pachetul '{0}' nu a putut fi instalat.\n\n{1}\n  python -m pip "
            "install {0}\n\n{2}").format(pip_name, hint, (err or "")[:300]))
    return False
