"""
MONAI-native tile dataset for PyTorch Connectomics.

This module provides tile-based dataset classes using MONAI's native dataset
infrastructure for large-scale connectomics data that cannot fit in memory.
"""

from __future__ import annotations
from typing import Dict, List, Any, Optional, Union, Sequence, Tuple
import numpy as np
import json
import random

import torch
from monai.data import Dataset, CacheDataset
from monai.transforms import Compose, LoadImaged, EnsureChannelFirstd
from monai.utils import ensure_tuple_rep

from .dataset_base import MonaiConnectomicsDataset, create_data_dicts_from_paths
from ..io import create_tile_metadata, reconstruct_volume_from_tiles


class MonaiTileDataset(MonaiConnectomicsDataset):
    """
    MONAI-native dataset for large-scale tile-based connectomics data.

    This dataset is designed for large-scale volumetric datasets that are stored
    as individual tiles. It constructs smaller chunks for processing without loading
    the entire volume into memory.

    Args:
        volume_json (str): JSON metadata file for input image tiles
        label_json (str, optional): JSON metadata file for label tiles
        mask_json (str, optional): JSON metadata file for valid mask tiles
        transforms (Compose, optional): MONAI transforms pipeline
        chunk_num (Tuple[int, int, int]): Volume splitting parameters (z, y, x). Default: (2, 2, 2)
        chunk_indices (List[Tuple], optional): Predefined list of chunk indices
        chunk_iter (int): Number of iterations on each chunk. Default: -1
        chunk_stride (bool): Allow overlap between chunks. Default: True
        sample_size (Tuple[int, int, int]): Size of samples to extract (z, y, x)
        mode (str): Dataset mode ('train', 'val', 'test'). Default: 'train'
        iter_num (int): Number of iterations per epoch (-1 for inference). Default: -1
        pad_size (Tuple[int, int, int]): Padding parameters (z, y, x). Default: (0, 0, 0)
        **kwargs: Additional arguments passed to base dataset
    """

    def __init__(
        self,
        volume_json: str,
        label_json: Optional[str] = None,
        mask_json: Optional[str] = None,
        transforms: Optional[Compose] = None,
        chunk_num: Tuple[int, int, int] = (2, 2, 2),
        chunk_indices: Optional[List[Tuple[int, int, int]]] = None,
        chunk_iter: int = -1,
        chunk_stride: bool = True,
        sample_size: Tuple[int, int, int] = (32, 256, 256),
        mode: str = 'train',
        iter_num: int = -1,
        pad_size: Tuple[int, int, int] = (0, 0, 0),
        **kwargs,
    ):
        # Load tile metadata
        self.volume_metadata = self._load_tile_metadata(volume_json)
        self.label_metadata = self._load_tile_metadata(label_json) if label_json else None
        self.mask_metadata = self._load_tile_metadata(mask_json) if mask_json else None

        # Store tile-specific parameters
        self.chunk_num = ensure_tuple_rep(chunk_num, 3)
        self.chunk_indices = chunk_indices
        self.chunk_iter = chunk_iter
        self.chunk_stride = chunk_stride
        self.pad_size = ensure_tuple_rep(pad_size, 3)

        # Calculate chunk coordinates if not provided
        if chunk_indices is None:
            self.chunk_indices = self._calculate_chunk_indices()

        # Create data dictionaries for chunks
        data_dicts = self._create_chunk_data_dicts()

        # Create transforms if not provided
        if transforms is None:
            transforms = self._create_default_transforms(mode=mode)

        # Initialize base dataset
        super().__init__(
            data_dicts=data_dicts,
            transforms=transforms,
            sample_size=sample_size,
            mode=mode,
            iter_num=iter_num,
            **kwargs,
        )

    def _load_tile_metadata(self, json_path: str) -> Dict[str, Any]:
        """Load tile metadata from JSON file."""
        if json_path is None:
            return None

        with open(json_path, 'r') as f:
            return json.load(f)

    def _calculate_chunk_indices(self) -> List[Tuple[int, int, int]]:
        """Calculate chunk indices based on chunk_num and volume dimensions."""
        # Get volume dimensions from metadata
        depth = self.volume_metadata['depth']
        height = self.volume_metadata['height']
        width = self.volume_metadata['width']

        # Calculate chunk sizes
        chunk_z = depth // self.chunk_num[0]
        chunk_y = height // self.chunk_num[1]
        chunk_x = width // self.chunk_num[2]

        chunk_indices = []
        for z in range(self.chunk_num[0]):
            for y in range(self.chunk_num[1]):
                for x in range(self.chunk_num[2]):
                    # Calculate chunk boundaries
                    z_start = z * chunk_z
                    z_end = min((z + 1) * chunk_z, depth)
                    y_start = y * chunk_y
                    y_end = min((y + 1) * chunk_y, height)
                    x_start = x * chunk_x
                    x_end = min((x + 1) * chunk_x, width)

                    chunk_indices.append({
                        'chunk_id': (z, y, x),
                        'coords': (z_start, z_end, y_start, y_end, x_start, x_end),
                    })

        return chunk_indices

    def _create_chunk_data_dicts(self) -> List[Dict[str, Any]]:
        """Create MONAI data dictionaries for each chunk."""
        data_dicts = []

        for chunk_info in self.chunk_indices:
            chunk_id = chunk_info['chunk_id']
            coords = chunk_info['coords']

            data_dict = {
                'image': {
                    'metadata': self.volume_metadata,
                    'chunk_coords': coords,
                    'chunk_id': chunk_id,
                },
            }

            if self.label_metadata:
                data_dict['label'] = {
                    'metadata': self.label_metadata,
                    'chunk_coords': coords,
                    'chunk_id': chunk_id,
                }

            if self.mask_metadata:
                data_dict['mask'] = {
                    'metadata': self.mask_metadata,
                    'chunk_coords': coords,
                    'chunk_id': chunk_id,
                }

            data_dicts.append(data_dict)

        return data_dicts

    def _create_default_transforms(self, mode: str) -> Compose:
        """Create default MONAI transforms for tile data."""
        keys = ['image']
        if self.label_metadata:
            keys.append('label')
        if self.mask_metadata:
            keys.append('mask')

        transforms = [
            # Custom tile loader
            TileLoaderd(keys=keys),
            # Ensure channel first format
            EnsureChannelFirstd(keys=keys),
        ]

        return Compose(transforms)


