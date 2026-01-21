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


class TestCodecEncodeDecode:
    """Integration tests for encode/decode cycle."""

    def test_encode_produces_metadata(self, sample_figpack_view, sample_context, mock_backend, mocker):
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
