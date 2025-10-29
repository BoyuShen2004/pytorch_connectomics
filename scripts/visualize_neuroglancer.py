#!/usr/bin/env python -i
"""
Neuroglancer visualization script for PyTorch Connectomics.

Visualize image and label volumes in a web browser using Neuroglancer.
Runs in interactive mode so you can examine loaded volumes.

Usage:
    python -i scripts/visualize_neuroglancer.py --config tutorials/monai_lucchi.yaml
    python -i scripts/visualize_neuroglancer.py --image path/to/image.tif --label path/to/label.h5
    python -i scripts/visualize_neuroglancer.py --volumes image:path/img.tif label:path/lbl.h5 seg:path/seg.h5

Interactive mode variables:
    viewer   - Neuroglancer viewer instance
    volumes  - Dictionary of loaded volumes {name: (data, type, resolution, offset)}
    cfg      - Config object (if loaded from --config)

Examples:
    # Examine volume data
    >>> volumes['train_image'][0].shape
    >>> volumes['train_image'][0].dtype

    # Access raw numpy arrays
    >>> img = volumes['train_image'][0]
    >>> lbl = volumes['train_label'][0]
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple, List, TYPE_CHECKING
import numpy as np

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import connectomics modules (needed for helper functions)
from connectomics.data.io import read_volume
from connectomics.config import load_config, resolve_data_paths

# Lazy import for neuroglancer (checked at runtime)
if TYPE_CHECKING:
    import neuroglancer
else:
    neuroglancer = None  # Will be imported in main() after arg parsing


def apply_image_transform(data: np.ndarray, cfg) -> np.ndarray:
    """
    Apply image transformations from config (normalization and clipping).

    Args:
        data: Input image array
        cfg: Config object with image_transform settings

    Returns:
        Transformed image array (same dtype as input)
    """
    if not hasattr(cfg.data, 'image_transform'):
        return data

    transform_cfg = cfg.data.image_transform
    original_dtype = data.dtype
    data = data.astype(np.float32)  # Work in float32

    print(f"  Applying image transformations:")
    print(f"    Original range: [{data.min():.2f}, {data.max():.2f}]")

    # Percentile clipping
    if hasattr(transform_cfg, 'clip_percentile_low') and hasattr(transform_cfg, 'clip_percentile_high'):
        low_pct = transform_cfg.clip_percentile_low
        high_pct = transform_cfg.clip_percentile_high

        if low_pct > 0.0 or high_pct < 1.0:
            low_val = np.percentile(data, low_pct * 100)
            high_val = np.percentile(data, high_pct * 100)
            print(f"    Clipping: {low_pct*100:.1f}th percentile ({low_val:.2f}) to {high_pct*100:.1f}th percentile ({high_val:.2f})")
            data = np.clip(data, low_val, high_val)

    # Normalization
    if hasattr(transform_cfg, 'normalize'):
        normalize_mode = transform_cfg.normalize

        if normalize_mode == "0-1":
            # Min-max normalization to [0, 1]
            data_min = data.min()
            data_max = data.max()
            if data_max > data_min:
                data = (data - data_min) / (data_max - data_min)
                print(f"    Normalized to [0, 1] (min-max)")
            else:
                print(f"    Warning: data_min == data_max, skipping normalization")

        elif normalize_mode == "normal":
            # Z-score normalization
            data_mean = data.mean()
            data_std = data.std()
            if data_std > 0:
                data = (data - data_mean) / data_std
                print(f"    Normalized (z-score): mean={data_mean:.2f}, std={data_std:.2f}")
            else:
                print(f"    Warning: data_std == 0, skipping normalization")

        elif normalize_mode == "none":
            print(f"    No normalization applied")

    print(f"    Final range: [{data.min():.2f}, {data.max():.2f}]")

    # Convert back to original dtype for visualization
    if normalize_mode == "0-1" and original_dtype == np.uint8:
        data = (data * 255).astype(np.uint8)
    elif normalize_mode == "0-1" and original_dtype == np.uint16:
        data = (data * 65535).astype(np.uint16)
    else:
        # Keep as float32 for normalized data
        pass

    return data


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Visualize volumes with Neuroglancer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # From config file (interactive mode recommended with -i)
  python -i scripts/visualize_neuroglancer.py --config tutorials/monai_lucchi.yaml

  # Specify files directly
  python -i scripts/visualize_neuroglancer.py \\
    --image datasets/Lucchi/img/train_im.tif \\
    --label datasets/Lucchi/label/train_label.tif

  # Multiple volumes with custom names (type inferred from name)
  python -i scripts/visualize_neuroglancer.py \\
    --volumes image:datasets/img.tif label:datasets/label.h5 prediction:outputs/pred.h5

  # Volumes with explicit type (format: name:type:path)
  python -i scripts/visualize_neuroglancer.py \\
    --volumes output:image:outputs/pred.h5 ground_truth:seg:datasets/label.h5

  # Volumes with resolution and offset (format: name:type:path:resolution:offset)
  python -i scripts/visualize_neuroglancer.py \\
    --volumes prediction:image:outputs/pred.h5:5-5-5:0-0-0

  # Mix config with additional volumes
  python -i scripts/visualize_neuroglancer.py \\
    --config tutorials/monai_lucchi.yaml --mode test \\
    --volumes prediction:image:outputs/lucchi_monai_unet/results/test_im_prediction.h5:5-5-5

  # Custom server settings
  python -i scripts/visualize_neuroglancer.py \\
    --config tutorials/monai_lucchi.yaml \\
    --ip 0.0.0.0 --port 8080 \\
    --resolution 30 6 6

Interactive mode (with -i flag):
  Access loaded variables:
    volumes['train_image'][0]  # numpy array of image data
    viewer                     # Neuroglancer viewer instance
    cfg                        # config object (if --config used)
        """,
    )

    # Input sources (at least one required, but not mutually exclusive)
    parser.add_argument(
        "--config", type=str, help="Path to config YAML file (reads train/test image/label paths)"
    )
    parser.add_argument(
        "--volumes",
        type=str,
        nargs="+",
        help='Volume paths in format "name:type:path[:resolution[:offset]]" where type is "image" or "seg", '
        'resolution is "z-y-x" in nm, and offset is "z-y-x" in voxels. '
        "Type can be omitted for backward compatibility (inferred from name). "
        'Examples: "pred:image:path.h5:5-5-5" or "label:seg:path.h5"',
    )
    parser.add_argument("--image", type=str, help="Path to image volume")
    parser.add_argument("--label", type=str, help="Path to label volume")

    # Server settings
    parser.add_argument(
        "--ip",
        type=str,
        default="localhost",
        help="Server IP address (default: localhost, use 0.0.0.0 for remote access)",
    )
    parser.add_argument("--port", type=int, default=9999, help="Server port (default: 9999)")

    # Volume metadata
    parser.add_argument(
        "--resolution",
        type=float,
        nargs=3,
        default=[30, 6, 6],
        help="Voxel resolution in nm as [z, y, x] (default: 30 6 6 for EM data)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        nargs=3,
        default=[0, 0, 0],
        help="Volume offset as [z, y, x] (default: 0 0 0)",
    )

    # Display options
    parser.add_argument(
        "--mode",
        type=str,
        choices=["train", "test", "both"],
        default="train",
        help="Which data to load from config (default: train)",
    )

    return parser.parse_args()


