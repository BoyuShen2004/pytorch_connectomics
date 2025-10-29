"""
MONAI model wrappers with standard interface.

Provides wrappers for MONAI native models (BasicUNet, UNet, UNETR, SwinUNETR)
that conform to the ConnectomicsModel interface.

Uses Hydra/OmegaConf configuration.
"""

import torch
import torch.nn as nn
from typing import Union, Dict

try:
    from monai.networks.nets import BasicUNet, UNet, UNETR, SwinUNETR
    MONAI_AVAILABLE = True
except ImportError:
    MONAI_AVAILABLE = False

from .base import ConnectomicsModel
from .registry import register_architecture


class MONAIModelWrapper(ConnectomicsModel):
    """
    Wrapper for MONAI models to provide ConnectomicsModel interface.

    MONAI models output single-scale tensors, so deep supervision is not supported.
    """

    def __init__(self, model: nn.Module):
        super().__init__()
        self.model = model
        self.supports_deep_supervision = False
        self.output_scales = 1

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass through MONAI model."""
        # For 2D models, squeeze the depth dimension if present
        if x.dim() == 5 and x.size(2) == 1:  # [B, C, 1, H, W] -> [B, C, H, W]
            x = x.squeeze(2)
        
        # Forward through model
        output = self.model(x)
        
        # For 2D models, add back the depth dimension if needed for sliding window inference
        if output.dim() == 4 and x.dim() == 5:  # [B, C, H, W] -> [B, C, 1, H, W]
            output = output.unsqueeze(2)
        
        return output


def _check_monai_available():
    """Check if MONAI is installed."""
    if not MONAI_AVAILABLE:
        raise ImportError(
            "MONAI is not installed. Install with: pip install monai\n"
            "Or: pip install 'monai[all]' for full functionality"
        )


@register_architecture('monai_basic_unet3d')
def build_basic_unet(cfg) -> ConnectomicsModel:
    """
    Build MONAI BasicUNet - simple and fast U-Net (2D or 3D).

    A straightforward U-Net implementation with configurable features.
    Good for quick experiments and baseline models.

    Config parameters:
        - model.in_channels: Number of input channels (default: 1)
        - model.out_channels: Number of output classes (default: 1)
        - model.spatial_dims: Spatial dimensions, 2 or 3 (default: auto-inferred from input_size)
        - model.input_size: Input patch size [H, W] for 2D or [D, H, W] for 3D
        - model.filters: Feature map sizes for each level (default: [32, 64, 128, 256, 512])
        - model.dropout: Dropout rate (default: 0.0)
        - model.activation: Activation function (default: 'relu')
        - model.norm: Normalization type (default: 'batch')

    Args:
        cfg: Hydra config object

    Returns:
        MONAIModelWrapper containing BasicUNet
    """
    _check_monai_available()

    in_channels = cfg.model.in_channels
    out_channels = cfg.model.out_channels

    # Auto-infer spatial_dims from input_size length
    if hasattr(cfg.model, 'input_size') and cfg.model.input_size:
        spatial_dims = len(cfg.model.input_size)
    else:
        raise ValueError(
            "model.input_size must be specified in config. "
            "Use [H, W] for 2D or [D, H, W] for 3D. "
            "spatial_dims will be automatically inferred from input_size length."
        )

    # BasicUNet requires exactly 6 feature levels
    base_features = list(cfg.model.filters) if hasattr(cfg.model, 'filters') else [32, 64, 128, 256, 512]
    while len(base_features) < 6:
        base_features.append(base_features[-1] * 2)
    features = tuple(base_features[:6])

    model = BasicUNet(
        spatial_dims=spatial_dims,
        in_channels=in_channels,
        out_channels=out_channels,
        features=features,
        dropout=getattr(cfg.model, 'dropout', 0.0),
        act=getattr(cfg.model, 'activation', 'relu'),
        norm=getattr(cfg.model, 'norm', 'batch'),
    )

    return MONAIModelWrapper(model)


@register_architecture('monai_unet')
def build_monai_unet(cfg) -> ConnectomicsModel:
    """
    Build MONAI UNet with residual units (2D or 3D).

    A more advanced U-Net with residual connections in each block.
    Better performance than BasicUNet but slightly slower.

    Config parameters:
        - model.in_channels: Number of input channels (default: 1)
        - model.out_channels: Number of output classes (default: 1)
        - model.spatial_dims: Spatial dimensions, 2 or 3 (default: auto-inferred from input_size)
        - model.input_size: Input patch size [H, W] for 2D or [D, H, W] for 3D
        - model.filters: Feature map sizes for each level (default: [32, 64, 128, 256, 512])
        - model.num_res_units: Number of residual units per block (default: 2)
        - model.kernel_size: Kernel size for convolutions (default: 3)
        - model.norm: Normalization type (default: 'batch')
        - model.dropout: Dropout rate (default: 0.0)

    Args:
        cfg: Hydra config object

    Returns:
        MONAIModelWrapper containing UNet
    """
    _check_monai_available()

    # Auto-infer spatial_dims from input_size length
    if hasattr(cfg.model, 'input_size') and cfg.model.input_size:
        spatial_dims = len(cfg.model.input_size)
    else:
        raise ValueError(
            "model.input_size must be specified in config. "
            "Use [H, W] for 2D or [D, H, W] for 3D. "
            "spatial_dims will be automatically inferred from input_size length."
        )
    features = list(cfg.model.filters) if hasattr(cfg.model, 'filters') else [32, 64, 128, 256, 512]
    channels = features[:5]  # Limit to 5 levels
    strides = [2] * (len(channels) - 1)  # 2x downsampling at each level

    # Handle normalization type and parameters
    norm_type = getattr(cfg.model, 'norm', 'batch')
    if norm_type == 'group':
        # For GroupNorm, we need to specify num_groups
        num_groups = getattr(cfg.model, 'num_groups', 8)
        norm = ("group", {"num_groups": num_groups})
    else:
        norm = norm_type

    model = UNet(
        spatial_dims=spatial_dims,
        in_channels=cfg.model.in_channels,
        out_channels=cfg.model.out_channels,
        channels=channels,
        strides=strides,
        num_res_units=getattr(cfg.model, 'num_res_units', 2),
        kernel_size=getattr(cfg.model, 'kernel_size', 3),
        norm=norm,
        dropout=getattr(cfg.model, 'dropout', 0.0),
    )

    return MONAIModelWrapper(model)


@register_architecture('monai_unetr')
def build_unetr(cfg) -> ConnectomicsModel:
    """
    Build MONAI UNETR (Transformer-based U-Net).

    Uses Vision Transformer (ViT) as encoder and CNN decoder.
    Good for large-scale 3D volumes but requires more memory.

    Config parameters:
        - model.in_channels: Number of input channels (default: 1)
        - model.out_channels: Number of output classes (default: 1)
        - model.input_size: Input patch size [D, H, W] (required)
        - model.feature_size: Base feature size (default: 16)
        - model.hidden_size: Transformer hidden size (default: 768)
        - model.mlp_dim: MLP dimension in transformer (default: 3072)
        - model.num_heads: Number of attention heads (default: 12)
        - model.pos_embed: Position embedding type (default: 'perceptron')
        - model.norm: Normalization type (default: 'instance')
        - model.dropout: Dropout rate (default: 0.0)

    Args:
        cfg: Hydra config object

    Returns:
        MONAIModelWrapper containing UNETR
    """
    _check_monai_available()

    model = UNETR(
        in_channels=cfg.model.in_channels,
        out_channels=cfg.model.out_channels,
        img_size=cfg.model.input_size,
        feature_size=getattr(cfg.model, 'feature_size', 16),
        hidden_size=getattr(cfg.model, 'hidden_size', 768),
        mlp_dim=getattr(cfg.model, 'mlp_dim', 3072),
        num_heads=getattr(cfg.model, 'num_heads', 12),
        pos_embed=getattr(cfg.model, 'pos_embed', 'perceptron'),
        norm_name=getattr(cfg.model, 'norm', 'instance'),
        dropout_rate=getattr(cfg.model, 'dropout', 0.0),
    )

    return MONAIModelWrapper(model)


@register_architecture('monai_swin_unetr')
def build_swin_unetr(cfg) -> ConnectomicsModel:
    """
    Build MONAI Swin UNETR (Swin Transformer U-Net).

    Uses Swin Transformer as encoder with hierarchical feature maps.
    State-of-the-art performance but computationally expensive.

    Config parameters:
        - model.in_channels: Number of input channels (default: 1)
        - model.out_channels: Number of output classes (default: 1)
        - model.input_size: Input patch size [D, H, W] (required)
        - model.feature_size: Base feature size (default: 48)
        - model.use_checkpoint: Use gradient checkpointing (default: False)
        - model.dropout: Dropout rate (default: 0.0)
        - model.attn_drop_rate: Attention dropout rate (default: 0.0)
        - model.dropout_path_rate: Stochastic depth rate (default: 0.0)

    Args:
        cfg: Hydra config object

    Returns:
        MONAIModelWrapper containing SwinUNETR
    """
    _check_monai_available()

    model = SwinUNETR(
        img_size=cfg.model.input_size,
        in_channels=cfg.model.in_channels,
        out_channels=cfg.model.out_channels,
        feature_size=getattr(cfg.model, 'feature_size', 48),
        use_checkpoint=getattr(cfg.model, 'use_checkpoint', False),
        drop_rate=getattr(cfg.model, 'dropout', 0.0),
        attn_drop_rate=getattr(cfg.model, 'attn_drop_rate', 0.0),
        dropout_path_rate=getattr(cfg.model, 'dropout_path_rate', 0.0),
    )

    return MONAIModelWrapper(model)


__all__ = [
    'MONAIModelWrapper',
    'build_basic_unet',
    'build_monai_unet',
    'build_unetr',
    'build_swin_unetr',
]