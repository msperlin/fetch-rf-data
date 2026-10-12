"""WebDAV access to the Receita Federal public CNPJ share.

The RFB open-data site is a Nextcloud public share.  Files live in monthly
folders (``YYYY-MM``) and are exposed through WebDAV, which gives us file
sizes, ETags and HTTP Range support (needed for resumable downloads).
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from .errors import ConfigError, ShareAccessError
from .progress import BAR_FORMAT

USER_AGENT = "rfbpy/1.0"

# The Receita Federal share sits behind a load balancer whose nodes do not all
# serve the public WebDAV share: PROPFIND from a bad node comes back as an
# empty ``HTTP 200`` (no DAV body, "Connection: close") or a connection reset
# instead of the expected ``207 Multistatus``.  After roughly 20 seconds of
# retrying, a good node is reached and answers with the XML while setting
# keep-alive + session cookies, so the rest of the session works.
PROPFIND_ATTEMPTS = 30
PROPFIND_RETRY_DELAY = 2.0

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

_thread_local = threading.local()

log = logging.getLogger("rfbpy")


class _Interrupted(Exception):
    """Internal signal used to abort a download when a stop event is set."""


def _get_session(auth=None, user_agent=None):
    """Return a per-thread ``requests.Session`` so connections are reused."""

    key = (user_agent, tuple(auth) if auth else ())
    if getattr(_thread_local, "key", None) != key:
        session = requests.Session()
        session.headers.update({"User-Agent": user_agent or USER_AGENT})
        if auth:
            session.auth = auth
        _thread_local.session = session
        _thread_local.key = key
    return _thread_local.session


def _timeout(request_timeout, connect_timeout=None, read_timeout=None):
    """Build a requests timeout (scalar or ``(connect, read)`` tuple)."""

    if connect_timeout is None and read_timeout is None:
        return request_timeout
    return (
        connect_timeout if connect_timeout is not None else request_timeout,
        read_timeout if read_timeout is not None else request_timeout,
    )


def _entry_name(entry):
    return entry["href"].rstrip("/").split("/")[-1]


def _parse_multistatus(text):
    """Parse a WebDAV ``207 Multistatus`` body into a list of entry dicts."""

    soup = BeautifulSoup(text, features="xml")
    entries = []
    for response in soup.find_all("d:response"):
        href_tag = response.find("d:href")
        if href_tag is None:
            continue
        size_tag = response.find("d:getcontentlength")
        mtime_tag = response.find("d:getlastmodified")
        etag_tag = response.find("d:getetag")
        entries.append(
            {
                "href": href_tag.text,
                "is_collection": response.find("d:collection") is not None,
                "size": int(size_tag.text) if size_tag is not None else None,
                "last_modified": mtime_tag.text if mtime_tag is not None else None,
                "etag": (etag_tag.text or "").strip('"') if etag_tag is not None else None,
            }
        )
    return entries


def _propfind(session, url, timeout, attempts=PROPFIND_ATTEMPTS, retry_delay=PROPFIND_RETRY_DELAY):
    """List a WebDAV collection, retrying past the share's flaky nodes.

    Returns a list of dicts with ``href``, ``is_collection``, ``size``,
    ``last_modified`` and ``etag``.
    """

    last_status = None
    last_error = None
    for attempt in range(1, attempts + 1):
        detail = None
        try:
            resp = session.request(
                "PROPFIND", url, headers={"Depth": "1"}, timeout=timeout
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (401, 403, 404):
                raise ShareAccessError(
                    f"Could not read the public share (HTTP {status}) at {url}. "
                    "The share URL/token may have changed."
                ) from exc
            last_error = exc
            detail = f"HTTP error: {exc}"
        except requests.RequestException as exc:
            # Bad nodes also reset the connection during the warm-up period.
            last_error = exc
            detail = f"request error: {exc}"
        else:
            body = resp.content or b""
            if resp.status_code == 207 and body.strip():
                return _parse_multistatus(resp.text)
            last_status = resp.status_code
            last_error = None
            detail = f"HTTP {resp.status_code}, {len(body)} bytes"

        if attempt < attempts:
            log.warning(
                "PROPFIND attempt %d/%d did not return a WebDAV body at %s (%s); "
                "retrying in %.1f s",
                attempt,
                attempts,
                url,
                detail,
                retry_delay,
            )
            time.sleep(retry_delay)

    raise ShareAccessError(
        f"Could not list {url} after {attempts} attempts "
        f"(last status: {last_status}, last error: {last_error}); "
        "the public share appears to be blocking PROPFIND requests. Try again later."
    )


def list_months(
    base_url, request_timeout, auth=None, connect_timeout=None, read_timeout=None, user_agent=None
):
    """Return the sorted ``YYYY-MM`` sub-folder names found under ``base_url``."""

    session = _get_session(auth, user_agent)
    entries = _propfind(
        session, base_url, _timeout(request_timeout, connect_timeout, read_timeout)
    )
    months = [
        _entry_name(entry)
        for entry in entries
        if entry["is_collection"] and _MONTH_RE.match(_entry_name(entry))
    ]
    return sorted(set(months))


def _resolve_folder(session, base_url, month, timeout):
    if month in (None, "", "latest"):
        entries = _propfind(session, base_url, timeout)
        months = [
            _entry_name(entry)
            for entry in entries
            if entry["is_collection"] and _MONTH_RE.match(_entry_name(entry))
        ]
        if months:
            folder = urljoin(base_url, sorted(months)[-1] + "/")
            log.info("Using latest folder: %s", folder)
            return folder, _propfind(session, folder, timeout)
        return base_url, entries

    folder = urljoin(base_url, str(month).strip("/") + "/")
    log.info("Using folder: %s", folder)
    return folder, _propfind(session, folder, timeout)


def get_all_links(
    base_url,
    desired_files,
    request_timeout,
    auth=None,
    month="latest",
    connect_timeout=None,
    read_timeout=None,
    user_agent=None,
):
    """List the ``.zip`` files matching ``desired_files``.

    Kept for convenience; :func:`list_files` + :func:`match_files` give access
    to the matched files and the unmatched requested entries separately.
    """

    files = list_files(
        base_url,
        request_timeout,
        auth=auth,
        month=month,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        user_agent=user_agent,
    )
    matched, _unmatched = match_files(files, desired_files)
    if not matched:
        log.warning("No files matched the desired list.")
        return []

    log.info("Matched %d files:", len(matched))
    for f in matched:
        log.info("  %s (%s bytes)", f["name"], f["size"])
    return matched


def list_files(
    base_url,
    request_timeout,
    auth=None,
    month="latest",
    connect_timeout=None,
    read_timeout=None,
    user_agent=None,
):
    """List every ``.zip`` file in the folder (no filtering), smallest first.

    Convenience wrapper over :func:`list_manifest`; use that one when you also
    need the resolved folder URL and month.
    """

    return list_manifest(
        base_url,
        request_timeout,
        auth=auth,
        month=month,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        user_agent=user_agent,
    )["files"]


def _month_label(folder, month):
    """Resolve the ``YYYY-MM`` label of ``folder`` from the request/folder."""

    if month and _MONTH_RE.match(str(month)):
        return str(month)
    label = unquote(Path(urlparse(folder).path).name)
    return label if _MONTH_RE.match(label) else None


def list_manifest(
    base_url,
    request_timeout,
    auth=None,
    month="latest",
    connect_timeout=None,
    read_timeout=None,
    user_agent=None,
):
    """List a folder and return ``{"folder", "month", "listed_at", "files"}``.

    ``file`` entries carry ``url``/``name``/``size``/``last_modified``/``etag``.
    ``month`` is the resolved ``YYYY-MM`` label (``None`` when it cannot be
    determined) and ``listed_at`` is when the listing was fetched.
    """

    if not base_url:
        raise ConfigError("base_url is required")

    session = _get_session(auth, user_agent)
    timeout = _timeout(request_timeout, connect_timeout, read_timeout)
    folder, entries = _resolve_folder(session, base_url, month, timeout)

    files = []
    for entry in entries:
        if entry["is_collection"]:
            continue
        href = entry["href"]
        if not href.lower().endswith(".zip"):
            continue
        name = unquote(Path(urlparse(href).path).name)
        files.append(
            {
                "url": urljoin(folder, href),
                "name": name,
                "size": entry["size"],
                "last_modified": entry["last_modified"],
                "etag": entry["etag"],
            }
        )

    # Small files first so useful lookup tables arrive early.
    files.sort(key=lambda f: (f["size"] if f["size"] is not None else 0))
    log.info("Found %d zip files", len(files))
    return {
        "folder": folder,
        "month": _month_label(folder, month),
        "listed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": files,
    }


def match_files(files, desired_files):
    """Split ``files`` into ``(matched, unmatched)`` for ``desired_files``.

    Requested entries are matched case-insensitively as substrings of the file
    names.  ``unmatched`` keeps the requested strings that matched no file.
    """

    wanted = [
        (str(d), str(d).strip().lower())
        for d in (desired_files or [])
        if str(d).strip()
    ]
    matched = []
    unmatched = []
    seen = set()
    for original, needle in wanted:
        hits = [f for f in files if needle in f["name"].lower()]
        if not hits:
            unmatched.append(original)
            continue
        for hit in hits:
            if hit["name"] not in seen:
                seen.add(hit["name"])
                matched.append(hit)

    matched.sort(key=lambda f: (f.get("size") if f.get("size") is not None else 0))
    return matched, unmatched


def download_with_retries(
    url,
    dir_zip,
    request_timeout,
    attempts=3,
    backoff=2,
    auth=None,
    expected_size=None,
    resume=True,
    verify_zip=True,
    connect_timeout=None,
    read_timeout=None,
    progress_cb=None,
    show_progress_bar=True,
    stop_event=None,
    name=None,
    user_agent=None,
):
    """Download ``url`` into ``dir_zip`` with retries and resume support.

    Returns ``True`` on success and ``False`` after all attempts fail.  Partial
    ``.part`` files are kept between attempts (and on failure) so a later run
    can resume via an HTTP Range request.
    """

    session = _get_session(auth, user_agent)
    timeout = _timeout(request_timeout, connect_timeout, read_timeout)

    filename = name or unquote(Path(urlparse(url).path).name)
    target = Path(dir_zip) / filename
    temp = target.with_name(target.name + ".part")

    for attempt in range(1, attempts + 1):
        resume_from = 0
        if resume and temp.exists() and temp.stat().st_size > 0:
            resume_from = temp.stat().st_size
            if expected_size is not None:
                if resume_from == expected_size:
                    temp.replace(target)
                    log.info("Resumed completed file: %s", target.name)
                    return True
                if resume_from > expected_size:
                    log.warning("Discarding oversized partial file: %s", temp.name)
                    temp.unlink()
                    resume_from = 0

        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
        try:
            log.info(
                "Downloading (%d/%d): %s%s",
                attempt,
                attempts,
                filename,
                f" (resuming at {resume_from:,} bytes)" if resume_from else "",
            )
            with session.get(url, stream=True, timeout=timeout, headers=headers) as r:
                append = bool(resume_from) and r.status_code == 206
                if not append:
                    r.raise_for_status()

                total = int(r.headers.get("content-length", 0) or 0)
                pbar_total = (resume_from + total) if append else (total or None)
                mode = "ab" if append else "wb"

                pbar = None
                if progress_cb is None and show_progress_bar:
                    pbar = tqdm(
                        total=pbar_total,
                        unit="B",
                        unit_scale=True,
                        desc=filename,
                        leave=False,
                        bar_format=BAR_FORMAT,
                    )
                try:
                    with open(temp, mode) as fh:
                        for chunk in r.iter_content(chunk_size=65536):
                            if stop_event is not None and stop_event.is_set():
                                raise _Interrupted()
                            if not chunk:
                                continue
                            fh.write(chunk)
                            if progress_cb is not None:
                                progress_cb(len(chunk))
                            elif pbar is not None:
                                pbar.update(len(chunk))
                finally:
                    if pbar is not None:
                        pbar.close()

            actual = temp.stat().st_size
            if expected_size is not None and actual != expected_size:
                raise IOError(f"size mismatch: got {actual}, expected {expected_size}")
            if verify_zip and not zipfile.is_zipfile(temp):
                raise IOError("downloaded file is not a valid zip archive")

            temp.replace(target)
            log.info("Saved: %s", target)
            return True

        except _Interrupted:
            log.warning("Interrupted, keeping partial file: %s", temp.name)
            raise
        except Exception as exc:
            response = getattr(exc, "response", None)
            status = response.status_code if response is not None else None
            log.warning("Attempt %d failed for %s: %s", attempt, filename, exc)

            if status is not None and 400 <= status < 500 and status not in (408, 429):
                log.error("Giving up on %s (HTTP %s)", filename, status)
                break

            if attempt < attempts:
                delay = backoff ** (attempt - 1) + random.uniform(0, 1)
                if response is not None:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = max(delay, float(retry_after))
                        except ValueError:
                            pass
                log.info("Retrying %s in %.1f s", filename, delay)
                time.sleep(delay)

    log.error("Failed to download after %d attempts: %s", attempts, filename)
    return False
