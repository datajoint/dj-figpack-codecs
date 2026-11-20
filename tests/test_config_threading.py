# Copyright 2026 DataJoint Inc.
# SPDX-License-Identifier: Apache-2.0

"""Store resolution must follow the caller's config, not global ``dj.config``.

One dashboard process serves many users. Each connection carries its own
credentials on ``dj.Instance.config``, while global ``dj.config`` holds only
whatever the image was built with. DataJoint 2.3.4 and later hand the
connection's config to every codec as ``context["config"]``; a codec that
ignores it reads the wrong store, and on a pod with no ambient AWS credentials
it raises instead. The codec reads the context only from ``context``, never from
the underscore keys DataJoint 2.3 still adds to ``key``.
"""

import datajoint as dj
import pytest
from datajoint.errors import DataJointError
from datajoint.settings import Config

from dj_figpack_codecs import FigpackCodec

#: What a dashboard pod's global config holds when no AWS keys are in the
#: environment. ``get_store_spec`` accepts it because every required key is
#: present; ``StorageBackend`` then rejects it because they are falsy.
KEYLESS_GLOBAL_S3 = {
    "protocol": "s3",
    "endpoint": "s3.us-east-2.amazonaws.com",
    "bucket": "from-global-config",
    "location": "global/outbox",
    "access_key": None,
    "secret_key": None,
    "schema_prefix": "prefix_from_global",
}

#: What works-api hands back for the signed-in user, landing on the Instance.
CONNECTION_S3 = {
    "protocol": "s3",
    "endpoint": "s3.us-east-2.amazonaws.com",
    "bucket": "from-connection-config",
    "location": "connection/outbox",
    "access_key": "AKIAEXAMPLEEXAMPLE12",
    "secret_key": "s" * 40,
}

STORED = {
    "path": "prefix_from_global/s/t/id=1/visualization_abcd1234.figpack",
    "store": "general",
    "title": "Test Visualization",
    "description": "A test plot",
}


def _connection_config(store_spec):
    """A connection-scoped Config, as ``dj.Instance`` builds for one user."""
    cfg = Config()
    cfg["stores"] = {"general": dict(store_spec)}
    return cfg


@pytest.fixture
def keyless_global_store():
    """Global ``dj.config`` carrying the credential-less spec."""
    original = dict(dj.config.get("stores") or {})
    dj.config["stores"] = {"general": dict(KEYLESS_GLOBAL_S3)}
    yield
    dj.config["stores"] = original


@pytest.fixture
def global_store(request):
    """Global ``dj.config`` carrying a usable spec (the ambient / worker case)."""
    original = dict(dj.config.get("stores") or {})
    dj.config["stores"] = {"general": dict(request.param)}
    yield
    dj.config["stores"] = original


def _insert_context(cfg):
    return {
        "schema": "test_schema",
        "table": "test_table",
        "field": "visualization",
        "config": cfg,
    }


def test_decode_reads_config_from_context(keyless_global_store):
    """decode resolves the store from ``context["config"]``."""
    ref = FigpackCodec().decode(
        STORED, key=None, context={"config": _connection_config(CONNECTION_S3)}
    )

    assert ref._backend.spec["bucket"] == "from-connection-config"
    assert ref._backend.spec["access_key"] == CONNECTION_S3["access_key"]


@pytest.mark.parametrize(
    "global_store",
    [{**KEYLESS_GLOBAL_S3, "access_key": "AKIAGLOBALGLOBAL1234", "secret_key": "g" * 40}],
    indirect=True,
)
def test_decode_needs_context(global_store):
    """Without ``context``, decode refuses rather than fall back to global ``dj.config``
    or to the ``_config`` that DataJoint 2.3 adds to ``key``."""
    with pytest.raises(DataJointError, match=r"missing: config"):
        FigpackCodec().decode(STORED, key={"_config": _connection_config(CONNECTION_S3)})


@pytest.mark.parametrize(
    "context",
    [None, {"schema": "s", "table": "t", "field": "f"}],
    ids=["no context", "no config"],
)
def test_encode_needs_context(keyless_global_store, sample_figpack_view, temp_store, context):
    """encode refuses a missing context, even when ``key`` carries the legacy
    underscore keys."""
    cfg = _connection_config({"protocol": "file", "location": str(temp_store)})
    legacy_key = {"id": 1, "_schema": "s", "_table": "t", "_field": "f", "_config": cfg}

    with pytest.raises(DataJointError, match=r"DataJoint 2\.3\.4"):
        FigpackCodec().encode(
            sample_figpack_view, key=legacy_key, context=context, store_name="general"
        )


def test_encode_writes_through_connection_config(
    keyless_global_store, sample_figpack_view, temp_store
):
    """encode resolves its backend from ``context["config"]``, not the global store."""
    cfg = _connection_config(
        {
            "protocol": "file",
            "location": str(temp_store),
            "schema_prefix": "prefix_from_connection",
        }
    )

    metadata = FigpackCodec().encode(
        sample_figpack_view, key={"id": 1}, context=_insert_context(cfg), store_name="general"
    )

    assert (temp_store / metadata["path"] / "data.zarr" / ".zmetadata").exists()


def test_encode_builds_path_from_connection_config(
    keyless_global_store, sample_figpack_view, temp_store, mock_backend, mocker
):
    """``_build_path`` reads ``schema_prefix`` off the store spec, so resolving it
    against the global config writes under the wrong prefix — silently, since the
    keyless spec still satisfies ``get_store_spec``. Backend resolution is mocked
    out here so this stays red even if only ``_get_backend`` is threaded."""
    cfg = _connection_config(
        {
            "protocol": "file",
            "location": str(temp_store),
            "schema_prefix": "prefix_from_connection",
        }
    )
    codec = FigpackCodec()
    mocker.patch.object(codec, "_get_backend", return_value=mock_backend)

    metadata = codec.encode(
        sample_figpack_view, key={"id": 1}, context=_insert_context(cfg), store_name="general"
    )

    assert metadata["path"].startswith("prefix_from_connection/")


def test_encode_reads_context_without_deprecation(
    keyless_global_store, sample_figpack_view, temp_store, recwarn
):
    """encode builds the path from ``context`` and the primary key in ``key``, and
    never touches the deprecated key-dict path, even when ``key`` carries the
    underscore keys DataJoint 2.3 adds."""
    cfg = _connection_config({"protocol": "file", "location": str(temp_store)})
    key = {"id": 1, "_schema": "wrong", "_table": "wrong", "_field": "wrong", "_config": None}

    metadata = FigpackCodec().encode(
        sample_figpack_view, key=key, context=_insert_context(cfg), store_name="general"
    )

    assert "/test_schema/test_table/id=1/" in metadata["path"]
    assert "wrong" not in metadata["path"]
    assert (temp_store / metadata["path"] / "index.html").exists()
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]
