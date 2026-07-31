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
        "provisional_session_id",
        "session_stable",
        "returning_visitor",
        "greeting_kind",
        "greeting_eligible",
        "identity_similarity",
        "face_quality_score",
        "recognition_usable",
        "enrollment_usable",
        "scene_state",
        "person_count",
        "updated_at",
    )
    require(
        "ui/smart-office-ui/src/vision/useProximityGreeting.ts",
        "VITE_VISION_SOURCE",
        "remote-with-fallback",
        "greetedVisitRef",
        "REMOTE_REARM_ABSENCE_MS",
        "Welcome back",
        "triggerProximityGreeting",
        "RemoteVisionClient",
        "ProximityDetection | RemoteVisionDetection | null",
    )
    require(
        "ui/smart-office-ui/src/virtual-host/OperatorDrawer.tsx",
        "RTX vision server connected",
        "proximity.endpoint",
        "RTX 视觉实时调试",
        "Track ID",
        "Visitor Session",
        "Identity ID",
        "人脸质量 / 正脸度",
        "识别可用 / 注册可用",
        "协议 / 事件源",
    )
    require(
        "ui/smart-office-ui/.env.phase5.example",
        "VITE_VISION_SOURCE=remote",
        "VITE_VISION_SERVER_WS=ws://",
    )
    print("PASS: Phase 5.2 stable-session and returning-visitor greeting contracts are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
