"""Hardware selection and DataLoader defaults for stable CUDA-first training."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass

import torch


@dataclass(frozen=True)
class HardwareInfo:
    selected_device: str
    cuda_available: bool
    cuda_version: str | None
    gpu_name: str | None
    total_vram_bytes: int | None
    cpu_logical_cores: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def select_device(requested: str = "auto") -> tuple[torch.device, HardwareInfo]:
    """Select CUDA whenever auto mode finds a working CUDA device; never silently fall back."""
    if requested not in {"auto", "cuda", "cpu"}:
        raise ValueError("Device must be one of: auto, cuda, cpu.")
    cuda_available = torch.cuda.is_available()
    if requested == "cuda" and not cuda_available:
        raise RuntimeError("CUDA was requested but no working CUDA device is available.")
    selected = "cuda" if requested == "cuda" or (requested == "auto" and cuda_available) else "cpu"
    gpu_name = total_vram = None
    if cuda_available:
        properties = torch.cuda.get_device_properties(0)
        gpu_name = properties.name
        total_vram = properties.total_memory
    info = HardwareInfo(
        selected_device=selected,
        cuda_available=cuda_available,
        cuda_version=torch.version.cuda,
        gpu_name=gpu_name,
        total_vram_bytes=total_vram,
        cpu_logical_cores=os.cpu_count() or 1,
    )
    logging.info("GeoAI hardware: %s", info.to_dict())
    if selected == "cuda":
        torch.backends.cudnn.benchmark = True
    return torch.device(selected), info


def choose_num_workers(requested: int | None = None) -> int:
    """Leave Windows/system capacity available rather than consuming every logical core."""
    if requested is not None:
        if requested < 0:
            raise ValueError("num_workers must be zero or greater.")
        return requested
    cores = os.cpu_count() or 1
    return min(8, max(0, (cores - 2) // 2))