def load_volumes_from_config(
    config_path: str, mode: str = "train"
) -> Dict[str, Tuple[np.ndarray, str, Optional[Tuple], None]]:
    """
    Load volumes from a config file.

    Args:
        config_path: Path to YAML config file
        mode: Which data to load ('train', 'test', 'both')

    Returns:
        Dictionary mapping volume names to (data, type, resolution, offset) tuples
        where type is 'image' or 'segmentation', resolution is from config (or None), and offset is None
    """
    cfg = load_config(config_path)
    cfg = resolve_data_paths(cfg)  # Resolve paths and expand globs
    volumes = {}

    # Get resolution from config
    train_resolution = None
    if hasattr(cfg.data, "train_resolution") and cfg.data.train_resolution:
        train_resolution = tuple(cfg.data.train_resolution)
        print(f"Using train resolution from config: {train_resolution} nm (z, y, x)")

    test_resolution = None
    # Check inference.data.test_resolution first, then fall back to data.test_resolution
    if (
        hasattr(cfg, "inference")
        and hasattr(cfg.inference, "data")
        and hasattr(cfg.inference.data, "test_resolution")
        and cfg.inference.data.test_resolution
    ):
        test_resolution = tuple(cfg.inference.data.test_resolution)
        print(f"Using test resolution from inference config: {test_resolution} nm (z, y, x)")
    elif hasattr(cfg.data, "test_resolution") and cfg.data.test_resolution:
        test_resolution = tuple(cfg.data.test_resolution)
        print(f"Using test resolution from data config: {test_resolution} nm (z, y, x)")

    # Training data
    if mode in ["train", "both"]:
        if hasattr(cfg.data, "train_image") and cfg.data.train_image:
            print(f"Loading train images: {cfg.data.train_image}")

            # Handle list of files (load all volumes)
            if isinstance(cfg.data.train_image, list):
                print(f"  Found {len(cfg.data.train_image)} volumes, loading all...")
                for idx, img_file in enumerate(cfg.data.train_image):
                    print(f"  [{idx+1}/{len(cfg.data.train_image)}] Loading: {img_file}")
                    try:
                        data = read_volume(img_file)

                        # Convert 2D to 3D if needed
                        if data.ndim == 2:
                            data = data[None, :, :]  # (H, W) -> (1, H, W)
                            print(f"      Converted 2D to 3D: {data.shape}")

                        # Apply image transformations (normalization, clipping)
                        data = apply_image_transform(data, cfg)

                        # Create unique name for this volume
                        vol_name = f"train_image_{idx}" if len(cfg.data.train_image) > 1 else "train_image"
                        volumes[vol_name] = (data, "image", train_resolution, None)
                        print(f"      Loaded: {data.shape}, dtype={data.dtype}")
                    except Exception as e:
                        print(f"      Error loading {img_file}: {e}")
            else:
                # Single file
                data = read_volume(cfg.data.train_image)
                if data is not None:
                    # Convert 2D to 3D if needed
                    if data.ndim == 2:
                        data = data[None, :, :]  # (H, W) -> (1, H, W)
                        print(f"  Converted 2D train image to 3D: {data.shape}")
                        # Update resolution from 2D to 3D
                        if train_resolution and len(train_resolution) == 2:
                            train_resolution = (1.0,) + train_resolution  # Add z=1.0
                            print(f"  Updated train resolution to 3D: {train_resolution}")

                    # Apply image transformations (normalization, clipping)
                    data = apply_image_transform(data, cfg)
                    volumes["train_image"] = (data, "image", train_resolution, None)

        if hasattr(cfg.data, "train_label") and cfg.data.train_label:
            print(f"Loading train labels: {cfg.data.train_label}")

            # Handle list of files (load all volumes)
            if isinstance(cfg.data.train_label, list):
                print(f"  Found {len(cfg.data.train_label)} volumes, loading all...")
                for idx, lbl_file in enumerate(cfg.data.train_label):
                    print(f"  [{idx+1}/{len(cfg.data.train_label)}] Loading: {lbl_file}")
                    try:
                        data = read_volume(lbl_file)

                        # Convert 2D to 3D if needed
                        if data.ndim == 2:
                            data = data[None, :, :]  # (H, W) -> (1, H, W)
                            print(f"      Converted 2D to 3D: {data.shape}")

                        # Create unique name for this volume
                        vol_name = f"train_label_{idx}" if len(cfg.data.train_label) > 1 else "train_label"
                        volumes[vol_name] = (data, "segmentation", train_resolution, None)
                        print(f"      Loaded: {data.shape}, dtype={data.dtype}")
                    except Exception as e:
                        print(f"      Error loading {lbl_file}: {e}")
            else:
                # Single file
                data = read_volume(cfg.data.train_label)
                if data is not None:
                    # Convert 2D to 3D if needed
                    if data.ndim == 2:
                        data = data[None, :, :]  # (H, W) -> (1, H, W)
                        print(f"  Converted 2D train label to 3D: {data.shape}")
                        # Update resolution from 2D to 3D
                        if train_resolution and len(train_resolution) == 2:
                            train_resolution = (1.0,) + train_resolution  # Add z=1.0
                            print(f"  Updated train resolution to 3D: {train_resolution}")
                    volumes["train_label"] = (data, "segmentation", train_resolution, None)

    # Test data
    if mode in ["test", "both"]:
        if (
            hasattr(cfg, "inference")
            and hasattr(cfg.inference, "data")
            and hasattr(cfg.inference.data, "test_image")
            and cfg.inference.data.test_image
        ):
            print(f"Loading test image: {cfg.inference.data.test_image}")
            data = read_volume(cfg.inference.data.test_image)
            # Convert 2D to 3D if needed
            if data.ndim == 2:
                data = data[None, :, :]  # (H, W) -> (1, H, W)
                print(f"  Converted 2D test image to 3D: {data.shape}")
                # Update resolution from 2D to 3D
                if test_resolution and len(test_resolution) == 2:
                    test_resolution = (1.0,) + test_resolution  # Add z=1.0
                    print(f"  Updated test resolution to 3D: {test_resolution}")
            volumes["test_image"] = (data, "image", test_resolution, None)

        if (
            hasattr(cfg, "inference")
            and hasattr(cfg.inference, "data")
            and hasattr(cfg.inference.data, "test_label")
            and cfg.inference.data.test_label
        ):
            print(f"Loading test label: {cfg.inference.data.test_label}")
            data = read_volume(cfg.inference.data.test_label)
            # Convert 2D to 3D if needed
            if data.ndim == 2:
                data = data[None, :, :]  # (H, W) -> (1, H, W)
                print(f"  Converted 2D test label to 3D: {data.shape}")
                # Update resolution from 2D to 3D
                if test_resolution and len(test_resolution) == 2:
                    test_resolution = (1.0,) + test_resolution  # Add z=1.0
                    print(f"  Updated test resolution to 3D: {test_resolution}")
            volumes["test_label"] = (data, "segmentation", test_resolution, None)

    if not volumes:
        print(f"WARNING: No volumes found in config for mode='{mode}'")

    return volumes


