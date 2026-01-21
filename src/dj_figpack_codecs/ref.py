# Copyright 2026 DataJoint Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Lazy reference to figpack visualizations stored in DataJoint OAS.

FigpackRef provides metadata access without downloading the visualization,
and includes methods for loading and displaying the stored view.
"""

from __future__ import annotations

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
