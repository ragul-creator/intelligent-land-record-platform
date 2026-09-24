"""Training, evaluation, and local prediction persistence for Phase C.2."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch import Tensor, nn
from torch.utils.data import DataLoader
from torchvision.transforms import functional as transforms

from ai.geoai.segmentation.building_dataset import WHUBuildingDataset
from ai.geoai.segmentation.metrics import SegmentationMetrics, binary_confusion, metrics_from_confusion
from ai.geoai.segmentation.model import BuildingSegmenter, ModelConfig, create_model, load_checkpoint, save_checkpoint
from ai.geoai.segmentation.runtime import HardwareInfo, choose_num_workers, select_device


DEFAULT_VALIDATION_THRESHOLDS = (0.30, 0.40, 0.50, 0.60, 0.70)


@dataclass(frozen=True)
class TrainingConfig:
    dataset_root: Path
    epochs: int = 1
    batch_size: int = 2
    learning_rate: float = 1e-4
    device: str = "auto"
    num_workers: int | None = None
    limit: int | None = None
    image_size: int | None = None
    seed: int = 42
    checkpoint_directory: Path = Path("data/models/buildings")
    resume: Path | None = None
    pretrained_backbone: bool = False


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _loader(dataset: WHUBuildingDataset, batch_size: int, device: torch.device, workers: int, shuffle: bool) -> DataLoader:
    settings: dict[str, object] = {"batch_size": batch_size, "shuffle": shuffle, "num_workers": workers, "pin_memory": device.type == "cuda"}
    if workers > 0:
        settings.update(persistent_workers=True, prefetch_factor=2)
    return DataLoader(dataset, **settings)


def _dice_loss(logits: Tensor, mask: Tensor, epsilon: float = 1e-7) -> Tensor:
    probability = torch.sigmoid(logits)
    intersection = (probability * mask).sum(dim=(1, 2, 3))
    denominator = probability.sum(dim=(1, 2, 3)) + mask.sum(dim=(1, 2, 3))
    return 1 - ((2 * intersection + epsilon) / (denominator + epsilon)).mean()


def _aggregate_metrics_sweep(
    model: BuildingSegmenter,
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


def train(config: TrainingConfig) -> dict[str, object]:
    """Run a real, configurable BCE+Dice training loop with explicit CUDA behavior."""
    if config.epochs <= 0 or config.batch_size <= 0:
        raise ValueError("epochs and batch_size must be greater than zero.")
    if config.learning_rate <= 0:
        raise ValueError("learning_rate must be greater than zero.")
    _seed_everything(config.seed)
    device, hardware = select_device(config.device)
    workers = choose_num_workers(config.num_workers)
    train_dataset = WHUBuildingDataset(config.dataset_root, "train", image_size=config.image_size, augment=True, limit=config.limit)
    validation_dataset = WHUBuildingDataset(config.dataset_root, "val", image_size=config.image_size, limit=config.limit)
    train_loader = _loader(train_dataset, config.batch_size, device, workers, shuffle=True)
    validation_loader = _loader(validation_dataset, config.batch_size, device, workers, shuffle=False)
    model_config = ModelConfig(pretrained_backbone=config.pretrained_backbone)
    model = create_model(model_config, device=device)
    start_epoch = 0
    if config.resume is not None:
        model, payload = load_checkpoint(config.resume, device=device)
        model_config = model.config
        optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
        if "optimizer_state" in payload:
            optimizer.load_state_dict(payload["optimizer_state"])
        start_epoch = int(payload.get("epoch", -1)) + 1
    else:
        optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    bce = nn.BCEWithLogitsLoss()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    checkpoint_dir = config.checkpoint_directory / run_id
    best_iou = -1.0
    best_checkpoint: Path | None = None
    history: list[dict[str, object]] = []
    try:
        for epoch in range(start_epoch, start_epoch + config.epochs):
            model.train()
            loss_total = 0.0
            batches = 0
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
                loss_total += float(loss.detach())
                batches += 1
            sweep = _aggregate_metrics_sweep(
                model, validation_loader, device
            )
            validation_threshold, validation_metrics = _best_threshold(sweep)
            validation_sweep = {
                f"{threshold:.2f}": metrics.to_dict()
                for threshold, metrics in sweep.items()
            }
            epoch_result = {
                "epoch": epoch,
                "train_loss": loss_total / max(1, batches),
                "validation": validation_metrics.to_dict(),
                "validation_threshold": validation_threshold,
                "validation_sweep": validation_sweep,
            }
            history.append(epoch_result)
            logging.info("GeoAI epoch result: %s", epoch_result)
            latest = save_checkpoint(
                checkpoint_dir / "latest.pt",
                model,
                optimizer,
                epoch=epoch,
                training_config=asdict(config),
                validation=validation_metrics.to_dict(),
                validation_threshold=validation_threshold,
                validation_sweep=validation_sweep,
            )
            if validation_metrics.iou > best_iou:
                best_iou = validation_metrics.iou
                best_checkpoint = save_checkpoint(
                    checkpoint_dir / "best.pt",
                    model,
                    optimizer,
                    epoch=epoch,
                    training_config=asdict(config),
                    validation=validation_metrics.to_dict(),
                    validation_threshold=validation_threshold,
                    validation_sweep=validation_sweep,
                )
    except torch.OutOfMemoryError as error:
        raise RuntimeError("CUDA out of memory. Retry with a smaller --batch-size; CPU fallback is disabled.") from error
    return {
        "run_id": run_id,
        "device": hardware.to_dict(),
        "checkpoint": str(best_checkpoint or latest),
        "history": history,
        "model_config": model_config.to_dict(),
        "recommended_threshold": (
            history[-1]["validation_threshold"] if history else 0.5
        ),
    }


def evaluate(dataset_root: str | Path, split: str, checkpoint: str | Path, *, device_request: str = "auto", batch_size: int = 2, num_workers: int | None = None, limit: int | None = None, image_size: int | None = None) -> dict[str, object]:
    device, hardware = select_device(device_request)
    model, payload = load_checkpoint(checkpoint, device=device)
    dataset = WHUBuildingDataset(dataset_root, split, image_size=image_size, limit=limit)
    sweep = _aggregate_metrics_sweep(
        model,
        _loader(
            dataset,
            batch_size,
            device,
            choose_num_workers(num_workers),
            False,
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


def infer_input(checkpoint: str | Path, input_path: str | Path, output_directory: str | Path, *, device_request: str = "auto", threshold: float = 0.5) -> dict[str, object]:
    """Persist pixel-space probability/mask/metadata outputs for one PNG or folder of PNGs."""
    source = Path(input_path)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1.")
    files = [source] if source.is_file() else sorted(source.glob("*.png"))
    if not files:
        raise ValueError("Input must be a PNG image or a directory containing PNG images.")
    device, hardware = select_device(device_request)
    model, _ = load_checkpoint(checkpoint, device=device)
    destination = Path(output_directory)
    destination.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for file in files:
        with Image.open(file) as opened:
            image = opened.convert("RGB")
            tensor = transforms.normalize(transforms.to_tensor(image), mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]).unsqueeze(0)
        with torch.inference_mode(), torch.amp.autocast(device_type=device.type, enabled=device.type == "cuda"):
            probability = model.probabilities(tensor.to(device, non_blocking=device.type == "cuda"))[0, 0].float().cpu().numpy()
        binary = probability >= threshold
        sample_id = file.stem
        np.save(destination / f"{sample_id}_probability.npy", probability)
        Image.fromarray((binary * 255).astype(np.uint8), mode="L").save(destination / f"{sample_id}_mask.png")
        predicted = probability[binary]
        record = {"sample_id": sample_id, "source_filename": file.name, "threshold": threshold, "model_version": model.config.model_version, "mean_probability_predicted_buildings": float(predicted.mean()) if predicted.size else 0.0, "predicted_building_pixel_fraction": float(binary.mean())}
        (destination / f"{sample_id}_metadata.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        records.append(record)
    return {"output_directory": str(destination), "predictions": records, "device": hardware.to_dict()}
