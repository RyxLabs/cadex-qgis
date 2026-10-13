# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Base class of the tools' background tasks.

The dialogs run a task in the task manager; a Processing algorithm calls run() on its own
thread and connects its feedback through stop_hook and progress_hook.
"""

import os
import traceback

from qgis.core import Qgis, QgsMessageLog, QgsTask  # type: ignore

from . import outputs


def log(message, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(message, "CADex", level=level)


def file_error(path, error) -> str:
    """The Romanian sentence for a file or folder that a task could not open and that is not
    one of its outputs, usually an input."""
    noun = "Folderul" if os.path.isdir(path) else "Fișierul"
    name = os.path.basename(os.path.normpath(path)) or path
    if isinstance(error, FileNotFoundError):
        return f"{noun} '{name}' nu a fost găsit."
    if isinstance(error, PermissionError):
        return (f"{noun} '{name}' nu poate fi deschis: este folosit de alt program sau nu "
                "aveți drept de acces la el.")
    reason = getattr(error, "strerror", None) or str(error) or error.__class__.__name__
    return f"{noun} '{name}' nu poate fi deschis: {reason}."


class CadexTask(QgsTask):

    def __init__(self, description, finished_cb=None):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self.finished_cb = finished_cb
        self.error_msg = None
        # the Romanian reason the report file was not written; the run's results stand
        self.report_error = None
        # a Processing feedback's isCanceled and setProgress
        self.stop_hook = None
        self.progress_hook = None

    def stopped(self) -> bool:
        return self.isCanceled() or (self.stop_hook is not None and bool(self.stop_hook()))

    def report(self, percent):
        self.setProgress(percent)
        if self.progress_hook is not None:
            self.progress_hook(percent)

    def output_paths(self) -> list:
        """The files the task writes: excel_path and gpkg_path when set. A task with other
        outputs returns those as well, so that failed() reports them as files it cannot write."""
        return [path for path in (getattr(self, "excel_path", None),
                                  getattr(self, "gpkg_path", None)) if path]

    def failed(self, what, error) -> bool:
        """Log the traceback, keep the message for the user, return False for run(). A file
        the task cannot write or open gets a Romanian sentence in place of the operating
        system's text: outputs.write_error for its outputs, file_error for any other file."""
        log(f"{what}: {error}\n{traceback.format_exc()}", Qgis.MessageLevel.Critical)
        # os.replace names the source first and the destination, the user's file, second
        path = (getattr(error, "filename2", None) or getattr(error, "filename", None)
                if isinstance(error, OSError) else None)
        if not path:
            self.error_msg = str(error) or error.__class__.__name__
        elif self._is_output(str(path)):
            self.error_msg = outputs.write_error(str(path), error)
        else:
            self.error_msg = file_error(str(path), error)
        return False

    def _is_output(self, path) -> bool:
        def key(name):
            return os.path.normcase(os.path.abspath(name))
        written = {key(part) for output in self.output_paths()
                   for part in [output] + outputs.parts(output)}
        return key(path) in written

    def write_report(self, write, path) -> bool:
        """Call write(), which writes the report file path. When the file cannot be written,
        keep the reason in report_error and return False: the run's other results stand."""
        try:
            write()
            return True
        except OSError as error:
            self.report_error = outputs.write_error(path, error)
            log(f"{path}: {error}", Qgis.MessageLevel.Warning)
            return False

    def finished(self, result):
        if self.finished_cb is not None:
            self.finished_cb(result, self)
