# Copyright 2026 DataJoint Inc.
# SPDX-License-Identifier: Apache-2.0

"""
FigpackCodec for storing figpack visualizations in DataJoint OAS.

This codec enables storing FigpackView objects as Zarr folders in
schema-addressed object storage, with lazy loading via FigpackRef.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import datajoint as dj
from datajoint.builtin_codecs import SchemaCodec
from datajoint.errors import DataJointError

from .ref import FigpackRef

if TYPE_CHECKING:
    from figpack import FigpackView


class FigpackCodec(SchemaCodec):
    """
    Schema-addressed storage for figpack visualizations as Zarr folders.

    The ``<figpack@>`` codec stores FigpackView objects as Zarr folders under a
    schema-addressed path chosen by the framework (mirrors schema/table, encodes
    primary keys as ``attr=value``, tokenized ``{attribute}_{token}.zarr`` filename).
    Visualizations are fetched lazily via ``FigpackRef``, which provides
    metadata access (title, description) without I/O.

    Store only - requires ``@`` modifier.

    Key Features:
        - **Native format**: Stores as Zarr folder (figpack's native format)
        - **Lazy loading**: Metadata available without download
        - **Browser display**: ``ref.show()`` opens visualization in browser
        - **Jupyter integration**: Rich HTML display in notebooks
        - **Schema-addressed**: Browsable paths that mirror database structure

    Example::

        import figpack_datajoint  # Auto-registers codec

        @schema
        class RasterPlot(dj.Computed):
            definition = '''
            -> SortedUnits
            ---
            visualization : <figpack@figures>
            '''

            def make(self, key):
                from figpack import views as vv

                spikes = (SortedUnits & key).fetch('spike_times')
                fig = vv.TimeseriesGraph(title="Spike Raster")
                # ... populate figure

                self.insert1({**key, 'visualization': fig})

        # Fetch - returns FigpackRef (lazy)
        ref = (RasterPlot & key).fetch1('visualization')
        ref.title       # "Spike Raster" - no download
        ref.description # "" - no download

        # Display in browser
        ref.show()

        # Or load explicitly
        view = ref.load()

    Storage Details:
        - File format: Zarr folder (figpack native)
        - Path: schema-addressed, framework-chosen (e.g. ``{schema}/{table}/{pk_attr}={val}/{attribute}_{token}.zarr/``)
        - Database column: JSON with ``{path, store, title, description}``

    See Also
    --------
    FigpackRef : The lazy reference returned on fetch.
    SchemaCodec : Base class for schema-addressed codecs.
    """

    name = "figpack"

    def validate(self, value: Any) -> None:
        """
        Validate that value is a FigpackView.

        Parameters
        ----------
        value : Any
            Value to validate.

        Raises
        ------
        DataJointError
            If value is not a FigpackView instance.
        """
        try:
            from figpack import FigpackView
        except ImportError:
            raise DataJointError(
                "<figpack> codec requires figpack package. Install with: pip install figpack"
            )

        if not isinstance(value, FigpackView):
            raise DataJointError(
                f"<figpack> requires figpack.FigpackView, got {type(value).__name__}"
            )

        from figpack.core.extension_view import ExtensionView

        if isinstance(value, ExtensionView):
            raise DataJointError(
                "<figpack> stores figure data only (data.zarr) and cannot yet preserve "
                "extension JavaScript; extension-based views are not supported."
            )

    def encode(
        self,
        value: "FigpackView",
        *,
        key: dict | None = None,
        store_name: str | None = None,
    ) -> dict:
        """
        Save FigpackView as Zarr folder and upload to storage.

        Parameters
        ----------
        value : FigpackView
            The figpack visualization to store.
        key : dict, optional
            Context dict with ``_schema``, ``_table``, ``_field``,
            and primary key values for path construction.
        store_name : str, optional
            Target store. If None, uses default store.

        Returns
        -------
        dict
            JSON metadata: ``{path, store, title, description}``.
        """
        import tempfile
        from pathlib import Path

        # Extract context using inherited helper
        schema, table, field, primary_key = self._extract_context(key)

        # Build schema-addressed storage path (folder, so no extension in path building)
        # We'll append .zarr to make it clear it's a Zarr folder
        path, token = self._build_path(
            schema, table, field, primary_key, ext=".zarr", store_name=store_name
        )

        # Extract metadata before saving
        title = getattr(value, "title", "") or ""
        description = getattr(value, "description", "") or ""

        # Save to temporary directory, then upload the figure DATA only
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_path = Path(tmpdir) / "bundle"

            # figpack >= 0.3: save() requires keyword-only `title`. It emits a full
            # viewer bundle (index.html + assets/ + data.zarr + extension manifest);
            # we store ONLY data.zarr — the viewer is laid over it at render time by
            # FigpackRef.serve_under(), so the store never duplicates viewer code.
            value.save(str(bundle_path), title=title, description=description)

            backend = self._get_backend(store_name)
            backend.put_folder(str(bundle_path / "data.zarr"), path)

        # Return metadata
        return {
            "path": path,
            "store": store_name,
            "title": title,
            "description": description,
        }

    def decode(self, stored: dict, *, key: dict | None = None) -> FigpackRef:
        """
        Create lazy FigpackRef from stored metadata.

        Parameters
        ----------
        stored : dict
            JSON metadata from database.
        key : dict, optional
            Primary key values (unused).

        Returns
        -------
        FigpackRef
            Lazy reference with metadata access and display methods.
        """
        backend = self._get_backend(stored.get("store"))
        return FigpackRef(stored, backend)