def load_volumes_from_paths(
    volume_specs: List[str],
) -> Dict[str, Tuple[np.ndarray, str, Optional[Tuple], Optional[Tuple]]]:
    """
    Load volumes from path specifications.

    Args:
        volume_specs: List of volume specifications in format:
            - "path" - just path (type inferred from name)
            - "name:path" - name and path (type inferred from name)
            - "name:type:path" - name, type (image/seg), and path
            - "name:type:path:resolution" - with resolution (e.g., "30-6-6")
            - "name:type:path:resolution:offset" - with resolution and offset (e.g., "0-0-0")

    Returns:
        Dictionary mapping volume names to (data, type, resolution, offset) tuples
        where resolution and offset can be None (use defaults)
    """
    volumes = {}

    for spec in volume_specs:
        parts = spec.split(":")

        # Check if second part is a type specifier (image/seg/segmentation)
        has_explicit_type = len(parts) >= 3 and parts[1] in ["image", "img", "seg", "segmentation"]

        # Parse based on format
        if len(parts) == 1:
            # Just path: "path/to/file.h5"
            name = Path(parts[0]).stem
            path = parts[0]
            vol_type = None  # Infer later
            resolution = None
            offset = None
        elif len(parts) == 2:
            # name:path
            name, path = parts
            vol_type = None  # Infer later
            resolution = None
            offset = None
        elif has_explicit_type:
            # Format: name:type:path[:resolution[:offset]]
            name = parts[0]
            vol_type = "segmentation" if parts[1] in ["seg", "segmentation"] else "image"
            path = parts[2]

            # Parse optional resolution and offset
            if len(parts) >= 4:
                resolution = tuple(float(x) for x in parts[3].split("-"))
            else:
                resolution = None

            if len(parts) >= 5:
                offset = tuple(int(x) for x in parts[4].split("-"))
            else:
                offset = None
        else:
            # Legacy format: name:path:resolution[:offset]
            name = parts[0]
            path = parts[1]
            vol_type = None  # Infer later

            if len(parts) >= 3:
                resolution = tuple(float(x) for x in parts[2].split("-"))
            else:
                resolution = None

            if len(parts) >= 4:
                offset = tuple(int(x) for x in parts[3].split("-"))
            else:
                offset = None

        print(f"Loading {name}: {path}")
        if resolution:
            print(f"  Custom resolution: {resolution}")
        if offset:
            print(f"  Custom offset: {offset}")

        data = read_volume(path).squeeze()
        
        # Convert 2D to 3D if needed
        if data.ndim == 2:
            data = data[None, :, :]  # (H, W) -> (1, H, W)
            print(f"  Converted 2D to 3D: {data.shape}")
            # Update resolution from 2D to 3D if it exists
            if resolution and len(resolution) == 2:
                resolution = (1.0,) + resolution  # Add z=1.0
                print(f"  Updated resolution to 3D: {resolution}")

        # Infer type if not explicitly specified
        if vol_type is None:
            name_lower = name.lower()
            if any(keyword in name_lower for keyword in ["label", "seg", "gt", "pred", "mask"]):
                vol_type = "segmentation"
            else:
                vol_type = "image"

        print(f"  Volume type: {vol_type}")
        volumes[name] = (data, vol_type, resolution, offset)

    return volumes


