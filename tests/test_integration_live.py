"""Live round-trip: <figpack@figures> insert -> lazy ref -> servable bundle.

Needs a running DataJoint 2.x-compatible MySQL and DJ_HOST/DJ_USER/DJ_PASS in the env;
skipped otherwise.
"""

import hashlib
import os

import numpy as np
import pytest

requires_live_dj = pytest.mark.skipif(
    not os.environ.get("DJ_HOST"), reason="needs a live DJ server (DJ_HOST/DJ_USER/DJ_PASS)"
)


@requires_live_dj
def test_figpack_column_roundtrip(tmp_path):
    import datajoint as dj
    import dj_figpack_codecs  # noqa: F401  (entry point auto-registers <figpack>)
    from dj_figpack_codecs import FigpackRef

    dj.config["safemode"] = False
    (tmp_path / "oas").mkdir()  # DJ 2.3 validates the file-store location exists
    dj.config["stores"] = {"figures": {"protocol": "file", "location": str(tmp_path / "oas")}}
    schema = dj.Schema("figpack_codec_test")

    @schema
    class Fig(dj.Manual):
        definition = """
        id : int
        ---
        fig : <figpack@figures>
        """

    try:
        from figpack.views import MultiChannelTimeseries

        rng = np.random.default_rng(0)
        view = MultiChannelTimeseries(
            start_time_sec=0.0,
            sampling_frequency_hz=30.0,
            data=rng.standard_normal((3000, 8)).astype(np.float32),
        )
        Fig.insert1({"id": 1, "fig": view})

        ref = (Fig & "id=1").fetch1("fig")
        assert isinstance(ref, FigpackRef)
        assert ref.path  # schema-addressed OAS path, no I/O

        # Spec Part-4 P0 exit gate: to_arrays returns the ref, not a stringified path
        (ref2,) = (Fig & "id=1").to_arrays("fig")
        assert isinstance(ref2, FigpackRef)

        serve = tmp_path / "serve"
        url = ref.serve_under(serve)
        fig_id = hashlib.sha1(ref.path.encode("utf-8")).hexdigest()[:16]
        assert url == f"/{fig_id}/index.html"
        root = serve / fig_id
        assert (root / "index.html").exists()
        assert (root / "data.zarr" / ".zmetadata").exists()
        assert (root / "extension_manifest.json").exists()
    finally:
        schema.drop()
