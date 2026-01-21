# Copyright 2026 DataJoint Inc.
# SPDX-License-Identifier: Apache-2.0

"""
dj-figpack-codecs: DataJoint codec for storing figpack visualizations.

This package provides a codec for storing figpack FigpackView objects
in DataJoint's schema-addressed object storage (OAS). Visualizations
are stored as Zarr folders and fetched lazily via FigpackRef.

Usage::

    import datajoint as dj
    import dj_figpack_codecs  # Auto-registers <figpack> codec

    @schema
    class Visualization(dj.Computed):
        definition = '''
        -> Analysis
        ---
        figure : <figpack@figures>
        '''

        def make(self, key):
            from figpack import views as vv

            fig = vv.TimeseriesGraph(title="My Plot")
            # ... populate figure
            self.insert1({**key, 'figure': fig})

    # Fetch returns FigpackRef (lazy)
    ref = Visualization.fetch1('figure')
    print(ref.title)  # No download
    ref.show()        # Display in browser
"""

__version__ = "0.1.0"

from .codec import FigpackCodec
from .ref import FigpackRef

__all__ = ["FigpackCodec", "FigpackRef", "__version__"]
