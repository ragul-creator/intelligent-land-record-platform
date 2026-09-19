"""Swappable torchvision DeepLabV3 adapter for binary building segmentation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
from torch import Tensor, nn
from torchvision.models import ResNet50_Weights
from torchvision.models.segmentation import deeplabv3_resnet50


@dataclass(frozen=True)
class ModelConfig:
    architecture: str = "deeplabv3_resnet50"
    encoder: str = "resnet50"
    model_version: str = "building-segmentation-c2-v1"
    pretrained_backbone: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class BuildingSegmenter(nn.Module):
    """DeepLabV3-ResNet50 with a one-channel binary building-logit head."""

    def __init__(self, config: ModelConfig | None = None) -> None:
        super().__init__()
        self.config = config or ModelConfig()
        if self.config.architecture != "deeplabv3_resnet50":
            raise ValueError(f"Unsupported building architecture: {self.config.architecture}")
        weights_backbone = ResNet50_Weights.IMAGENET1K_V2 if self.config.pretrained_backbone else None
        self.network = deeplabv3_resnet50(weights=None, weights_backbone=weights_backbone)
        self.network.classifier[-1] = nn.Conv2d(256, 1, kernel_size=1)

    def forward(self, image: Tensor) -> Tensor:
        return self.network(image)["out"]

    @torch.inference_mode()
    def probabilities(self, image: Tensor) -> Tensor:
        return torch.sigmoid(self.forward(image))


def create_model(config: ModelConfig | None = None, *, device: torch.device | None = None) -> BuildingSegmenter:
    """Create the local model without hidden downloads unless pretrained_backbone is explicit."""
    model = BuildingSegmenter(config)
    if device is not None:
        model.to(device)
    return model


def save_checkpoint(
    path: str | Path,
    model: BuildingSegmenter,
    optimizer: torch.optim.Optimizer | None = None,
    **metadata: Any,
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"model_state": model.state_dict(), "model_config": model.config.to_dict(), **metadata}
    if optimizer is not None:
        payload["optimizer_state"] = optimizer.state_dict()
    torch.save(payload, destination)
    return destination


def load_checkpoint(path: str | Path, *, device: torch.device) -> tuple[BuildingSegmenter, dict[str, Any]]:
    # Checkpoints may have been created on Windows and contain serialized
    # pathlib.WindowsPath metadata. Linux cannot instantiate WindowsPath, so
    # temporarily map it to PosixPath only while deserializing the trusted
    # local checkpoint.
    import pathlib

    original_windows_path = pathlib.WindowsPath
    try:
        if pathlib.Path().anchor != "\\":
            pathlib.WindowsPath = pathlib.PosixPath
        payload = torch.load(Path(path), map_location=device, weights_only=False)
    finally:
        pathlib.WindowsPath = original_windows_path

    config = ModelConfig(**payload["model_config"])
    model = create_model(config, device=device)
    model.load_state_dict(payload["model_state"])
    # Inference tensors are float32; normalize checkpoint parameters to the
    # same dtype so Windows-trained checkpoints cannot leave the Linux
    # runtime with float64 parameters.
    model = model.float()
    model.eval()
    return model, payload
