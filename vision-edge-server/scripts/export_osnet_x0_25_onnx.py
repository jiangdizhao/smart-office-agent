from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
import urllib.request
from pathlib import Path


OSNET_SOURCE_URL = (
    "https://raw.githubusercontent.com/KaiyangZhou/deep-person-reid/"
    "master/torchreid/models/osnet.py"
)
WEIGHTS_URL = (
    "https://huggingface.co/kaiyangzhou/osnet/resolve/main/"
    "osnet_x0_25_msmt17_combineall_256x128_amsgrad_ep150_stp60_"
    "lr0.0015_b64_fb10_softmax_labelsmooth_flip_jitter.pth?download=true"
)
WEIGHTS_SHA256 = "cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18"


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    print(f"Downloading {url}")
    urllib.request.urlretrieve(url, destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Export official OSNet x0.25 ReID weights to ONNX.")
    parser.add_argument("--output", default="models/osnet_x0_25.onnx")
    parser.add_argument("--work-dir", default=".cache/osnet_export")
    args = parser.parse_args()

    try:
        import torch
    except Exception as exc:
        print(f"PyTorch is required in the exporter environment: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    work_dir = Path(args.work_dir).resolve()
    source_path = work_dir / "osnet.py"
    weights_path = work_dir / "osnet_x0_25_msmt17.pth"
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    download(OSNET_SOURCE_URL, source_path)
    download(WEIGHTS_URL, weights_path)
    actual_hash = sha256(weights_path)
    if actual_hash.lower() != WEIGHTS_SHA256:
        print(
            f"Weight SHA-256 mismatch: expected {WEIGHTS_SHA256}, got {actual_hash}",
            file=sys.stderr,
        )
        return 1

    spec = importlib.util.spec_from_file_location("official_osnet", source_path)
    if spec is None or spec.loader is None:
        print("Could not load the downloaded OSNet architecture", file=sys.stderr)
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    model = module.osnet_x0_25(num_classes=1041, pretrained=False, loss="softmax")
    checkpoint = torch.load(weights_path, map_location="cpu", weights_only=False)
    state_dict = checkpoint.get("state_dict", checkpoint) if isinstance(checkpoint, dict) else checkpoint
    current = model.state_dict()
    matched = {}
    for key, value in state_dict.items():
        normalized = key[7:] if key.startswith("module.") else key
        if normalized in current and tuple(current[normalized].shape) == tuple(value.shape):
            matched[normalized] = value
    missing, unexpected = model.load_state_dict(matched, strict=False)
    print(f"Loaded {len(matched)} tensors; missing={len(missing)}, unexpected={len(unexpected)}")
    if len(matched) < 100:
        print("Too few checkpoint tensors matched the OSNet architecture", file=sys.stderr)
        return 1

    model.eval()
    dummy = torch.zeros((1, 3, 256, 128), dtype=torch.float32)
    with torch.no_grad():
        sample = model(dummy)
    print(f"Feature shape before export: {tuple(sample.shape)}")

    torch.onnx.export(
        model,
        dummy,
        output_path,
        input_names=["images"],
        output_names=["embeddings"],
        dynamic_axes={"images": {0: "batch"}, "embeddings": {0: "batch"}},
        opset_version=17,
        do_constant_folding=True,
    )

    import onnx

    onnx_model = onnx.load(output_path)
    onnx.checker.check_model(onnx_model)
    print(f"PASS: OSNet x0.25 ONNX exported to {output_path}")
    print(f"Output size: {output_path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
