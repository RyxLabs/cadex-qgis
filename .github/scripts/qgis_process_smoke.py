# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Install the release zip into a throwaway QGIS profile, enable the plugin for qgis_process
and check that qgis_process lists the CADex algorithms, in the version of the zip.

Usage: python .github/scripts/qgis_process_smoke.py <cadex.<version>.zip> <qgis_process>

<qgis_process> is the command itself on Linux and the qgis_process-qgis*.bat of the QGIS
installation on Windows.
"""

import configparser
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ALGORITHMS = ["cadex:areareduction", "cadex:buildingtopology", "cadex:cgxmlconvert",
              "cadex:comparecgxml", "cadex:comparelayers", "cadex:intravilanreport",
              "cadex:topologycheck", "cadex:topologycheckcgxml"]


def zip_version(path: str) -> str:
    with zipfile.ZipFile(path) as zf:
        text = zf.read("cadex/metadata.txt").decode("utf-8")
    parser = configparser.ConfigParser()
    parser.read_file(io.StringIO(text))
    return parser.get("general", "version")


def qgis_process(command: str, profile: str, *arguments) -> str:
    """The standard output of a qgis_process run on the profile; stops on a failed run."""
    environment = dict(os.environ, QGIS_CUSTOM_CONFIG_PATH=profile)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    done = subprocess.run([command] + list(arguments), env=environment, capture_output=True)
    output = done.stdout.decode("utf-8", errors="replace")
    if done.returncode != 0:
        sys.exit(f"qgis_process {' '.join(arguments)} ended with exit code {done.returncode}:\n"
                 f"{output}\n{done.stderr.decode('utf-8', errors='replace')}")
    return output


def main(package: str, command: str) -> None:
    profile = tempfile.mkdtemp(prefix="cadex_smoke_")
    try:
        with zipfile.ZipFile(package) as zf:
            zf.extractall(os.path.join(profile, "profiles", "default", "python", "plugins"))
        print(qgis_process(command, profile, "plugins", "enable", "cadex"))
        listing = qgis_process(command, profile, "--json", "list")
        # qgis_process may print notices before the JSON document
        provider = json.loads(listing[listing.index("{"):])["providers"].get("cadex")
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    if provider is None:
        sys.exit("qgis_process lists no CADex provider")
    found = sorted(provider["algorithms"])
    print(f"CADex {provider.get('version')}: " + ", ".join(found))
    if found != ALGORITHMS:
        sys.exit(f"expected the algorithms {', '.join(ALGORITHMS)}")
    if provider.get("version") != zip_version(package):
        sys.exit(f"the provider's version is not {zip_version(package)}, the version of the zip")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