def create_neuroglancer_layer(
    data: np.ndarray,
    resolution: Tuple[float, float, float],
    offset: Tuple[int, int, int] = (0, 0, 0),
    volume_type: str = "image",
) -> "neuroglancer.LocalVolume":
    """
    Create a Neuroglancer layer from volume data.

    Args:
        data: Volume data (3D or 4D numpy array)
        resolution: Voxel resolution in nm as (z, y, x)
        offset: Volume offset as (z, y, x)
        volume_type: 'image' or 'segmentation'

    Returns:
        Neuroglancer LocalVolume layer
    """
    # Handle 4D data (C, Z, Y, X) -> use first channel
    if data.ndim == 4:
        print(f"  4D volume detected: {data.shape}, using first channel")
        data = data[0]

    # Ensure 3D
    if data.ndim != 3:
        raise ValueError(f"Expected 3D volume, got shape {data.shape}")

    # Create coordinate space
    coord_space = neuroglancer.CoordinateSpace(
        names=["z", "y", "x"], units=["nm", "nm", "nm"], scales=resolution
    )

    print(f"  Shape: {data.shape}, Type: {volume_type}, Resolution: {resolution}")

    return neuroglancer.LocalVolume(
        data, dimensions=coord_space, volume_type=volume_type, voxel_offset=offset
    )


