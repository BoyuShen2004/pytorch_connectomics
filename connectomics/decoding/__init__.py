"""
Decoding package for PyTorch Connectomics.

This package provides post-processing functions to convert model predictions
into final instance segmentation masks for various biological structures.

Modules:
    - segmentation: Mitochondria and organelle instance decoding
    - synapse: Synaptic polarity instance decoding
    - postprocess: General post-processing utilities
    - utils: Shared utility functions
    - auto_tuning: Hyperparameter optimization for post-processing

Import patterns:
    from connectomics.decoding import decode_binary_watershed, decode_binary_contour_watershed
    from connectomics.decoding import polarity2instance
    from connectomics.decoding import stitch_3d, watershed_split
    from connectomics.decoding import optimize_threshold, SkeletonMetrics
"""

from .segmentation import (
    decode_binary_cc,
    decode_binary_watershed,
    decode_binary_contour_cc,
    decode_binary_contour_watershed,
    decode_binary_contour_distance_watershed,
    decode_affinity_cc,
)

from .auto_tuning import (
    optimize_threshold,
    optimize_parameters,
    grid_search_threshold,
    SkeletonMetrics,
)

from .synapse import (
    polarity2instance,
)

from .postprocess import (
    binarize_and_median,
    remove_masks,
    add_masks,
    merge_masks,
    watershed_split,
    stitch_3d,
    intersection_over_union,
)

from .utils import (
    cast2dtype,
    remove_small_instances,
    remove_large_instances,
    merge_small_objects,
)


__all__ = [
    # Segmentation decoding
    'decode_binary_cc',
    'decode_binary_watershed',
    'decode_binary_contour_cc',
    'decode_binary_contour_watershed',
    'decode_binary_contour_distance_watershed',
    'decode_affinity_cc',

    # Auto-tuning
    'optimize_threshold',
    'optimize_parameters',
    'grid_search_threshold',
    'SkeletonMetrics',

    # Synapse decoding
    'polarity2instance',

    # Post-processing
    'binarize_and_median',
    'remove_masks',
    'add_masks',
    'merge_masks',
    'watershed_split',
    'stitch_3d',
    'intersection_over_union',

    # Utilities
    'cast2dtype',
    'remove_small_instances',
    'remove_large_instances',
    'merge_small_objects',
]
