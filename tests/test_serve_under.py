"""FigpackRef.serve_under(): publish the stored bundle as a servable figure."""

import hashlib
import os
import re
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def stored_ref(sample_figpack_view, sample_context, mock_backend, default_store_config, mocker):
    """Encode a real view through the codec, return the decoded ref (round-trip fidelity)."""
    from dj_figpack_codecs import FigpackCodec

    codec = FigpackCodec()
    mocker.patch.object(codec, "_get_backend", return_value=mock_backend)
    metadata = codec.encode(sample_figpack_view, key=sample_context, store_name="default")
    return codec.decode(metadata)


def test_serve_under_publishes_the_stored_bundle(stored_ref, tmp_path):
    url = stored_ref.serve_under(tmp_path)

    fig_id = hashlib.sha1(stored_ref.path.encode("utf-8")).hexdigest()[:16]
    assert url == f"/{fig_id}/index.html"  # leading "/", schema-addressed id
    assert re.fullmatch(r"/[0-9a-f]{16}/index\.html", url)  # hex id — path-safe by construction

    dest = tmp_path / fig_id
    assert (dest / "index.html").exists()  # viewer, from the store
    assert (dest / "assets").is_dir()
    assert (dest / "data.zarr" / ".zmetadata").exists()  # figure data
    assert (dest / "extension_manifest.json").exists()  # written by figpack at encode time


def test_serve_under_is_idempotent_and_touches_for_ttl(stored_ref, tmp_path):
    url1 = stored_ref.serve_under(tmp_path)
    dest = tmp_path / url1.lstrip("/").split("/")[0]

    os.utime(dest, (1, 1))  # pretend the dir aged
    marker = dest / "data.zarr" / ".zmetadata"
    before = marker.stat().st_mtime

    url2 = stored_ref.serve_under(tmp_path)
    assert url2 == url1
    assert marker.stat().st_mtime == before  # no re-download
    assert dest.stat().st_mtime > 1  # dir mtime refreshed (clean_assets TTL)


def test_serve_under_repairs_partial_build(stored_ref, tmp_path):
    url = stored_ref.serve_under(tmp_path)
    dest = tmp_path / url.lstrip("/").split("/")[0]
    (dest / "index.html").unlink()  # simulate a crashed earlier build
    shutil.rmtree(dest / "assets")

    url2 = stored_ref.serve_under(tmp_path)
    assert url2 == url
    assert (dest / "index.html").exists()
    assert (dest / "data.zarr" / ".zmetadata").exists()


def test_serve_under_publish_race_loser_treats_winner_as_success(stored_ref, tmp_path, monkeypatch):
    """Two workers cold-rendering the same figure: the loser's os.replace hits the
    winner's completed bundle (ENOTEMPTY) and must return success, not raise."""
    import dj_figpack_codecs.ref as ref_mod

    # Pre-build the "winner's" bundle in a separate dir.
    winner_dir = tmp_path / "winner"
    url = stored_ref.serve_under(winner_dir)
    fig_id = url.split("/")[1]

    serve = tmp_path / "serve"
    real_replace = ref_mod.os.replace

    def racing_replace(src, dst):
        # Simulate the winner publishing between our existence check and publish.
        if not (Path(dst) / "index.html").exists():
            shutil.copytree(winner_dir / fig_id, dst)
        return real_replace(src, dst)  # now raises ENOTEMPTY

    monkeypatch.setattr(ref_mod.os, "replace", racing_replace)
    url2 = stored_ref.serve_under(serve)

    assert url2 == url
    assert (serve / fig_id / "index.html").exists()  # winner's bundle intact
    assert not list(serve.glob(f".{fig_id}-*"))  # loser's staging cleaned


def test_serve_under_sweeps_orphaned_staging_dirs(stored_ref, tmp_path):
    """Staging dirs orphaned by a killed process are swept once they age out."""
    fig_id = hashlib.sha1(stored_ref.path.encode("utf-8")).hexdigest()[:16]
    orphan = tmp_path / f".{fig_id}-orphan"
    orphan.mkdir(parents=True)
    two_hours_ago = 7200
    import time as _time

    os.utime(orphan, (_time.time() - two_hours_ago, _time.time() - two_hours_ago))

    stored_ref.serve_under(tmp_path)
    assert not orphan.exists()
    assert (tmp_path / fig_id / "index.html").exists()


def test_serve_under_failure_cleans_staging_and_leaves_no_partial(sample_metadata, tmp_path):
    """A failed materialization must raise, leave no staging litter, and no partial
    published dir — so the next call retries instead of serving garbage."""
    from unittest.mock import MagicMock

    from dj_figpack_codecs import FigpackRef

    backend = MagicMock()
    backend.protocol = "file"
    backend._full_path.return_value = str(tmp_path / "does-not-exist")

    ref = FigpackRef(sample_metadata, backend)
    fig_id = hashlib.sha1(sample_metadata["path"].encode("utf-8")).hexdigest()[:16]

    with pytest.raises(FileNotFoundError):
        ref.serve_under(tmp_path)

    assert not list(tmp_path.glob(f".{fig_id}-*"))  # staging cleaned
    assert not (tmp_path / fig_id).exists()  # nothing half-published


