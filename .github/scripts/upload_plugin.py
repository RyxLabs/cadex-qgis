# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Upload a release zip to plugins.qgis.org as a new version of the plugin.

Usage: PLUGINS_QGIS_ORG_TOKEN=<token> python .github/scripts/upload_plugin.py <cadex.<version>.zip>

The token is made on the plugin's page of the site, in its Tokens tab; it uploads versions of
that plugin only and is shown once. The request is the one the site's own view takes
(QGIS-Plugins-Website, plugins/views.py, version_create_api): a POST of the form field
"package" to /plugins/api/<package name>/version/add/ with the token as Bearer credential.
The site answers 201 with the new version, which stays unapproved until its security scan
has passed.
"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
import zipfile

SITE = "https://plugins.qgis.org"


def package_name(path: str) -> str:
    """The plugin's folder in the zip, which is its name on the site."""
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()[0].split("/")[0]


def form(path: str) -> tuple:
    """(content type, body) of a multipart form holding the zip as "package"."""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as fp:
        data = fp.read()
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="package"; '
            f'filename="{os.path.basename(path)}"\r\n'
            "Content-Type: application/zip\r\n\r\n").encode("utf-8")
    return (f"multipart/form-data; boundary={boundary}",
            head + data + f"\r\n--{boundary}--\r\n".encode("utf-8"))


def upload(path: str, token: str) -> dict:
    url = f"{SITE}/plugins/api/{package_name(path)}/version/add/"
    content_type, body = form(path)
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        sys.exit(f"{url} answered {error.code}:\n"
                 f"{error.read().decode('utf-8', errors='replace')[:2000]}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not os.environ.get("PLUGINS_QGIS_ORG_TOKEN"):
        sys.exit(__doc__)
    answer = upload(sys.argv[1], os.environ["PLUGINS_QGIS_ORG_TOKEN"])
    print(json.dumps(answer, indent=1))
    if not answer.get("success"):
        sys.exit(1)
