# Copyright 2026 DataJoint Inc.
# SPDX-License-Identifier: Apache-2.0

"""
FigpackCodec for storing figpack visualizations in DataJoint OAS.

This codec enables storing FigpackView objects as self-contained bundles in
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


def _require_context(context: dict | None, *names: str) -> dict:
    """
    Return ``context``, checked to carry ``names``.

    DataJoint 2.3.4 and later pass ``context`` to every codec that declares it:
    ``schema``, ``table``, ``field``, and ``config`` on insert, ``config`` on
    fetch. The codec reads them there only, never from underscore keys in ``key``.
    """
    missing = [n for n in names if context is None or context.get(n) is None]
    if context is None or missing:
        raise DataJointError(
            "<figpack> needs the context that DataJoint 2.3.4 and later pass to codecs "
            f"(missing: {', '.join(missing)}); install datajoint>=2.3.4"
        )
    return context


def _primary_key(key: dict | None) -> dict:
    """The primary key values in ``key``, without the underscore keys DataJoint 2.3 adds."""
    return {k: v for k, v in (key or {}).items() if not k.startswith("_")}


class FigpackCodec(SchemaCodec):
    """
    Schema-addressed storage for figpack visualizations as self-contained bundles.

    The ``<figpack@>`` codec stores FigpackView objects as figpack bundles under a
    schema-addressed path chosen by the framework (mirrors schema/table, encodes
    primary keys as ``attr=value``, tokenized ``{attribute}_{token}.figpack`` folder name).
    Visualizations are fetched lazily via ``FigpackRef``, which provides
    metadata access (title, description) without I/O.

    Store only - requires ``@`` modifier.

    Key Features:
        - **Native format**: stores figpack's own bundle — viewer, data and
          extension manifest together, so the object is what figpack produced
        - **Lazy loading**: Metadata available without download
        - **Serving**: ``ref.serve_under(base_dir)`` publishes the stored bundle and
          ``ref.show()`` serves it over HTTP; neither needs ``figpack`` installed
        - **Jupyter integration**: Rich HTML display in notebooks
        - **Schema-addressed**: Browsable paths that mirror database structure

    Example::

        import dj_figpack_codecs  # Auto-registers codec

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
                # figpack >= 0.3: title is an optional view attribute, not a
                # constructor kwarg — the codec reads it via getattr().
                fig = vv.TimeseriesGraph()
                fig.title = "Spike Raster"
                # ... populate figure

                self.insert1({**key, 'visualization': fig})

        # Fetch - returns FigpackRef (lazy)
        ref = (RasterPlot & key).fetch1('visualization')
        ref.title       # "Spike Raster" - no download
        ref.description # "" - no download

        # Materialize a servable viewer bundle (e.g. for a dashboard)
        url = ref.serve_under("assets/serve")

    Storage Details:
        - File format: figpack bundle — index.html, assets/, data.zarr/ and the
          extension manifest, exactly as ``FigpackView.save()`` emits them
        - Path: schema-addressed, framework-chosen (e.g. ``{schema}/{table}/{pk_attr}={val}/{attribute}_{token}.figpack/``)
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
        TypeError
            If value is not a FigpackView instance.
        DataJointError
            If the figpack package is not installed.
        """
        try:
            from figpack import FigpackView
        except ImportError:
            raise DataJointError(
                "<figpack> codec requires figpack package. Install with: pip install figpack"
            )

        # Codec convention: TypeError for unsupported types (matches
        # AttachCodec/FilepathCodec). The ImportError branch above stays a
        # DataJointError — a missing package is an environment problem, not a
        # value problem.
        if not isinstance(value, FigpackView):
            raise TypeError(f"<figpack> requires figpack.FigpackView, got {type(value).__name__}")

    def encode(
        self,
        value: "FigpackView",
        *,
        key: dict | None = None,
        context: dict | None = None,
        store_name: str | None = None,
    ) -> dict:
        """
        Save FigpackView as a bundle and upload the whole folder to storage.

        Parameters
        ----------
        value : FigpackView
            The figpack visualization to store.
        key : dict, optional
            Primary key values for path construction.
        context : dict
            ``schema``, ``table``, ``field`` and ``config`` of the insert, as
            DataJoint 2.3.4 and later pass them.
        store_name : str, optional
            Target store. If None, uses default store.

        Returns
        -------
        dict
            JSON metadata: ``{path, store, title, description}``.

        Raises
        ------
        DataJointError
            If ``context`` lacks the ``schema``, ``table``, ``field``, or
            ``config`` that DataJoint 2.3.4 and later pass.
        """
        import tempfile
        from pathlib import Path

        context = _require_context(context, "schema", "table", "field", "config")
        # The inserting connection's own config, never global dj.config: in a
        # process serving many users, the global config is someone else's store.
        config = context["config"]

        # The object is a figpack bundle folder, not a Zarr store.
        path, _token = self._build_path(
            context["schema"],
            context["table"],
            context["field"],
            _primary_key(key),
            ext=".figpack",
            store_name=store_name,
            config=config,
        )

        # Extract metadata before saving
        title = getattr(value, "title", "") or ""
        description = getattr(value, "description", "") or ""

        # Save to a temporary directory, then upload the whole bundle
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_path = Path(tmpdir) / "bundle"

            # figpack >= 0.3: save() requires keyword-only `title`. It emits a full
            # viewer bundle (index.html + assets/ + data.zarr + extension manifest)
            # and the whole folder is the object: a figpack figure is not data with a
            # viewer laid over it at render time, it is the bundle. Storing all of it
            # is what lets the serving container render without figpack installed, and
            # what gives an extension view somewhere to keep its JavaScript.
            value.save(str(bundle_path), title=title, description=description)

            backend = self._get_backend(store_name, config=config)
            backend.put_folder(str(bundle_path), path)

        # Return metadata
        return {
            "path": path,
            "store": store_name,
            "title": title,
            "description": description,
        }

    def decode(
        self, stored: dict, *, key: dict | None = None, context: dict | None = None
    ) -> FigpackRef:
        """
        Create lazy FigpackRef from stored metadata.

        Parameters
        ----------
        stored : dict
            JSON metadata from database.
        key : dict, optional
            Primary key values (unused).
        context : dict
            Carries ``config``, the fetching connection's config with that
            user's store credentials, as DataJoint 2.3.4 and later pass it.

        Returns
        -------
        FigpackRef
            Lazy reference with metadata access and display methods.

        Raises
        ------
        DataJointError
            If ``context`` lacks the ``config`` that DataJoint 2.3.4 and later pass.
        """
        del key  # the stored metadata locates the bundle
        config = _require_context(context, "config")["config"]
        backend = self._get_backend(stored.get("store"), config=config)
        return FigpackRef(stored, backend)
