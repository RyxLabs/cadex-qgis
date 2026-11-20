# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""CGXML sources (.cgxml files, ZIP archives, folders) and reading them file by file."""

import errno
import os
import re
import zipfile
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from . import cgxml_reader

EXTENSION = ".cgxml"
_QUOTED = re.compile(r'"([^"]+)"')

# read_each() reads up to READ_AHEAD files of a folder ahead of the one it parses, on READERS
# threads: opening a file for the first time waits several milliseconds for the antivirus
# scan, longer than parsing it takes. A file over READ_AHEAD_BYTES is read when its turn
# comes.
READ_AHEAD = 32
READERS = 8
READ_AHEAD_BYTES = 4 * 1024 * 1024
# a ZIP member is inflated this much at a time
_PIECE = 1024 * 1024


def split_paths(text) -> list:
    """One path as typed, or several in double quotes: "a.cgxml" "b.cgxml"."""
    text = (text or "").strip()
    if not text:
        return []
    quoted = _QUOTED.findall(text)
    if quoted:
        return [p.strip() for p in quoted if p.strip()]
    return [text]


def join_paths(paths) -> str:
    """The inverse of split_paths()."""
    paths = [p for p in paths if p]
    if len(paths) == 1:
        return paths[0]
    return " ".join(f'"{p}"' for p in paths)


def _as_list(paths):
    return split_paths(paths) if isinstance(paths, str) else [p for p in paths if p]


def _mac_metadata(name) -> bool:
    """The "._name" metadata files and the __MACOSX folder that macOS adds to copies and
    ZIPs."""
    parts = name.replace("\\", "/").split("/")
    return parts[-1].startswith("._") or "__MACOSX" in parts


def is_source(path) -> bool:
    return os.path.isdir(path) or path.lower().endswith((EXTENSION, ".zip"))


class Unreadable(tuple):
    """A list_sources() entry for a chosen path that gives no files: a ZIP archive or folder
    that cannot be listed, a path that does not exist, a file of another kind. It is shaped
    like the others, (display name, path, None). problem is the Romanian reason read_each()
    gives for it, error the exception, if any."""

    def __new__(cls, display, path, problem, error=None):
        entry = super().__new__(cls, (display, path, None))
        entry.problem = problem
        entry.error = error
        return entry


DAMAGED_ARCHIVE = "arhiva ZIP este deteriorată sau incompletă"
ARCHIVE_IN_USE = ("arhiva ZIP nu poate fi deschisă: este folosită de alt program sau nu aveți "
                  "drept de acces la ea")
MISSING_PATH = "calea nu a fost găsită"
NOT_A_SOURCE = "nu este un folder, o arhivă ZIP sau un fișier .cgxml"


def unreadable_text(sources) -> str:
    """'name' (reason) of each Unreadable entry of list_sources(), "" when there is none."""
    return ", ".join(f"'{source[0]}' ({source.problem})" for source in sources
                     if isinstance(source, Unreadable))


# (missing, in use or not allowed, other reason) for a file, an archive and a folder
_OPEN_PROBLEMS = {
    "file": ("fișierul nu a fost găsit",
             "fișierul nu poate fi deschis: este folosit de alt program sau nu aveți drept de "
             "acces la el",
             "fișierul nu poate fi deschis: {0}"),
    "zip": ("arhiva ZIP nu a fost găsită", ARCHIVE_IN_USE,
            "arhiva ZIP nu poate fi deschisă: {0}"),
    "folder": ("folderul nu a fost găsit",
               "folderul nu poate fi citit: nu aveți drept de acces la el",
               "folderul nu poate fi citit: {0}"),
}


def _open_problem(error, kind) -> str:
    """The Romanian reason an OSError gives for a "file", "zip" or "folder"."""
    missing, denied, other = _OPEN_PROBLEMS[kind]
    if isinstance(error, FileNotFoundError):
        return missing
    if isinstance(error, PermissionError):
        return denied
    return other.format(getattr(error, "strerror", None) or str(error)
                        or error.__class__.__name__)


def _never():
    return False


