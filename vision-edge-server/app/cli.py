from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import load_config
from app.hardware import probe_camera, probe_gpu


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RTX Vision Edge Server Phase 0 utilities")
    parser.add_argument("command", choices=["probe-gpu", "probe-camera", "probe-all", "show-config"])
    parser.add_argument("--config", type=Path, default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config, config_path = load_config(args.config)
    if args.command == "probe-gpu":
        result = probe_gpu(config.gpu)
    elif args.command == "probe-camera":
        result = probe_camera(config.camera)
    elif args.command == "probe-all":
        result = {
            "config_path": str(config_path),
            "gpu": probe_gpu(config.gpu),
            "camera": probe_camera(config.camera),
        }
    else:
        result = {"config_path": str(config_path), "config": config.model_dump(mode="json")}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
