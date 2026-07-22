"""
Tests for FigpackCodec and FigpackRef.
"""

import pytest

# Skip all tests if figpack is not installed
figpack = pytest.importorskip("figpack")


class TestFigpackRef:
    """Tests for FigpackRef lazy reference."""

    def test_metadata_access(self, sample_metadata, mock_backend):
        """Test that metadata is accessible without loading."""
        from dj_figpack_codecs import FigpackRef

        ref = FigpackRef(sample_metadata, mock_backend)

        assert ref.title == "Test Visualization"
        assert ref.description == "A test plot"
        assert ref.path == "_schema/test_schema/test_table/id=1/visualization.zarr"
        assert ref.store == "default"
        assert not ref.is_loaded

    def test_repr(self, sample_metadata, mock_backend):
        """Test string representation."""
        from dj_figpack_codecs import FigpackRef

        ref = FigpackRef(sample_metadata, mock_backend)
        repr_str = repr(ref)

        assert "FigpackRef" in repr_str
        assert "Test Visualization" in repr_str
        assert "not loaded" in repr_str

    def test_repr_html(self, sample_metadata, mock_backend):
        """Test HTML representation for Jupyter."""
        from dj_figpack_codecs import FigpackRef

        ref = FigpackRef(sample_metadata, mock_backend)
        html = ref._repr_html_()

        assert "Test Visualization" in html
        assert "A test plot" in html
        assert ".show()" in html
        assert ".load()" in html

    def test_empty_metadata(self, mock_backend):
        """Test handling of missing metadata fields."""
        from dj_figpack_codecs import FigpackRef

        minimal_metadata = {
            "path": "some/path.zarr",
        }
        ref = FigpackRef(minimal_metadata, mock_backend)

        assert ref.title == ""
        assert ref.description == ""
        assert ref.store is None


class TestFigpackCodec:
    """Tests for FigpackCodec."""

    def test_codec_registration(self):
        """Test that codec is registered after import."""
        import dj_figpack_codecs  # noqa: F401
        from datajoint.codecs import is_codec_registered

        assert is_codec_registered("figpack")

    def test_codec_name(self):
        """Test codec has correct name."""
        from dj_figpack_codecs import FigpackCodec

        codec = FigpackCodec()
        assert codec.name == "figpack"

    def test_get_dtype_requires_store(self):
        """Test that codec requires @ modifier."""
        from datajoint.errors import DataJointError

        from dj_figpack_codecs import FigpackCodec

        codec = FigpackCodec()

        # Should work with store
        assert codec.get_dtype(is_store=True) == "json"

        # Should fail without store
        with pytest.raises(DataJointError, match="requires @"):
            codec.get_dtype(is_store=False)

    def test_validate_accepts_figpack_view(self, sample_figpack_view):
        """Test validation accepts FigpackView."""
        from dj_figpack_codecs import FigpackCodec

        codec = FigpackCodec()
        # Should not raise
        codec.validate(sample_figpack_view)

    def test_validate_rejects_non_figpack_view(self):
        """Test validation rejects other types."""
        from datajoint.errors import DataJointError

        from dj_figpack_codecs import FigpackCodec

        codec = FigpackCodec()

        with pytest.raises(DataJointError, match="requires figpack.FigpackView"):
            codec.validate("not a view")

        with pytest.raises(DataJointError, match="requires figpack.FigpackView"):
            codec.validate({"dict": "value"})

        with pytest.raises(DataJointError, match="requires figpack.FigpackView"):
            codec.validate(None)

    def test_validate_rejects_extension_view(self):
        """v1 stores data-only Zarr; extension JS lives outside data.zarr and would be
        silently dropped — so extension-based views are rejected at insert time."""
        pytest.importorskip("figpack")
        from datajoint.errors import DataJointError
        from figpack.core.extension_view import ExtensionView
        from figpack.core.figpack_extension import FigpackExtension

        from dj_figpack_codecs import FigpackCodec

        ext_view = ExtensionView(
            extension=FigpackExtension(name="demo-ext", javascript_code="// noop"),
            view_type="demo.View",
        )
        with pytest.raises(DataJointError, match="extension"):
            FigpackCodec().validate(ext_view)

    def test_validate_accepts_plain_core_view(self):
        """Core (non-extension) views validate — the reference figure type included."""
        pytest.importorskip("figpack")
        import numpy as np
        from figpack.views import MultiChannelTimeseries

        from dj_figpack_codecs import FigpackCodec

        view = MultiChannelTimeseries(
            start_time_sec=0.0,
            sampling_frequency_hz=10.0,
            data=np.zeros((20, 2), dtype=np.float32),
        )
        FigpackCodec().validate(view)  # no raise

    def test_validate_rejects_plotly_figure_extension_view(self):
        """Canary: figpack's PlotlyFigure IS an ExtensionView (its JS lives outside
        data.zarr), so data-only storage must reject it. If figpack ever rebases
        PlotlyFigure onto a core view, this test flags the policy for re-evaluation."""
        pytest.importorskip("figpack")
        plotly = pytest.importorskip("plotly")
        import plotly.graph_objects as go
        from datajoint.errors import DataJointError

        from dj_figpack_codecs import FigpackCodec

        from figpack.views import PlotlyFigure

        fig = PlotlyFigure(fig=go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 4])]))
        with pytest.raises(DataJointError, match="extension"):
            FigpackCodec().validate(fig)


