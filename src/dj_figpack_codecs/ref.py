# Copyright 2026 DataJoint Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Lazy reference to figpack visualizations stored in DataJoint OAS.

FigpackRef provides metadata access without downloading the visualization,
and includes methods for loading and displaying the stored view.
"""

from __future__ import annotations

import hashlib
import html
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datajoint.errors import DataJointError

if TYPE_CHECKING:
    from figpack import FigpackView


class FigpackRef:
    """
    Lazy reference to a figpack visualization stored in OAS.

    This class provides metadata access without I/O and methods for
    loading and displaying the stored visualization.

    Attributes
    ----------
    title : str
        Visualization title (from metadata, no I/O).
    description : str
        Visualization description (from metadata, no I/O).
    path : str
        Storage path within the store.
    store : str or None
        Store name (None for default).

    Examples
    --------
    Metadata access without download::

        ref = (RasterPlot & key).fetch1('visualization')
        print(ref.title)        # "Spike Raster" - no download
        print(ref.description)  # "Unit activity..." - no download

    Materialize a servable viewer bundle (e.g. for a dashboard)::

        url = ref.serve_under("assets/serve")

    Jupyter integration::

        ref  # Displays inline in notebook

    ``load()`` / ``show()`` are not yet implemented (issue #3); both raise
    ``NotImplementedError`` pointing at ``serve_under``.
    """

    __slots__ = ("_meta", "_backend")

    def __init__(self, metadata: dict, backend: Any):
        """
        Initialize FigpackRef from metadata and storage backend.

        Parameters
        ----------
        metadata : dict
            JSON metadata containing path, store, title, description.
        backend : StorageBackend
            Storage backend for file operations.
        """
        self._meta = metadata
        self._backend = backend

    @property
    def title(self) -> str:
        """Visualization title (no I/O required)."""
        return self._meta.get("title", "")

    @property
    def description(self) -> str:
        """Visualization description (no I/O required)."""
        return self._meta.get("description", "")

    @property
    def path(self) -> str:
        """Storage path within the store."""
        return self._meta["path"]

    @property
    def store(self) -> str | None:
        """Store name (None for default store)."""
        return self._meta.get("store")

    def serve_under(self, base_dir) -> str:
        """Publish the stored bundle under ``base_dir`` and return its URL.

        Downloads the stored object — the whole figpack bundle, viewer included — into
        ``base_dir/<id>/`` and returns the leading-"/" relative URL ``/<id>/index.html``.
        Nothing is assembled and ``figpack`` need not be installed. ``<id>`` is a stable hash of the ref's schema-addressed
        OAS path — no I/O to compute, distinct primary keys map to distinct directories.

        Idempotent and concurrency-tolerant: if ``index.html`` already exists the build
        is skipped. The build is staged in a temp dir and published with ``os.replace``
        so a half-written bundle is never served; when two workers race to materialize
        the same figure, the loser detects the winner's completed bundle and treats it
        as success (the bundles are byte-equivalent). Every call refreshes the directory
        mtime so a dashboard's TTL-based asset eviction (which evicts by the dir's own
        mtime) does not sweep an in-use figure.

        This is the ``MaterializableRef`` seam consumed by dash-datajoint-components'
        ``PlotGrid`` (structural typing — no import of the dashboard package here).
        """
        base_dir = Path(base_dir)
        fig_id = hashlib.sha1(self.path.encode("utf-8")).hexdigest()[:16]
        dest = base_dir / fig_id
        index = dest / "index.html"

        if not index.exists():
            base_dir.mkdir(parents=True, exist_ok=True)

            # Best-effort sweep of staging dirs orphaned by a killed process (SIGKILL/
            # OOM between mkdtemp and publish) — they are dot-prefixed, so TTL sweepers
            # keyed on published dirs never evict them.
            for stale in base_dir.glob(f".{fig_id}-*"):
                try:
                    if time.time() - stale.stat().st_mtime > 3600:
                        shutil.rmtree(stale, ignore_errors=True)
                except OSError:
                    pass

            tmp = Path(tempfile.mkdtemp(dir=str(base_dir), prefix=f".{fig_id}-"))
            try:
                # The stored object is the whole bundle, so serving is a download.
                # Nothing here knows figpack's internal layout: a custom view may name
                # its Zarr folder differently or carry several, and an extension view
                # brings JavaScript that exists only in the bundle.
                full_path = self._backend._full_path(self.path)
                if self._backend.protocol == "file":
                    shutil.copytree(full_path, tmp, dirs_exist_ok=True)
                else:
                    self._backend.fs.get(full_path, str(tmp), recursive=True)

                # fsspec's recursive get lands contents *as* dst only when dst does not
                # pre-exist; tmp does. Fail loud rather than publish a bundle with no
                # entry point, which would serve as an unexplained 404.
                if not (tmp / "index.html").exists():
                    raise DataJointError(
                        f"figure download produced an unexpected layout under {tmp} "
                        f"(no index.html) — fsspec recursive-get semantics may have "
                        f"changed for protocol {self._backend.protocol!r}, or the "
                        f"stored object predates bundle storage (issue #7)"
                    )

                try:
                    if dest.exists() and not index.exists():
                        shutil.rmtree(dest)  # stale partial build (no index.html)
                    os.replace(tmp, dest)  # publish: index.html appears only complete
                except OSError:
                    if index.exists():
                        # A concurrent worker published the same figure first (e.g.
                        # os.replace ENOTEMPTY). Its bundle is byte-equivalent — treat
                        # as success and discard our staging copy.
                        shutil.rmtree(tmp, ignore_errors=True)
                    else:
                        raise
            except Exception:
                shutil.rmtree(tmp, ignore_errors=True)
                raise

        try:
            os.utime(dest)  # dir-level touch: TTL eviction goes by the dir's own mtime
        except FileNotFoundError:
            pass  # concurrent rebuild swapped the dir this instant; next call repairs
        return f"/{fig_id}/index.html"

    def show(self, *, open_browser: bool = True) -> str:
        """Serve this figure over HTTP and return its URL.

        A figpack viewer fetches its Zarr chunks with HTTP range requests, so
        opening ``index.html`` from the filesystem does not work — the bundle has to
        be served. The server runs on an ephemeral port in a daemon thread and lives
        as long as the process, which suits a notebook or a script; nothing has to be
        cleaned up.

        Parameters
        ----------
        open_browser : bool, optional
            Open the URL in the default browser. Default True.

        Returns
        -------
        str
            The figure's URL.
        """
        import functools
        import http.server
        import socketserver
        import threading
        import webbrowser

        served = Path(tempfile.mkdtemp(prefix="figpack-show-"))
        url_path = self.serve_under(served)

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
        # 0 = ephemeral port, so concurrent shows never collide
        httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        url = f"http://127.0.0.1:{httpd.server_address[1]}{url_path}"
        if open_browser:
            webbrowser.open(url)
        return url

    def _repr_html_(self) -> str:
        """
        HTML representation for Jupyter notebooks.

        Returns an informative HTML snippet showing metadata.
        Loading the full visualization requires calling show() or load().
        """
        # title/description originate in user-controlled figure metadata —
        # escape before interpolating into notebook HTML.
        title_html = (
            f"<strong>{html.escape(self.title)}</strong>" if self.title else "<em>Untitled</em>"
        )
        desc_html = html.escape(
            self.description[:200] + "..." if len(self.description) > 200 else self.description
        )
        return f"""
        <div style="border: 1px solid #ccc; padding: 10px; border-radius: 5px; max-width: 400px;">
            <div style="font-size: 14px; margin-bottom: 5px;">{title_html}</div>
            <div style="font-size: 12px; color: #666; margin-bottom: 8px;">{desc_html}</div>
            <div style="font-size: 11px; color: #999;">
                FigpackRef | <code>.show()</code> to display | <code>.serve_under()</code> to publish
            </div>
        </div>
        """

    def __repr__(self) -> str:
        title_preview = f'"{self.title[:30]}..."' if len(self.title) > 30 else f'"{self.title}"'
        return f"FigpackRef(title={title_preview})"

    def __str__(self) -> str:
        return repr(self)
