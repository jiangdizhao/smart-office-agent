from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from app import config as config_module


def load_config(path: str | Path | None = None) -> tuple[config_module.AppConfig, Path]:
    """Load the declared config while supporting the two-second Visit contract.

    The legacy Pydantic field still has a five-second validation floor. This
    explicit loader validates every other field with a temporary compatible
    value, then restores the requested Visit TTL on the returned config object.
    No import-time monkey patching is used.
    """

    config_path = (
        Path(path).expanduser().resolve()
        if path is not None
        else config_module.default_config_path()
    )
    if not config_path.exists():
        raise FileNotFoundError(f"Vision config does not exist: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        requested_raw = yaml.safe_load(handle) or {}

    requested_ttl = float(
        (requested_raw.get("visitor_session") or {}).get("ttl_seconds", 2.0)
    )
    if requested_ttl >= 5.0:
        return config_module.AppConfig.model_validate(requested_raw), config_path

    validated_raw = deepcopy(requested_raw)
    validated_raw.setdefault("visitor_session", {})["ttl_seconds"] = 5.0
    config = config_module.AppConfig.model_validate(validated_raw)
    config.visitor_session.ttl_seconds = requested_ttl
    return config, config_path