def visualize_volumes(
    volumes: Dict[str, Tuple],
    ip: str = "localhost",
    port: int = 9999,
    resolution: Tuple[float, float, float] = (30, 6, 6),
    offset: Tuple[int, int, int] = (0, 0, 0),
) -> "neuroglancer.Viewer":
    """
    Visualize volumes with Neuroglancer.

    Args:
        volumes: Dictionary mapping names to (data, type, resolution, offset) tuples
                 where resolution and offset can be None (use defaults)
        ip: Server IP address
        port: Server port
        resolution: Default voxel resolution in nm as (z, y, x)
        offset: Default volume offset as (z, y, x)

    Returns:
        Neuroglancer viewer instance
    """
    if not volumes:
        print("ERROR: No volumes to visualize")
        return None

    # Set up Neuroglancer server
    print(f"\nStarting Neuroglancer server on {ip}:{port}")
    neuroglancer.set_server_bind_address(bind_address=ip, bind_port=port)
    viewer = neuroglancer.Viewer()

    # Add all volumes as layers
    with viewer.txn() as state:
        for name, vol_data in volumes.items():
            print(f"\nAdding layer: {name}")

            # Handle both old format (data, type) and new format (data, type, resolution, offset)
            if len(vol_data) == 2:
                data, vol_type = vol_data
                vol_resolution = resolution  # Use default
                vol_offset = offset  # Use default
            else:
                data, vol_type, vol_resolution, vol_offset = vol_data
                # Use volume-specific values if provided, otherwise use defaults
                vol_resolution = vol_resolution if vol_resolution is not None else resolution
                vol_offset = vol_offset if vol_offset is not None else offset

            layer = create_neuroglancer_layer(data, vol_resolution, vol_offset, vol_type)
            state.layers.append(name=name, layer=layer)

    # Print viewer URL and instructions
    print("\n" + "=" * 70)
    print("Neuroglancer viewer ready!")
    print("=" * 70)
    print(f"\nOpen this URL in your browser:")
    print(f"  {viewer}")
    print(f"\nServer: {ip}:{port}")
    print(f"Volumes: {list(volumes.keys())}")
    print("\n" + "=" * 70)
    print("Interactive Python session - examine variables:")
    print("  viewer   - Neuroglancer viewer instance")
    print("  volumes  - Dictionary of loaded volumes")
    print("  For volume data: volumes['name'][0] (numpy array)")
    print("\nExit with: exit() or Ctrl+D")
    print("=" * 70 + "\n")

    return viewer