class TileLoaderd:
    """
    MONAI-style transform for loading tile-based data.

    This transform reconstructs volumes from tiles based on chunk coordinates
    and metadata information.
    """

    def __init__(self, keys: Sequence[str]):
        self.keys = keys

    def __call__(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Load tile data for specified keys."""
        result = {}

        for key in self.keys:
            if key in data:
                tile_info = data[key]
                metadata = tile_info['metadata']
                coords = tile_info['chunk_coords']

                # Reconstruct volume from tiles
                volume = self._load_tiles_for_chunk(metadata, coords)
                result[key] = volume
            else:
                result[key] = data[key]

        # Copy non-image data
        for key, value in data.items():
            if key not in self.keys:
                result[key] = value

        return result

    def _load_tiles_for_chunk(
        self,
        metadata: Dict[str, Any],
        coords: Tuple[int, int, int, int, int, int],
    ) -> np.ndarray:
        """Load and reconstruct volume chunk from tiles."""
        z_start, z_end, y_start, y_end, x_start, x_end = coords

        # Get tile paths for the depth range
        tile_paths = metadata['image'][z_start:z_end]

        # Volume coordinates for reconstruction
        volume_coords = [z_start, z_end, y_start, y_end, x_start, x_end]

        # Tile dataset coordinates (full volume)
        tile_coords = [0, metadata['depth'], 0, metadata['height'], 0, metadata['width']]

        # Reconstruct volume from tiles
        volume = reconstruct_volume_from_tiles(
            tile_paths=tile_paths,
            volume_coords=volume_coords,
            tile_coords=tile_coords,
            tile_size=metadata['tile_size'],
            data_type=np.dtype(metadata['dtype']),
            tile_start=metadata.get('tile_st', [0, 0]),
            tile_ratio=metadata.get('tile_ratio', 1.0),
        )

        return volume


class MonaiCachedTileDataset(MonaiTileDataset):
    """
    Cached version of MONAI tile dataset.

    This dataset caches reconstructed chunks in memory for improved performance.
    Suitable when the total size of cached chunks fits in available memory.

    Args:
        cache_rate (float): Percentage of chunks to cache. Default: 1.0
        num_workers (int): Number of workers for caching. Default: 0
        **kwargs: Arguments passed to MonaiTileDataset
    """

    def __init__(
        self,
        cache_rate: float = 1.0,
        num_workers: int = 0,
        **kwargs,
    ):
        # Initialize tile-specific attributes first
        volume_json = kwargs.pop('volume_json')
        label_json = kwargs.pop('label_json', None)
        mask_json = kwargs.pop('mask_json', None)
        transforms = kwargs.pop('transforms', None)

        # Load metadata
        self.volume_metadata = self._load_tile_metadata(volume_json)
        self.label_metadata = self._load_tile_metadata(label_json) if label_json else None
        self.mask_metadata = self._load_tile_metadata(mask_json) if mask_json else None

        # Set tile-specific parameters
        chunk_num = kwargs.get('chunk_num', (2, 2, 2))
        self.chunk_num = ensure_tuple_rep(chunk_num, 3)
        self.chunk_indices = kwargs.get('chunk_indices', None)

        # Calculate chunk coordinates if not provided
        if self.chunk_indices is None:
            self.chunk_indices = self._calculate_chunk_indices()

        # Create data dictionaries
        data_dicts = self._create_chunk_data_dicts()

        # Create transforms if not provided
        if transforms is None:
            transforms = self._create_default_transforms(kwargs.get('mode', 'train'))

        # Initialize as MONAI CacheDataset
        CacheDataset.__init__(
            self,
            data=data_dicts,
            transform=transforms,
            cache_rate=cache_rate,
            num_workers=num_workers,
        )

        # Store connectomics parameters
        for key, value in kwargs.items():
            setattr(self, key, value)

        sample_size = kwargs.get('sample_size', (32, 256, 256))
        self.sample_size = ensure_tuple_rep(sample_size, 3)
        self.mode = kwargs.get('mode', 'train')
        self.iter_num = kwargs.get('iter_num', -1)

        # Calculate dataset length
        if self.iter_num > 0:
            self.dataset_length = self.iter_num
        else:
            self.dataset_length = len(data_dicts)

    def _load_tile_metadata(self, json_path: str) -> Dict[str, Any]:
        """Load tile metadata from JSON file."""
        if json_path is None:
            return None

        with open(json_path, 'r') as f:
            return json.load(f)

    def _calculate_chunk_indices(self) -> List[Tuple[int, int, int]]:
        """Calculate chunk indices based on chunk_num and volume dimensions."""
        # Get volume dimensions from metadata
        depth = self.volume_metadata['depth']
        height = self.volume_metadata['height']
        width = self.volume_metadata['width']

        # Calculate chunk sizes
        chunk_z = depth // self.chunk_num[0]
        chunk_y = height // self.chunk_num[1]
        chunk_x = width // self.chunk_num[2]

        chunk_indices = []
        for z in range(self.chunk_num[0]):
            for y in range(self.chunk_num[1]):
                for x in range(self.chunk_num[2]):
                    # Calculate chunk boundaries
                    z_start = z * chunk_z
                    z_end = min((z + 1) * chunk_z, depth)
                    y_start = y * chunk_y
                    y_end = min((y + 1) * chunk_y, height)
                    x_start = x * chunk_x
                    x_end = min((x + 1) * chunk_x, width)

                    chunk_indices.append({
                        'chunk_id': (z, y, x),
                        'coords': (z_start, z_end, y_start, y_end, x_start, x_end),
                    })

        return chunk_indices

    def _create_chunk_data_dicts(self) -> List[Dict[str, Any]]:
        """Create MONAI data dictionaries for each chunk."""
        data_dicts = []

        for chunk_info in self.chunk_indices:
            chunk_id = chunk_info['chunk_id']
            coords = chunk_info['coords']

            data_dict = {
                'image': {
                    'metadata': self.volume_metadata,
                    'chunk_coords': coords,
                    'chunk_id': chunk_id,
                },
            }

            if self.label_metadata:
                data_dict['label'] = {
                    'metadata': self.label_metadata,
                    'chunk_coords': coords,
                    'chunk_id': chunk_id,
                }

            if self.mask_metadata:
                data_dict['mask'] = {
                    'metadata': self.mask_metadata,
                    'chunk_coords': coords,
                    'chunk_id': chunk_id,
                }

            data_dicts.append(data_dict)

        return data_dicts

    def _create_default_transforms(self, mode: str) -> Compose:
        """Create default MONAI transforms for tile data."""
        keys = ['image']
        if self.label_metadata:
            keys.append('label')
        if self.mask_metadata:
            keys.append('mask')

        transforms = [
            TileLoaderd(keys=keys),
            EnsureChannelFirstd(keys=keys),
        ]

        return Compose(transforms)

    def __len__(self) -> int:
        return self.dataset_length


def create_tile_dataset(
    volume_json: str,
    label_json: Optional[str] = None,
    mask_json: Optional[str] = None,
    transforms: Optional[Compose] = None,
    dataset_type: str = 'standard',
    cache_rate: float = 1.0,
    **kwargs,
) -> Union[MonaiTileDataset, MonaiCachedTileDataset]:
    """
    Factory function to create MONAI tile datasets.

    Args:
        volume_json: JSON metadata file for input image tiles
        label_json: Optional JSON metadata file for label tiles
        mask_json: Optional JSON metadata file for mask tiles
        transforms: MONAI transforms pipeline
        dataset_type: Type of dataset ('standard', 'cached')
        cache_rate: Cache rate for cached datasets
        **kwargs: Additional arguments for dataset initialization

    Returns:
        Appropriate MONAI tile dataset instance
    """
    if dataset_type == 'cached':
        return MonaiCachedTileDataset(
            volume_json=volume_json,
            label_json=label_json,
            mask_json=mask_json,
            transforms=transforms,
            cache_rate=cache_rate,
            **kwargs,
        )
    else:
        return MonaiTileDataset(
            volume_json=volume_json,
            label_json=label_json,
            mask_json=mask_json,
            transforms=transforms,
            **kwargs,
        )


def create_tile_data_dicts_from_json(
    volume_json: str,
    label_json: Optional[str] = None,
    mask_json: Optional[str] = None,
    chunk_num: Tuple[int, int, int] = (2, 2, 2),
) -> List[Dict[str, Any]]:
    """
    Create MONAI data dictionaries from tile JSON metadata files.

    Args:
        volume_json: JSON metadata file for input image tiles
        label_json: Optional JSON metadata file for label tiles
        mask_json: Optional JSON metadata file for mask tiles
        chunk_num: Volume splitting parameters (z, y, x)

    Returns:
        List of MONAI-style data dictionaries for tile chunks
    """
    # This would use the same logic as in MonaiTileDataset._create_chunk_data_dicts
    # but as a standalone function
    pass


__all__ = [
    'MonaiTileDataset',
    'MonaiCachedTileDataset',
    'TileLoaderd',
    'create_tile_dataset',
    'create_tile_data_dicts_from_json',
]