class TestCodecEncodeDecode:
    """Integration tests for encode/decode cycle."""

    def test_encode_produces_metadata(
        self, sample_figpack_view, sample_context, mock_backend, default_store_config, mocker
    ):
        """Test that encode produces correct metadata structure."""
        from dj_figpack_codecs import FigpackCodec

        codec = FigpackCodec()

        # Mock the backend retrieval
        mocker.patch.object(codec, "_get_backend", return_value=mock_backend)

        metadata = codec.encode(
            sample_figpack_view,
            key=sample_context,
            store_name="default",
        )

        assert "path" in metadata
        assert "store" in metadata
        assert "title" in metadata
        assert "description" in metadata

        assert metadata["title"] == "Test Visualization"
        assert metadata["description"] == "A test plot"
        assert metadata["store"] == "default"
        assert ".zarr" in metadata["path"]

    def test_encode_stores_data_only_zarr(
        self, sample_figpack_view, sample_context, mock_backend, temp_store, default_store_config, mocker
    ):
        """encode() uploads figpack's data.zarr only — no viewer files — and the
        consolidated zarr metadata carries the title."""
        import json

        from dj_figpack_codecs import FigpackCodec

        codec = FigpackCodec()
        mocker.patch.object(codec, "_get_backend", return_value=mock_backend)

        metadata = codec.encode(sample_figpack_view, key=sample_context, store_name="default")

        stored = temp_store / metadata["path"]
        assert (stored / ".zmetadata").exists()          # consolidated zarr metadata
        assert not (stored / "index.html").exists()      # no viewer in the store
        assert not (stored / "assets").exists()
        zmeta = json.loads((stored / ".zmetadata").read_text())
        assert zmeta["metadata"][".zattrs"]["title"] == "Test Visualization"
        assert zmeta["metadata"][".zattrs"]["description"] == "A test plot"

    def test_encode_titleless_view_stores_empty_title(
        self, sample_context, mock_backend, default_store_config, mocker
    ):
        """Views without a title attribute (e.g. MultiChannelTimeseries) must encode:
        figpack's save() requires the title kwarg but accepts an empty string."""
        pytest.importorskip("figpack")
        import numpy as np
        from figpack.views import MultiChannelTimeseries

        from dj_figpack_codecs import FigpackCodec

        view = MultiChannelTimeseries(
            start_time_sec=0.0,
            sampling_frequency_hz=10.0,
            data=np.zeros((20, 2), dtype=np.float32),
        )
        codec = FigpackCodec()
        mocker.patch.object(codec, "_get_backend", return_value=mock_backend)

        metadata = codec.encode(view, key=sample_context, store_name="default")
        assert metadata["title"] == ""
        assert metadata["description"] == ""

    def test_decode_returns_figpack_ref(self, sample_metadata, mock_backend, mocker):
        """Test that decode returns FigpackRef."""
        from dj_figpack_codecs import FigpackCodec, FigpackRef

        codec = FigpackCodec()

        # Mock the backend retrieval
        mocker.patch.object(codec, "_get_backend", return_value=mock_backend)

        ref = codec.decode(sample_metadata)

        assert isinstance(ref, FigpackRef)
        assert ref.title == sample_metadata["title"]
        assert ref.description == sample_metadata["description"]
