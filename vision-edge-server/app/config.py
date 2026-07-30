from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(StrictModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8015, ge=1, le=65535)
    heartbeat_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.strip().upper()


class CameraSettings(StrictModel):
    enabled: bool = True
    probe_on_startup: bool = True
    device_index: int = Field(default=0, ge=0)
    requested_width: int = Field(default=3840, ge=1)
    requested_height: int = Field(default=2160, ge=1)
    requested_fps: float = Field(default=30.0, gt=0)
    preferred_backends: list[Literal["DSHOW", "MSMF", "ANY"]] = Field(
        default_factory=lambda: ["DSHOW", "MSMF", "ANY"]
    )
    warmup_frames: int = Field(default=3, ge=0, le=60)
    sample_frames: int = Field(default=5, ge=1, le=60)


class GpuSettings(StrictModel):
    probe_on_startup: bool = True
    require_cuda: bool = False
    nvidia_smi_timeout_seconds: float = Field(default=5.0, gt=0, le=30.0)


class AppConfig(StrictModel):
    service_name: str = "rtx-vision-edge-server"
    version: str = "0.1.0"
    phase: str = "phase0_service_skeleton"
    server: ServerSettings = Field(default_factory=ServerSettings)
    camera: CameraSettings = Field(default_factory=CameraSettings)
    gpu: GpuSettings = Field(default_factory=GpuSettings)


def default_config_path() -> Path:
    configured = os.getenv("VISION_CONFIG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[1] / "config" / "vision.yaml").resolve()


def load_config(path: str | Path | None = None) -> tuple[AppConfig, Path]:
    config_path = Path(path).expanduser().resolve() if path else default_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"Vision config does not exist: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(raw), config_path
