# Copyright 2026 DataJoint Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Lazy reference to figpack visualizations stored in DataJoint OAS.

FigpackRef provides metadata access without downloading the visualization,
and includes methods for loading and displaying the stored view.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

    Explicit loading::

        view = ref.load()  # Downloads and returns FigpackView

    Direct display::

        ref.show()  # Downloads and displays in browser

    Jupyter integration::

        ref  # Displays inline in notebook
    """

    __slots__ = ("_meta", "_backend", "_cached")

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
        self._cached: FigpackView | None = None

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

    @property
    def is_loaded(self) -> bool:
        """True if visualization has been downloaded and cached."""
        return self._cached is not None

    def load(self) -> "FigpackView":
        """
        Download and return the FigpackView.

        Returns
        -------
        FigpackView
            The reconstructed figpack visualization.

        Notes
        -----
        The view is cached after first load. Subsequent calls return
        the cached instance.

        Examples
        --------
        Load and manipulate::

            view = ref.load()
            # Modify view or extract data
        """
        if self._cached is not None:
            return self._cached

        import shutil
        import tempfile
        from pathlib import Path

        from figpack import view_figure

        # Download Zarr folder to temporary location
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "figure.zarr"

            if self._backend.protocol == "file":
                # Local filesystem - copy directly
                remote_path = self._backend._full_path(self.path)
                shutil.copytree(remote_path, local_path)
            else:
                # Remote storage - download folder
                self._backend.get_folder(self.path, str(local_path))

            # Load the figpack view from Zarr folder
            # figpack's view_figure function loads from a path
            self._cached = view_figure(str(local_path))

        return self._cached

    def serve_under(self, base_dir) -> str:
        """Materialize a servable viewer bundle under ``base_dir`` and return its URL.

        Assembles ``base_dir/<id>/`` = figpack's viewer dist (shipped inside the
        installed ``figpack`` package) + this ref's ``data.zarr`` (downloaded from the
        store) + an empty extension manifest, then returns the leading-"/" relative URL
        ``/<id>/index.html``. ``<id>`` is a stable hash of the ref's schema-addressed
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
        import figpack  # the viewer dist ships inside the figpack package

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
                # 1) figure data (schema-addressed zarr) from the store
                zarr_dest = tmp / "data.zarr"
                full_path = self._backend._full_path(self.path)
                if self._backend.protocol == "file":
                    shutil.copytree(full_path, zarr_dest)
                else:
                    self._backend.fs.get(full_path, str(zarr_dest), recursive=True)

                # 2) viewer dist (index.html + assets/) from the installed figpack
                dist = Path(figpack.__file__).parent / "figpack-figure-dist"
                shutil.copytree(dist, tmp, dirs_exist_ok=True)

                # 3) bundles always carry an extension manifest; data-only storage has
                #    no extensions (validate() rejects ExtensionView)
                (tmp / "extension_manifest.json").write_text('{"extensions": []}')

                try:
                    if dest.exists() and not index.exists():
                        shutil.rmtree(dest)  # stale partial build (no index.html)
                    os.replace(tmp, dest)    # publish: index.html appears only complete
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

    def show(self, **kwargs) -> None:
        """
        Download and display the visualization in browser.

        Parameters
        ----------
        **kwargs
            Additional arguments passed to FigpackView.show().

        Examples
        --------
        Display in browser::

            ref.show()

        Display with custom options::

            ref.show(open_in_browser=True, port=8080)
        """
        view = self.load()
        view.show(**kwargs)

    def _repr_html_(self) -> str:
        """
        HTML representation for Jupyter notebooks.

        Returns an informative HTML snippet showing metadata.
        Loading the full visualization requires calling show() or load().
        """
        title_html = f"<strong>{self.title}</strong>" if self.title else "<em>Untitled</em>"
        desc_html = self.description[:200] + "..." if len(self.description) > 200 else self.description
        status = "loaded" if self.is_loaded else "not loaded"

        return f"""
        <div style="border: 1px solid #ccc; padding: 10px; border-radius: 5px; max-width: 400px;">
            <div style="font-size: 14px; margin-bottom: 5px;">{title_html}</div>
            <div style="font-size: 12px; color: #666; margin-bottom: 8px;">{desc_html}</div>
            <div style="font-size: 11px; color: #999;">
                FigpackRef ({status}) | <code>.show()</code> to display | <code>.load()</code> to get view
            </div>
        </div>
        """

    def __repr__(self) -> str:
        status = "loaded" if self.is_loaded else "not loaded"
        title_preview = f'"{self.title[:30]}..."' if len(self.title) > 30 else f'"{self.title}"'
        return f"FigpackRef(title={title_preview}, {status})"

    def __str__(self) -> str:
        return repr(self)
