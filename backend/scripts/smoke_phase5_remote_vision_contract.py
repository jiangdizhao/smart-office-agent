from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def require(path: str, *needles: str) -> None:
    target = ROOT / path
    if not target.exists():
        raise SystemExit(f"FAIL: missing {path}")
    text = target.read_text(encoding="utf-8")
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"FAIL: {path} does not contain {needle!r}")


def main() -> int:
    require(
        "ui/smart-office-ui/src/vision/remoteVisionClient.ts",
        "class RemoteVisionClient",
        "get_client_state",
        "client_state_snapshot",
        "visitor_session_id",
        "greeting_eligible",
    )
    require(
        "ui/smart-office-ui/src/vision/useProximityGreeting.ts",
        "VITE_VISION_SOURCE",
        "remote-with-fallback",
        "greetedSessionsRef",
        "triggerProximityGreeting",
        "RemoteVisionClient",
    )
    require(
        "ui/smart-office-ui/src/virtual-host/OperatorDrawer.tsx",
        "RTX vision server connected",
        "proximity.endpoint",
    )
    require(
        "ui/smart-office-ui/.env.phase5.example",
        "VITE_VISION_SOURCE=remote",
        "VITE_VISION_SERVER_WS=ws://",
    )
    print("PASS: Phase 5 Smart Office remote-vision client contract is present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
