"""Round trip through a real S3-compatible store.

Runs only when ``FIGPACK_TEST_S3_ENDPOINT`` names one, for example a local MinIO or
moto server; ``FIGPACK_TEST_S3_BUCKET`` must already exist there. Exercises the real
DataJoint ``StorageBackend`` and s3fs, which the memory-filesystem test does not.
"""

import os

import pytest

ENDPOINT = os.environ.get("FIGPACK_TEST_S3_ENDPOINT")

pytestmark = pytest.mark.skipif(
    not ENDPOINT, reason="set FIGPACK_TEST_S3_ENDPOINT to run against an S3 store"
)


def test_encode_then_serve_under_through_s3(sample_figpack_view, tmp_path):
    from datajoint.settings import Config

    from dj_figpack_codecs import FigpackCodec

    cfg = Config()
    cfg["stores"] = {
        "figures": {
            "protocol": "s3",
            "endpoint": ENDPOINT,
            "bucket": os.environ.get("FIGPACK_TEST_S3_BUCKET", "figpack-test"),
            "location": f"roundtrip/{tmp_path.name}",
            "access_key": os.environ.get("FIGPACK_TEST_S3_ACCESS_KEY", "testing"),
            "secret_key": os.environ.get("FIGPACK_TEST_S3_SECRET_KEY", "testing"),
            "secure": False,
        }
    }
    context = {"schema": "s", "table": "t", "field": "fig", "config": cfg}

    codec = FigpackCodec()
    stored = codec.encode(
        sample_figpack_view, key={"id": 1}, context=context, store_name="figures"
    )
    ref = codec.decode(stored, context={"config": cfg})

    url = ref.serve_under(tmp_path / "serve")

    published = tmp_path / "serve" / url.split("/")[1]
    assert (published / "index.html").exists()
    assert (published / "data.zarr" / ".zmetadata").exists()
