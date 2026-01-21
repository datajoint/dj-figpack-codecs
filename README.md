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

# Display in browser
ref.show()

# Or load the full FigpackView
view = ref.load()
```

### Jupyter Integration

In Jupyter notebooks, `FigpackRef` displays a rich HTML preview:

```python
ref  # Shows title, description, and action hints
```

### Storage Structure

Visualizations are stored as Zarr folders with schema-addressed paths:

```
{store_location}/
└── _schema/
    └── {schema}/
        └── {table}/
            └── {primary_key}/
                └── {attribute}.zarr/
```

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
- `is_loaded` - Whether data has been cached

**Methods:**
- `load()` - Download and return the `FigpackView`
- `show(**kwargs)` - Download and display in browser

## Requirements

- Python >= 3.10
- datajoint >= 2.0
- figpack >= 0.3

## License

Copyright 2026 DataJoint Inc.

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.
