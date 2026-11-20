# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The values a dialog was last run with, per dialog and widget.

A value is kept for the QGIS profile or, with project=True, for the open project: a file
path or a column chosen in one project's layers means nothing in another project. Both kinds
live in QgsSettings; a value written into the project would mark the project as modified.

A project without a file keeps its values while it is open, in the QGIS session that wrote
them. They become the values of its file when it is saved, and are dropped when it is closed
(watch_project).
"""

import hashlib
import os
import time
import uuid

from qgis.core import QgsApplication, QgsProject, QgsSettings  # type: ignore

# inside settings_service.GROUP, so reset_all() clears them too
GROUP = "CADex/dialog_state"
PROJECT_GROUP = "CADex/project_state"

# the group of the open project while it has no file
UNSAVED = "unsaved"
# how many projects keep their values; past that, the ones used longest ago are dropped
MAX_PROJECTS = 100
_USED, _SESSION = "_used", "_session"
# the property of the QgsApplication that holds the token of the QGIS session: the same
# after a reload of the plugin, another one in another session
SESSION_MARK = "cadexSession"
_watching = False


def project_id() -> str:
    """The settings group of the open project: a digest of its file path, UNSAVED for a
    project without a file."""
    name = QgsProject.instance().fileName()
    if not name:
        return UNSAVED
    if os.path.isabs(name):
        name = os.path.normcase(os.path.normpath(name))
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def _session() -> str:
    app = QgsApplication.instance()
    token = app.property(SESSION_MARK) if app is not None else None
    if not token:
        token = uuid.uuid4().hex
        if app is not None:
            app.setProperty(SESSION_MARK, token)
    return str(token)


def _drop_unsaved_of_another_session():
    """Drop the values of a project without a file that an earlier QGIS session left: that
    project was closed with QGIS."""
    settings = QgsSettings()
    stamp = settings.value(f"{PROJECT_GROUP}/{UNSAVED}/{_SESSION}")
    if stamp is not None and stamp != _session():
        settings.remove(f"{PROJECT_GROUP}/{UNSAVED}")


def _key(dialog_name: str, name: str, project: bool) -> str:
    if project:
        group = project_id()
        if group == UNSAVED:
            _drop_unsaved_of_another_session()
        return f"{PROJECT_GROUP}/{group}/{dialog_name}/{name}"
    return f"{GROUP}/{dialog_name}/{name}"


def load(dialog_name: str, name: str, default=None, project: bool = False):
    return QgsSettings().value(_key(dialog_name, name, project), default)


def save(dialog_name: str, name: str, value, project: bool = False):
    QgsSettings().setValue(_key(dialog_name, name, project), value)
    if project:
        _mark_used()


def remove(dialog_name: str, name: str, project: bool = False):
    QgsSettings().remove(_key(dialog_name, name, project))


def _mark_used():
    """Stamp the open project's group and drop the groups of the projects used longest ago,
    so the settings do not grow with every project ever opened."""
    settings = QgsSettings()
    group = f"{PROJECT_GROUP}/{project_id()}"
    settings.setValue(f"{group}/{_USED}", time.time())
    if project_id() == UNSAVED:
        settings.setValue(f"{group}/{_SESSION}", _session())
    settings.beginGroup(PROJECT_GROUP)
    try:
        groups = settings.childGroups()

        def used(name):
            try:
                return float(settings.value(f"{name}/{_USED}", 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        for name in sorted(groups, key=used)[:max(len(groups) - MAX_PROJECTS, 0)]:
            settings.remove(name)
    finally:
        settings.endGroup()


def watch_project():
    """Follow the open project, so that the values of a project without a file stay with
    it: forget_unsaved() when it is closed, keep_unsaved_with_file() when it is saved.

    The plugin calls it when it loads, and each window calls it before connecting its own
    QgsProject slots: Qt runs the slots of a signal in the order they were connected, so
    forget_unsaved() runs before a window restores its values for the new project."""
    global _watching
    if _watching:
        return
    _watching = True
    project = QgsProject.instance()
    project.cleared.connect(forget_unsaved)
    project.projectSaved.connect(keep_unsaved_with_file)


def unwatch_project():
    """Undo watch_project(), when the plugin is unloaded."""
    global _watching
    if not _watching:
        return
    _watching = False
    project = QgsProject.instance()
    for signal, slot in ((project.cleared, forget_unsaved),
                         (project.projectSaved, keep_unsaved_with_file)):
        try:
            signal.disconnect(slot)
        except (TypeError, RuntimeError):
            pass  # not connected, or the project is being destroyed


def forget_unsaved(*_args):
    """Drop the values kept for a project without a file: that project has been closed."""
    QgsSettings().remove(f"{PROJECT_GROUP}/{UNSAVED}")


def keep_unsaved_with_file(*_args):
    """Move the values kept while the open project had no file to the group of its file. A
    file that has values of its own, from the project saved there before, keeps those."""
    group = project_id()
    if group == UNSAVED:
        return
    _drop_unsaved_of_another_session()
    settings = QgsSettings()
    settings.beginGroup(PROJECT_GROUP)
    try:
        settings.beginGroup(UNSAVED)
        # without the group's own stamps, which _mark_used() writes for the file
        values = {key: settings.value(key) for key in settings.allKeys()
                  if not key.startswith("_")}
        settings.endGroup()
        if not values:
            return
        settings.beginGroup(group)
        taken = bool(settings.allKeys())
        settings.endGroup()
        if not taken:
            for key, value in values.items():
                settings.setValue(f"{group}/{key}", value)
        settings.remove(UNSAVED)
    finally:
        settings.endGroup()
    if not taken:
        _mark_used()
