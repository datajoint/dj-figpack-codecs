"""FigpackRef.serve_under(): materialize a servable viewer bundle from stored data.zarr."""
import hashlib
import os
import re
import shutil

import pytest


@pytest.fixture
def stored_ref(sample_figpack_view, sample_context, mock_backend, default_store_config, mocker):
    """Encode a real view through the codec, return the decoded ref (round-trip fidelity)."""
    from dj_figpack_codecs import FigpackCodec

    codec = FigpackCodec()
    mocker.patch.object(codec, "_get_backend", return_value=mock_backend)
    metadata = codec.encode(sample_figpack_view, key=sample_context, store_name="default")
    return codec.decode(metadata)


def test_serve_under_assembles_viewer_bundle(stored_ref, tmp_path):
    url = stored_ref.serve_under(tmp_path)

    fig_id = hashlib.sha1(stored_ref.path.encode("utf-8")).hexdigest()[:16]
    assert url == f"/{fig_id}/index.html"                    # leading "/", schema-addressed id
    assert re.fullmatch(r"/[0-9a-f]{16}/index\.html", url)   # hex id — path-safe by construction

    dest = tmp_path / fig_id
    assert (dest / "index.html").exists()                    # viewer dist
    assert (dest / "assets").is_dir()
    assert (dest / "data.zarr" / ".zmetadata").exists()      # figure data
    assert (dest / "extension_manifest.json").read_text() == '{"extensions": []}'


def test_serve_under_is_idempotent_and_touches_for_ttl(stored_ref, tmp_path):
    url1 = stored_ref.serve_under(tmp_path)
    dest = tmp_path / url1.lstrip("/").split("/")[0]

    os.utime(dest, (1, 1))                                   # pretend the dir aged
    marker = dest / "data.zarr" / ".zmetadata"
    before = marker.stat().st_mtime

    url2 = stored_ref.serve_under(tmp_path)
    assert url2 == url1
    assert marker.stat().st_mtime == before                  # no re-download
    assert dest.stat().st_mtime > 1                          # dir mtime refreshed (clean_assets TTL)


def test_serve_under_repairs_partial_build(stored_ref, tmp_path):
    url = stored_ref.serve_under(tmp_path)
    dest = tmp_path / url.lstrip("/").split("/")[0]
    (dest / "index.html").unlink()                           # simulate a crashed earlier build
    shutil.rmtree(dest / "assets")

    url2 = stored_ref.serve_under(tmp_path)
    assert url2 == url
    assert (dest / "index.html").exists()
    assert (dest / "data.zarr" / ".zmetadata").exists()


def test_serve_under_remote_backend_uses_fs_get(sample_metadata, tmp_path):
    """Non-file protocols download via fsspec: fs.get(full_path, local, recursive=True)
    — StorageBackend has no get_folder (verified on datajoint 2.3.1)."""
    from unittest.mock import MagicMock

    from dj_figpack_codecs import FigpackRef

    remote_zarr = tmp_path / "remote-data.zarr"              # what the fake fs.get delivers
    remote_zarr.mkdir()
    (remote_zarr / ".zmetadata").write_text("{}")

    backend = MagicMock()
    backend.protocol = "s3"
    backend._full_path.return_value = "bucket/loc/" + sample_metadata["path"]

    def fake_get(src, dst, recursive=False):
        assert recursive is True
        shutil.copytree(remote_zarr, dst)

    backend.fs.get.side_effect = fake_get

    ref = FigpackRef(sample_metadata, backend)
    serve = tmp_path / "serve"
    url = ref.serve_under(serve)

    backend.fs.get.assert_called_once()
    fig_id = hashlib.sha1(sample_metadata["path"].encode("utf-8")).hexdigest()[:16]
    assert url == f"/{fig_id}/index.html"
    assert (serve / fig_id / "data.zarr" / ".zmetadata").exists()
    assert (serve / fig_id / "index.html").exists()
