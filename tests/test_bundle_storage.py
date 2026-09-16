"""The stored object is the whole figpack bundle, not just its data.

A figpack figure is the folder: viewer, data, and extension manifest together.
Storing only ``data.zarr`` made the render path depend on a compatible ``figpack``
being installed in the serving container, and left extension views nowhere to put
their JavaScript. See issue #7.
"""

import hashlib

import pytest


def _extension_view(js="// demo extension"):
    from figpack.core.extension_view import ExtensionView
    from figpack.core.figpack_extension import FigpackExtension

    return ExtensionView(
        extension=FigpackExtension(name="demo-ext", javascript_code=js),
        view_type="demo-ext.DemoView",
    )


def test_encode_stores_the_whole_bundle(
    sample_figpack_view, sample_context, mock_backend, default_store_config, temp_store, mocker
):
    """The store holds what ``value.save()`` produced — viewer included."""
    from dj_figpack_codecs import FigpackCodec

    codec = FigpackCodec()
    mocker.patch.object(codec, "_get_backend", return_value=mock_backend)

    meta = codec.encode(sample_figpack_view, key=sample_context, store_name="default")

    stored = temp_store / meta["path"]
    assert (stored / "index.html").exists()
    assert (stored / "assets").is_dir()
    assert (stored / "data.zarr" / ".zmetadata").exists()
    assert (stored / "extension_manifest.json").exists()


def test_validate_accepts_extension_view():
    """Extension JS travels inside the bundle, so extension views are storable.

    The extension is installed on the machine that populates the table and would
    never exist in the serving container — storing the bundle is what makes such a
    view portable at all.
    """
    from dj_figpack_codecs import FigpackCodec

    FigpackCodec().validate(_extension_view())  # no raise


def test_extension_javascript_survives_the_round_trip(
    sample_context, mock_backend, default_store_config, temp_store, mocker
):
    """Storing the bundle has to actually carry the extension's JavaScript."""
    from dj_figpack_codecs import FigpackCodec

    codec = FigpackCodec()
    mocker.patch.object(codec, "_get_backend", return_value=mock_backend)

    meta = codec.encode(
        _extension_view("// SENTINEL-EXT-JS"), key=sample_context, store_name="default"
    )

    stored = temp_store / meta["path"]
    manifest = (stored / "extension_manifest.json").read_text()
    assert "demo-ext" in manifest
    js = [p for p in stored.rglob("*.js") if "SENTINEL-EXT-JS" in p.read_text(errors="ignore")]
    assert js, f"extension JS not found under {stored}"


def test_serve_under_serves_the_stored_bundle_verbatim(
    sample_figpack_view,
    sample_context,
    mock_backend,
    default_store_config,
    temp_store,
    tmp_path,
    mocker,
):
    """No viewer overlay: what was stored is what gets served.

    Marking the stored viewer proves the served copy came from the store rather
    than from the installed figpack package's dist.
    """
    from dj_figpack_codecs import FigpackCodec

    codec = FigpackCodec()
    mocker.patch.object(codec, "_get_backend", return_value=mock_backend)
    meta = codec.encode(sample_figpack_view, key=sample_context, store_name="default")

    stored = temp_store / meta["path"]
    (stored / "index.html").write_text("<!-- SENTINEL-FROM-STORE -->")

    ref = codec.decode(meta)
    url = ref.serve_under(tmp_path)

    fig_id = hashlib.sha1(ref.path.encode("utf-8")).hexdigest()[:16]
    assert url == f"/{fig_id}/index.html"
    assert (tmp_path / fig_id / "index.html").read_text() == "<!-- SENTINEL-FROM-STORE -->"


def test_serve_under_does_not_import_figpack(
    sample_figpack_view,
    sample_context,
    mock_backend,
    default_store_config,
    tmp_path,
    mocker,
    monkeypatch,
):
    """The render path is static file serving — the serving container needs no figpack."""
    from dj_figpack_codecs import FigpackCodec

    codec = FigpackCodec()
    mocker.patch.object(codec, "_get_backend", return_value=mock_backend)
    ref = codec.decode(codec.encode(sample_figpack_view, key=sample_context, store_name="default"))

    import builtins

    real_import = builtins.__import__

    def no_figpack(name, *args, **kwargs):
        if name == "figpack" or name.startswith("figpack."):
            raise AssertionError(f"serve_under imported {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_figpack)
    ref.serve_under(tmp_path)


def test_show_serves_the_bundle_over_http(
    sample_figpack_view, sample_context, mock_backend, default_store_config, mocker
):
    """show() serves rather than opening a file:// path — a figpack viewer fetches
    its Zarr chunks over HTTP and cannot load from the filesystem."""
    import urllib.request

    from dj_figpack_codecs import FigpackCodec

    codec = FigpackCodec()
    mocker.patch.object(codec, "_get_backend", return_value=mock_backend)
    ref = codec.decode(codec.encode(sample_figpack_view, key=sample_context, store_name="default"))

    url = ref.show(open_browser=False)

    assert url.startswith("http://127.0.0.1:")
    with urllib.request.urlopen(url, timeout=10) as r:
        assert r.status == 200
        assert b"<!doctype html" in r.read(200).lower()


def test_load_is_gone():
    """load() described reconstructing a FigpackView from stored data. The stored
    object is a bundle, so that contract no longer describes anything (issue #3)."""
    from dj_figpack_codecs import FigpackRef

    assert not hasattr(FigpackRef, "load")


def test_show_honours_range_requests(
    sample_figpack_view, sample_context, mock_backend, default_store_config, mocker
):
    """figpack packs chunks into large consolidated files and the viewer ranges into
    them. A handler that ignores Range would make the browser pull whole files."""
    import urllib.error
    import urllib.request

    from dj_figpack_codecs import FigpackCodec

    codec = FigpackCodec()
    mocker.patch.object(codec, "_get_backend", return_value=mock_backend)
    ref = codec.decode(codec.encode(sample_figpack_view, key=sample_context, store_name="default"))

    url = ref.show(open_browser=False)

    with urllib.request.urlopen(url, timeout=10) as r:
        whole = r.read()
        assert r.headers.get("Accept-Ranges") == "bytes"

    req = urllib.request.Request(url, headers={"Range": "bytes=0-9"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 206
        assert r.headers["Content-Range"] == f"bytes 0-9/{len(whole)}"
        assert r.read() == whole[:10]

    # a suffix range, which is how a reader pulls a trailing index
    req = urllib.request.Request(url, headers={"Range": "bytes=-5"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 206
        assert r.read() == whole[-5:]

    # past the end is 416, not a silent whole-file send
    req = urllib.request.Request(url, headers={"Range": f"bytes={len(whole) + 10}-"})
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("expected 416")
    except urllib.error.HTTPError as e:
        assert e.code == 416
        assert e.headers["Content-Range"] == f"bytes */{len(whole)}"