def main():
    """Main entry point."""
    args = parse_args()

    # Check for neuroglancer (after argparse so --help works without it)
    try:
        import neuroglancer as ng

        global neuroglancer
        neuroglancer = ng
    except ImportError:
        print("\nERROR: neuroglancer not installed.")
        print("Install with: pip install neuroglancer")
        print("Or: pip install neuroglancer-python\n")
        sys.exit(1)

    # Validate that at least one input source is provided
    # Empty strings count as no input
    has_config = bool(args.config)
    has_image = bool(args.image and args.image.strip())
    has_label = bool(args.label and args.label.strip())
    has_volumes = bool(args.volumes)

    if not any([has_config, has_image, has_label, has_volumes]):
        print("ERROR: At least one input source is required:")
        print("  --config CONFIG      Load from config file")
        print("  --image IMG          Load image volume")
        print("  --label LBL          Load label volume")
        print("  --volumes VOL...     Load multiple volumes")
        print(
            "\nExample: python scripts/visualize_neuroglancer.py --image img.tif --label label.h5"
        )
        sys.exit(1)

    # Load volumes based on input method (can combine multiple sources!)
    # Use global to make variables accessible in interactive mode
    global volumes, viewer, cfg
    volumes = {}
    cfg = None

    # Load from config first (if provided)
    if args.config:
        cfg = load_config(args.config)  # Store config for interactive access
        volumes.update(load_volumes_from_config(args.config, args.mode))

    # Add image/label (if provided and not empty strings)
    if args.image and args.image.strip():
        print(f"Loading image: {args.image}")
        data = read_volume(args.image)
        # Convert 2D to 3D if needed
        if data.ndim == 2:
            data = data[None, :, :]  # (H, W) -> (1, H, W)
            print(f"  Converted 2D image to 3D: {data.shape}")
        volumes["image"] = (data, "image", None, None)
    if args.label and args.label.strip():
        print(f"Loading label: {args.label}")
        data = read_volume(args.label)
        # Convert 2D to 3D if needed
        if data.ndim == 2:
            data = data[None, :, :]  # (H, W) -> (1, H, W)
            print(f"  Converted 2D label to 3D: {data.shape}")
        volumes["label"] = (data, "segmentation", None, None)

    # Add additional volumes (if provided) - these can override config volumes
    if args.volumes:
        volumes.update(load_volumes_from_paths(args.volumes))

    if not volumes:
        print("ERROR: No volumes loaded. Check your input paths.")
        sys.exit(1)

    # Start visualization (returns viewer for interactive access)
    viewer = visualize_volumes(
        volumes=volumes,
        ip=args.ip,
        port=args.port,
        resolution=tuple(args.resolution),
        offset=tuple(args.offset),
    )

    # Return viewer for interactive mode
    return viewer


if __name__ == "__main__":
    main()
