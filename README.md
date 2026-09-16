# dj-figpack-codecs

DataJoint codec for storing [figpack](https://github.com/datajoint/figpack) visualizations in schema-addressed object storage (OAS).

## Installation

```bash
pip install dj-figpack-codecs
```

## Quick Start

```python
import datajoint as dj
import dj_figpack_codecs  # Auto-registers <figpack> codec

# Configure a store for visualizations
dj.config['stores'] = {
    'figures': {
        'protocol': 'file',
        'location': '/data/figures',
    }
}

schema = dj.Schema('my_analysis')

@schema
class RasterPlot(dj.Computed):
    definition = """
    -> SortedUnits
    ---
    visualization : <figpack@figures>
    """

    def make(self, key):
        from figpack import views as vv
        import numpy as np

        # Fetch spike data
        spikes = (SortedUnits & key).fetch('spike_times')

        # Create figpack visualization
        fig = vv.TimeseriesGraph(
            title="Spike Raster",
            description="Unit activity over time"
        )

        for i, spike_train in enumerate(spikes):
            fig.add_line_series(
                name=f"Unit {i}",
                t=spike_train.tolist(),
                y=[i] * len(spike_train),
            )

        self.insert1({**key, 'visualization': fig})
```

## Usage

### Fetching Visualizations

Fetching returns a `FigpackRef` - a lazy reference that provides metadata without downloading:

```python
# Fetch returns FigpackRef (lazy)
ref = (RasterPlot & key).fetch1('visualization')

# Access metadata without download
print(ref.title)        # "Spike Raster"
print(ref.description)  # "Unit activity over time"

# Serve it over HTTP and open it in a browser
ref.show()
```

### Jupyter Integration

In Jupyter notebooks, `FigpackRef` displays a rich HTML preview:

```python
ref  # Shows title, description, and action hints
```

### Storage Structure

A figpack figure is its bundle — viewer, data and extension manifest together — and the
whole folder is what gets stored. That is what lets a figure be served without `figpack`
installed, and what gives an extension view somewhere to keep its JavaScript.

Bundles are stored under a **schema-addressed path chosen by the
framework** (DataJoint's `build_object_path`): it mirrors the schema/table structure,
encodes primary keys as `attr=value` segments, and ends in a tokenized filename
(`{attribute}_{token}.zarr`), subject to the store's prefix/partitioning configuration —
for example:

```
{store_location}/demo_showcase/fluorescence_figpack/session_id=4/fig_NPhczfGY.zarr/
```

The stored folder holds `index.html`, `assets/`, `data.zarr/` and
`extension_manifest.json` — exactly what `FigpackView.save()` produced. Treat it as
opaque: a custom view may name its data folder differently or carry several.

The layout is browsable but framework-owned — do not hand-build or rely on exact paths;
the database column's metadata (`path`, `store`) is the source of truth.

## API Reference

### FigpackCodec

The codec for `<figpack@store>` attributes. Registered automatically on import.

### FigpackRef

Lazy reference returned when fetching `<figpack@>` attributes.

**Properties:**
- `title` - Visualization title (no I/O)
- `description` - Visualization description (no I/O)
- `path` - Storage path
- `store` - Store name

**Methods:**
- `show(open_browser=True)` - Serve the figure over HTTP; returns its URL
- `serve_under(base_dir)` - Publish the stored bundle; returns its relative URL

### Serving a figure in a dashboard

`FigpackRef.serve_under(base_dir)` downloads the stored bundle into `base_dir/<id>/`
and returns the relative URL `/<id>/index.html`. Nothing is assembled and `figpack`
need not be installed in the serving process. Dashboards (e.g. dash-datajoint-components'
`PlotGrid`) serve `base_dir` over HTTP and embed the URL in an `<iframe>`; repeated
calls are idempotent and refresh the directory mtime for TTL-based cache cleaners.

## Requirements

- Python >= 3.10
- datajoint >= 2.0
- figpack >= 0.3

## License

Copyright 2026 DataJoint Inc.

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.
