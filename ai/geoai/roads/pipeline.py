"""Short, real training and evaluation entry points for the separate road model."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from torch import Tensor, nn
from torch.utils.data import DataLoader

from ai.geoai.roads.dataset import RoadSegmentationDataset
from ai.geoai.roads.model import RoadModelConfig, RoadSegmenter, create_model, load_checkpoint, save_checkpoint
from ai.geoai.segmentation.metrics import SegmentationMetrics, binary_confusion, metrics_from_confusion
from ai.geoai.segmentation.runtime import choose_num_workers, select_device


DEFAULT_VALIDATION_THRESHOLDS = (0.30, 0.40, 0.50, 0.60, 0.70)


@dataclass(frozen=True)
class RoadTrainingConfig:
    dataset_root: Path
    epochs: int = 1
    batch_size: int = 2
    learning_rate: float = 1e-4
    device: str = "auto"
    num_workers: int | None = None
    limit: int | None = None
    image_size: int | None = None
    seed: int = 42
    checkpoint_directory: Path = Path("data/models/roads")
    resume: Path | None = None
    pretrained_backbone: bool = False


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _loader(dataset: RoadSegmentationDataset, *, batch_size: int, device: torch.device, workers: int, shuffle: bool) -> DataLoader:
    parameters: dict[str, object] = {"batch_size": batch_size, "shuffle": shuffle, "num_workers": workers, "pin_memory": device.type == "cuda"}
    if workers > 0:
        parameters.update(persistent_workers=True, prefetch_factor=2)
    return DataLoader(dataset, **parameters)


def _dice_loss(logits: Tensor, mask: Tensor, epsilon: float = 1e-7) -> Tensor:
    probability = torch.sigmoid(logits)
    intersection = (probability * mask).sum(dim=(1, 2, 3))
    denominator = probability.sum(dim=(1, 2, 3)) + mask.sum(dim=(1, 2, 3))
    return 1 - ((2 * intersection + epsilon) / (denominator + epsilon)).mean()


def _metrics_sweep(
    model: RoadSegmenter,
    loader: DataLoader,
    device: torch.device,
    thresholds: tuple[float, ...] = DEFAULT_VALIDATION_THRESHOLDS,
) -> dict[float, SegmentationMetrics]:
    totals = {threshold: [0, 0, 0] for threshold in thresholds}
    model.eval()
    with torch.inference_mode():
        for batch in loader:
            image = batch["image"].to(
                device, non_blocking=device.type == "cuda"
            )
            mask = batch["mask"].to(
                device, non_blocking=device.type == "cuda"
            )
            with torch.amp.autocast(
                device_type=device.type, enabled=device.type == "cuda"
            ):
                probability = model.probabilities(image)
            for threshold in thresholds:
                tp, fp, fn = binary_confusion(
                    probability, mask, threshold=threshold
                )
                totals[threshold][0] += tp
                totals[threshold][1] += fp
                totals[threshold][2] += fn
    return {
        threshold: metrics_from_confusion(*counts)
        for threshold, counts in totals.items()
    }


def _best_threshold(
    sweep: dict[float, SegmentationMetrics],
) -> tuple[float, SegmentationMetrics]:
    return max(
        sweep.items(),
        key=lambda item: (
            item[1].iou,
            item[1].dice,
            item[1].precision,
            -abs(item[0] - 0.5),
        ),
    )


def train(config: RoadTrainingConfig) -> dict[str, object]:
    """Train a road-only BCE+Dice checkpoint; callers control data and run duration."""
    if config.epochs <= 0 or config.batch_size <= 0 or config.learning_rate <= 0:
        raise ValueError("epochs, batch_size, and learning_rate must be greater than zero.")
    _seed_everything(config.seed)
    device, hardware = select_device(config.device)
    workers = choose_num_workers(config.num_workers)
    train_loader = _loader(
        RoadSegmentationDataset(
            config.dataset_root,
            "train",
            image_size=config.image_size,
            limit=config.limit,
            augment=True,
        ),
        batch_size=config.batch_size,
        device=device,
        workers=workers,
        shuffle=True,
    )
    validation_loader = _loader(
        RoadSegmentationDataset(
            config.dataset_root,
            "val",
            image_size=config.image_size,
            limit=config.limit,
        ),
        batch_size=config.batch_size,
        device=device,
        workers=workers,
        shuffle=False,
    )
    model, start_epoch = create_model(
        RoadModelConfig(pretrained_backbone=config.pretrained_backbone),
        device=device,
    ), 0
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    if config.resume is not None:
        model, payload = load_checkpoint(config.resume, device=device)
        if "optimizer_state" in payload:
            optimizer.load_state_dict(payload["optimizer_state"])
        start_epoch = int(payload.get("epoch", -1)) + 1
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    bce = nn.BCEWithLogitsLoss()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = config.checkpoint_directory / run_id
    best_iou, best_checkpoint, history = -1.0, None, []
    try:
        for epoch in range(start_epoch, start_epoch + config.epochs):
            model.train()
            total, batches = 0.0, 0
            for batch in train_loader:
                image = batch["image"].to(device, non_blocking=device.type == "cuda")
                mask = batch["mask"].to(device, non_blocking=device.type == "cuda")
                optimizer.zero_grad(set_to_none=True)
                with torch.amp.autocast(device_type=device.type, enabled=device.type == "cuda"):
                    logits = model(image)
                    loss = bce(logits, mask) + _dice_loss(logits, mask)
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
                total, batches = total + float(loss.detach()), batches + 1
            sweep = _metrics_sweep(model, validation_loader, device)
            validation_threshold, validation_metrics = _best_threshold(sweep)
            validation = validation_metrics.to_dict()
            validation_sweep = {
                f"{threshold:.2f}": metrics.to_dict()
                for threshold, metrics in sweep.items()
            }
            history.append(
                {
                    "epoch": epoch,
                    "train_loss": total / max(1, batches),
                    "validation": validation,
                    "validation_threshold": validation_threshold,
                    "validation_sweep": validation_sweep,
                }
            )
            checkpoint_metadata = {
                "optimizer_state": optimizer.state_dict(),
                "epoch": epoch,
                "training_config": asdict(config),
                "validation": validation,
                "validation_threshold": validation_threshold,
                "validation_sweep": validation_sweep,
            }
            latest = save_checkpoint(
                output_dir / "latest.pt",
                model,
                **checkpoint_metadata,
            )
            if validation["iou"] > best_iou:
                best_iou = validation["iou"]
                best_checkpoint = save_checkpoint(
                    output_dir / "best.pt",
                    model,
                    **checkpoint_metadata,
                )
    except torch.OutOfMemoryError as error:
        raise RuntimeError("CUDA out of memory. Retry with a smaller --batch-size; CPU fallback is disabled.") from error
    return {
        "run_id": run_id,
        "device": hardware.to_dict(),
        "checkpoint": str(best_checkpoint or latest),
        "history": history,
        "model_config": model.config.to_dict(),
        "recommended_threshold": (
            history[-1]["validation_threshold"] if history else 0.5
        ),
    }


def evaluate(dataset_root: str | Path, split: str, checkpoint: str | Path, *, device_request: str = "auto", batch_size: int = 2, num_workers: int | None = None, limit: int | None = None, image_size: int | None = None) -> dict[str, object]:
    device, hardware = select_device(device_request)
    model, payload = load_checkpoint(checkpoint, device=device)
    dataset = RoadSegmentationDataset(dataset_root, split, image_size=image_size, limit=limit)
    sweep = _metrics_sweep(
        model,
        _loader(
            dataset,
            batch_size=batch_size,
            device=device,
            workers=choose_num_workers(num_workers),
            shuffle=False,
        ),
        device,
    )
    best_threshold, metrics = _best_threshold(sweep)
    return {
        "split": split,
        "metrics": metrics.to_dict(),
        "best_threshold": best_threshold,
        "threshold_sweep": {
            f"{threshold:.2f}": value.to_dict()
            for threshold, value in sweep.items()
        },
        "device": hardware.to_dict(),
        "model_version": model.config.model_version,
        "checkpoint_epoch": payload.get("epoch"),
    }
