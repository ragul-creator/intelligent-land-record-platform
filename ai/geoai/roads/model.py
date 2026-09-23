"""Separate local DeepLabV3 adapter for binary road segmentation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import pathlib

import torch
from torch import Tensor, nn
from torchvision.models import ResNet50_Weights
from torchvision.models.segmentation import deeplabv3_resnet50


@dataclass(frozen=True)
class RoadModelConfig:
    architecture: str = "deeplabv3_resnet50"
    encoder: str = "resnet50"
    model_version: str = "road-segmentation-h2b5-v1"
    pretrained_backbone: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RoadSegmenter(nn.Module):
    """One-channel road-logit model. It never loads pretrained weights implicitly."""

    def __init__(self, config: RoadModelConfig | None = None) -> None:
        super().__init__()
        self.config = config or RoadModelConfig()
        if self.config.architecture != "deeplabv3_resnet50":
            raise ValueError(f"Unsupported road architecture: {self.config.architecture}")
        backbone = ResNet50_Weights.IMAGENET1K_V2 if self.config.pretrained_backbone else None
        self.network = deeplabv3_resnet50(weights=None, weights_backbone=backbone)
        self.network.classifier[-1] = nn.Conv2d(256, 1, kernel_size=1)

    def forward(self, image: Tensor) -> Tensor:
        return self.network(image)["out"]

    @torch.inference_mode()
    def probabilities(self, image: Tensor) -> Tensor:
        return torch.sigmoid(self.forward(image))


def create_model(config: RoadModelConfig | None = None, *, device: torch.device | None = None) -> RoadSegmenter:
    model = RoadSegmenter(config)
    if device is not None:
        model.to(device)
    return model


def save_checkpoint(path: str | Path, model: RoadSegmenter, **metadata: Any) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"model_state": model.state_dict(), "model_config": model.config.to_dict(), **metadata}, destination)
    return destination


def load_checkpoint(path: str | Path, *, device: torch.device) -> tuple[RoadSegmenter, dict[str, Any]]:
    checkpoint = Path(path)
    if not checkpoint.is_file():
        raise RuntimeError("Road GeoAI is unavailable: GEOAI_ROAD_CHECKPOINT is not configured to a readable checkpoint.")
    # Checkpoints may have been created on Windows but loaded in the Linux worker.
    original_windows_path = pathlib.WindowsPath
    try:
        if Path().anchor != "\\":
            pathlib.WindowsPath = pathlib.PosixPath
        payload = torch.load(checkpoint, map_location=device, weights_only=False)
    finally:
        pathlib.WindowsPath = original_windows_path
    if not isinstance(payload, dict) or "model_state" not in payload or "model_config" not in payload:
        raise RuntimeError("Road GeoAI checkpoint is invalid or does not contain a road model configuration.")
    config = RoadModelConfig(**payload["model_config"])
    if not config.model_version.startswith("road-"):
        raise RuntimeError("Road GeoAI checkpoint is not identified as a road segmentation model.")
    model = create_model(config, device=device).float()
    model.load_state_dict(payload["model_state"])
    model.eval()
    return model, payload