def list_sources(paths, recursive=True, stop=_never):
    """[(display name, path, zip member or None)] of every .cgxml in the sources, sorted.

    paths is a path or a list of them. With several roots the display name starts with the
    root's name, so equal file names in two folders stay apart. The other files of a folder
    are skipped. A chosen path that does not exist, a chosen file of another kind, a damaged
    or locked archive and a chosen folder that cannot be listed are one Unreadable entry
    each, so the other sources are still read. A subfolder that cannot be listed is skipped:
    every NTFS drive holds System Volume Information and every Documents folder the hidden
    My Music link, and a user's account may list neither. When stop() turns true the listing
    ends with what it has found.
    """
    roots = _as_list(paths)
    prefix = len(roots) > 1
    sources = []
    seen = set()

    def add(display, path, member):
        marker = (os.path.normcase(os.path.abspath(path)), member)
        if marker not in seen:
            seen.add(marker)
            sources.append((display, path, member))

    def unreadable(display, path, problem, error):
        marker = (os.path.normcase(os.path.abspath(path)), None)
        if marker not in seen:
            seen.add(marker)
            sources.append(Unreadable(display, path, problem, error))

    for root in roots:
        if stop():
            break
        label = os.path.basename(root.rstrip("/\\")) or root
        if os.path.isfile(root) and root.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(root) as archive:
                    members = [info.filename for info in archive.infolist()
                               if not info.is_dir()
                               and info.filename.lower().endswith(EXTENSION)
                               and not _mac_metadata(info.filename)]
            except OSError as error:
                unreadable(label, root, _open_problem(error, "zip"), error)
                continue
            except (zipfile.BadZipFile, EOFError, ValueError) as error:
                unreadable(label, root, DAMAGED_ARCHIVE, error)
                continue
            for member in members:
                add(f"{label}/{member}" if prefix else member, root, member)
        elif os.path.isfile(root) and root.lower().endswith(EXTENSION):
            add(os.path.basename(root), root, None)
        elif os.path.isdir(root):
            failed = []
            chosen = os.path.normcase(os.path.abspath(root))

            def refused(error):
                # os.walk calls this for every folder it cannot list; only the chosen folder
                # is reported
                if os.path.normcase(os.path.abspath(error.filename or root)) == chosen:
                    failed.append(error)

            if recursive:
                walked = []
                for folder, dirs, files in os.walk(root, onerror=refused):
                    if stop():
                        break
                    dirs.sort()
                    walked.extend(os.path.join(folder, name) for name in files
                                  if name.lower().endswith(EXTENSION)
                                  and not _mac_metadata(name))
            else:
                try:
                    walked = [os.path.join(root, name) for name in os.listdir(root)
                              if name.lower().endswith(EXTENSION)
                              and not _mac_metadata(name)
                              and os.path.isfile(os.path.join(root, name))]
                except OSError as error:
                    walked = []
                    failed.append(error)
            for error in failed:
                unreadable(label, root, _open_problem(error, "folder"), error)
            for full in walked:
                relative = os.path.relpath(full, root)
                add(os.path.join(label, relative) if prefix else relative, full, None)
        elif os.path.exists(root):
            unreadable(label, root, NOT_A_SOURCE, None)
        else:
            kind = next((k for k, ending in (("zip", ".zip"), ("file", EXTENSION))
                         if root.lower().endswith(ending)), None)
            unreadable(label, root, _OPEN_PROBLEMS[kind][0] if kind else MISSING_PATH,
                       FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), root))
    sources.sort(key=lambda s: cgxml_reader._natural_key(s[0]))
    return sources


def file_name(source) -> str:
    """The file name of a list_sources() entry, without folders."""
    _display, path, member = source
    return os.path.basename((member or path).replace("\\", "/").rstrip("/"))


def read_source(source, archives):
    """(imobile, constructii, warnings) of one list_sources() entry. archives caches open
    ZIP files by path; the caller closes them."""
    if isinstance(source, Unreadable):
        raise cgxml_reader.CgxmlError(source.problem)
    display, path, member = source
    if member is None:
        return cgxml_reader.read_file(path, display)
    archive = archives.get(path)
    if archive is None:
        archive = zipfile.ZipFile(path)
        archives[path] = archive
    return cgxml_reader.parse_bytes(_member_bytes(archive, archive.getinfo(member)), display)


def _member_bytes(archive, info) -> bytes:
    """The content of a ZIP member, read _PIECE bytes at a time: ZipFile.read() inflates a
    member whose header understates its size in full, up to 2 GB, before it cuts the content
    to the declared size."""
    if info.file_size > cgxml_reader.MAX_FILE_BYTES:
        raise cgxml_reader.CgxmlError(
            f"fișier prea mare ({info.file_size // (1024 * 1024)} MB)")
    pieces = []
    with archive.open(info) as member:
        piece = member.read(_PIECE)
        while piece:
            pieces.append(piece)
            piece = member.read(_PIECE)
    return b"".join(pieces)


def _file_bytes(path):
    """The content of a file read ahead, None for one over READ_AHEAD_BYTES. Runs on a reader
    thread."""
    if os.path.getsize(path) > READ_AHEAD_BYTES:
        return None
    with open(path, "rb") as fp:
        return fp.read()


def read_each(sources, stop=_never, progress=None):
    """Yield (source, parsed or None, problem or None) for each source, in their order.

    An unreadable file yields its reason and the others are still read. progress receives
    0 to 1. The files of a folder are read by a few threads ahead of the one being parsed,
    which is parsed on the caller's thread.
    """
    archives = {}
    total = max(len(sources), 1)
    readers = ThreadPoolExecutor(READERS, thread_name_prefix="CADex CGXML")
    following = iter(sources)
    # (source, the future of its content or None for a source read when its turn comes)
    ahead = deque()

    def read_ahead():
        while len(ahead) < READ_AHEAD:
            source = next(following, None)
            if source is None:
                return
            plain = not isinstance(source, Unreadable) and source[2] is None
            ahead.append((source, readers.submit(_file_bytes, source[1]) if plain else None))

    try:
        done = 0
        read_ahead()
        while ahead:
            if stop():
                return
            source, content = ahead.popleft()
            read_ahead()
            try:
                data = content.result() if content is not None else None
                if data is None:
                    result = read_source(source, archives)
                else:
                    result = cgxml_reader.parse_bytes(data, source[0])
            except cgxml_reader.CgxmlError as e:
                yield source, None, str(e)
            except (zipfile.BadZipFile, zlib.error):
                # a member whose compressed data or checksum is damaged
                yield source, None, "fișierul din arhivă este deteriorat"
            except RuntimeError as e:
                # zipfile raises this for an encrypted member
                yield source, None, f"nu s-a putut citi din arhivă ({e})"
            except OSError as e:
                yield source, None, _open_problem(e, "zip" if source[2] is not None else "file")
            except Exception as e:
                yield source, None, f"nu s-a putut citi ({e})"
            else:
                yield source, result, None
            done += 1
            if progress is not None:
                progress(done / total)
    finally:
        for _source, content in ahead:
            if content is not None:
                content.cancel()
        readers.shutdown(wait=True)
        for archive in archives.values():
            archive.close()
