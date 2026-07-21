"""
Test fixtures for figpack-datajoint.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def temp_store():
    """Create a temporary directory for storage testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_backend(temp_store):
    """Create a mock storage backend that writes to temp directory."""
    backend = MagicMock()
    backend.protocol = "file"

    def put_folder(local_path, remote_path):
        """Copy folder to temp store."""
        import shutil

        dest = temp_store / remote_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(local_path, dest)

    def get_folder(remote_path, local_path):
        """Copy folder from temp store."""
        import shutil

        src = temp_store / remote_path
        shutil.copytree(src, local_path)

    def _full_path(path):
        """Return full path in temp store."""
        return str(temp_store / path)

    backend.put_folder = put_folder
    backend.get_folder = get_folder
    backend._full_path = _full_path

    return backend


@pytest.fixture
def sample_figpack_view():
    """Create a sample FigpackView for testing."""
    pytest.importorskip("figpack")
    from figpack import views as vv

    # Create a simple timeseries graph
    import numpy as np

    # figpack >= 0.3 removed title/description from view constructors (they are
    # save()-time metadata); the codec reads optional view attributes via getattr().
    fig = vv.TimeseriesGraph()
    fig.title = "Test Visualization"
    fig.description = "A test plot"

    # Add some sample data
    t = np.linspace(0, 10, 100)
    y = np.sin(t)
    fig.add_line_series(name="sine", t=t.tolist(), y=y.tolist(), color="blue")

    return fig


@pytest.fixture
def default_store_config(temp_store):
    """datajoint >= 2.3 validates the store in _build_path (get_store_spec), so tests
    that exercise encode() need a configured 'default' store even with a mocked backend."""
    import datajoint as dj

    original = dict(dj.config.get("stores") or {})
    dj.config["stores"] = {"default": {"protocol": "file", "location": str(temp_store)}}
    yield
    dj.config["stores"] = original


@pytest.fixture
def sample_metadata():
    """Sample metadata dict as would be stored in database."""
    return {
        "path": "_schema/test_schema/test_table/id=1/visualization.zarr",
        "store": "default",
        "title": "Test Visualization",
        "description": "A test plot",
    }


@pytest.fixture
def sample_context():
    """Sample context dict as passed to encode."""
    return {
        "_schema": "test_schema",
        "_table": "test_table",
        "_field": "visualization",
        "id": 1,
    }
