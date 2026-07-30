from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the live Phase 1 camera and detector pipeline.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout_seconds
    last_status = None
    with httpx.Client(timeout=10.0) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(f"{args.base_url}/api/v1/status")
                response.raise_for_status()
                last_status = response.json()
                vision = last_status.get("vision") or {}
                camera = vision.get("camera") or {}
                detector = vision.get("detector") or {}
                if (
                    vision.get("status") == "ready"
                    and camera.get("running")
                    and int(camera.get("frame_id") or 0) > 0
                    and detector.get("ready")
                    and int(detector.get("inference_count") or 0) > 0
                ):
                    break
            except Exception:
                pass
            time.sleep(1.0)
        else:
            print("FAIL: Phase 1 did not become ready before timeout.", file=sys.stderr)
            print(last_status, file=sys.stderr)
            return 1

        detections = client.get(f"{args.base_url}/api/v1/detections")
        detections.raise_for_status()
        frame = client.get(f"{args.base_url}/api/v1/debug/frame.jpg")
        frame.raise_for_status()
        if frame.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            print("FAIL: debug frame is not image/jpeg", file=sys.stderr)
            return 1
        if len(frame.content) < 1000:
            print("FAIL: debug frame is unexpectedly small", file=sys.stderr)
            return 1

    vision = last_status["vision"]
    print("PASS: Phase 1 camera and person-detection pipeline is ready.")
    print(f"Camera frame: {vision['camera']['frame_id']}")
    print(f"Camera actual: {vision['camera']['actual']}")
    print(f"Detector providers: {vision['detector']['providers']}")
    print(f"Inference count: {vision['detector']['inference_count']}")
    print(f"Latest timings: {vision['last_timings']}")
    print(f"Latest person count: {detections.json().get('person_count')}")
    print(f"Debug JPEG bytes: {len(frame.content)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