def test_real_file_backend_roundtrip_without_db(
    sample_figpack_view, sample_context, default_store_config, tmp_path
):
    """encode -> decode -> serve_under through DataJoint's REAL StorageBackend
    (file protocol; _get_backend reads only dj.config — no DB connection needed).
    Guards against backend API drift that MagicMock-based tests cannot see."""
    from dj_figpack_codecs import FigpackCodec, FigpackRef

    codec = FigpackCodec()  # _get_backend NOT mocked
    metadata = codec.encode(sample_figpack_view, key=sample_context, store_name="default")
    ref = codec.decode(metadata)
    assert isinstance(ref, FigpackRef)

    serve = tmp_path / "serve"
    url = ref.serve_under(serve)
    fig_id = url.split("/")[1]
    assert (serve / fig_id / "index.html").exists()
    assert (serve / fig_id / "data.zarr" / ".zmetadata").exists()


def test_serve_under_remote_backend_uses_fs_get(sample_metadata, tmp_path):
    """Non-file protocols download via fsspec: fs.get(full_path, local, recursive=True)
    — StorageBackend has no get_folder (verified on datajoint 2.3.1)."""
    from unittest.mock import MagicMock

    from dj_figpack_codecs import FigpackRef

    remote_bundle = tmp_path / "remote-bundle"  # what the fake fs.get delivers
    (remote_bundle / "data.zarr").mkdir(parents=True)
    (remote_bundle / "data.zarr" / ".zmetadata").write_text("{}")
    (remote_bundle / "index.html").write_text("<!-- stored viewer -->")

    backend = MagicMock()
    backend.protocol = "s3"
    backend._full_path.return_value = "bucket/loc/" + sample_metadata["path"]

    def fake_get(src, dst, recursive=False):
        assert recursive is True
        shutil.copytree(remote_bundle, dst, dirs_exist_ok=True)

    backend.fs.get.side_effect = fake_get

    ref = FigpackRef(sample_metadata, backend)
    serve = tmp_path / "serve"
    url = ref.serve_under(serve)

    backend.fs.get.assert_called_once()
    fig_id = hashlib.sha1(sample_metadata["path"].encode("utf-8")).hexdigest()[:16]
    assert url == f"/{fig_id}/index.html"
    assert (serve / fig_id / "data.zarr" / ".zmetadata").exists()
    # the viewer is the one that was stored, not one assembled from a local figpack
    assert (serve / fig_id / "index.html").read_text() == "<!-- stored viewer -->"


def test_serve_under_rejects_nested_download_layout(sample_metadata, tmp_path):
    """fsspec's recursive get is destination-sensitive: if a backend/version ever
    nests the source dir INSIDE dst instead of landing contents AS dst, the published
    bundle would have no index.html at its root. serve_under must fail loud rather
    than publish a figure that serves as an unexplained 404."""
    from unittest.mock import MagicMock

    from datajoint.errors import DataJointError

    from dj_figpack_codecs import FigpackRef

    remote_bundle = tmp_path / "remote-bundle"
    (remote_bundle / "data.zarr").mkdir(parents=True)
    (remote_bundle / "data.zarr" / ".zmetadata").write_text("{}")
    (remote_bundle / "index.html").write_text("<!-- stored viewer -->")

    backend = MagicMock()
    backend.protocol = "s3"
    backend._full_path.return_value = "bucket/loc/" + sample_metadata["path"]

    def nesting_get(src, dst, recursive=False):
        # the wrong layout: source dir nested inside dst, so no index.html at the root
        shutil.copytree(remote_bundle, Path(dst) / "bundle")

    backend.fs.get.side_effect = nesting_get

    ref = FigpackRef(sample_metadata, backend)
    serve = tmp_path / "serve"

    with pytest.raises(DataJointError, match="unexpected layout"):
        ref.serve_under(serve)

    fig_id = hashlib.sha1(sample_metadata["path"].encode("utf-8")).hexdigest()[:16]
    assert not list(serve.glob(f".{fig_id}-*"))  # staging cleaned
    assert not (serve / fig_id).exists()  # nothing half-published

    # Recovery: the failed attempt must not wedge the destination — once the
    # backend delivers the correct layout, the same ref publishes normally.
    def correct_get(src, dst, recursive=False):
        shutil.copytree(remote_bundle, dst, dirs_exist_ok=True)

    backend.fs.get.side_effect = correct_get
    url = ref.serve_under(serve)
    assert url == f"/{fig_id}/index.html"
    assert (serve / fig_id / "index.html").exists()
