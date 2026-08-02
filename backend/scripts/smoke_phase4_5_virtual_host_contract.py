from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402
import smoke_result_center_admin_contract as result_center_admin_contract  # noqa: E402


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def assert_contains(text: str, *needles: str) -> None:
    for needle in needles:
        assert needle in text, f"Missing required contract text: {needle}"


def assert_not_contains(text: str, *needles: str) -> None:
    for needle in needles:
        assert needle not in text, f"Exhibition main-screen source leaked debug detail: {needle}"


def main() -> None:
    client = TestClient(app)

    intro = client.post(
        "/agent/turn",
        json={
            "conversation_id": "phase4-5-exhibition-contract",
            "text": "请介绍一下你自己",
            "language": "zh",
            "input_source": "text",
            "actor_context": {"type": "employee"},
        },
    )
    intro.raise_for_status()
    intro_payload = intro.json()
    assert intro_payload["route"] == "reception_knowledge"
    assert intro_payload["source_ids"] == ["company_profile:assistant_identity"]
    assert "PowerPoint" in intro_payload["spoken_text"]
    assert "Outlook" in intro_payload["spoken_text"]
    assert "您想先" in intro_payload["spoken_text"]
    assert "我已理解您的话" not in intro_payload["spoken_text"]
    assert "当前请求不需要创建办公任务" not in intro_payload["spoken_text"]
    assert "Phase 2" not in intro_payload["spoken_text"]

    company = client.post(
        "/agent/turn",
        json={
            "conversation_id": "phase4-5-exhibition-contract",
            "text": "请介绍一下 Smart Office 方案",
            "language": "zh",
            "input_source": "text",
            "actor_context": {"type": "employee"},
        },
    )
    company.raise_for_status()
    company_payload = company.json()
    assert company_payload["route"] == "reception_knowledge"
    assert company_payload["source_ids"] == ["company_profile:solution_overview"]
    assert "主屏" in company_payload["spoken_text"]
    assert "副屏" in company_payload["spoken_text"]
    assert "您更想了解" in company_payload["spoken_text"]

    profile = json.loads(read("data/company_knowledge/company_profile.json"))
    assert profile["content_version"].startswith("phase4-5-exhibition-guide")

    app_source = read("ui/smart-office-ui/src/virtual-host/VirtualHostApp.tsx")
    overlay_source = read("ui/smart-office-ui/src/virtual-host/ApprovalOverlay.tsx")
    drawer_source = read("ui/smart-office-ui/src/virtual-host/OperatorDrawer.tsx")
    caption_source = read("ui/smart-office-ui/src/virtual-host/LiveCaption.tsx")
    phase4_css = read("ui/smart-office-ui/src/virtual-host/VirtualHostPhase4.css")
    controller_source = read("ui/smart-office-ui/src/voice/useOfficeVoiceController.ts")
    realtime_source = read("ui/smart-office-ui/src/voice/realtimeAgentRuntime.ts")
    visit_bridge_source = read("ui/smart-office-ui/src/voice/visitRealtimeLeaseBridge.ts")
    command_recovery_source = read("ui/smart-office-ui/src/voice/commandSpeechRecovery.ts")
    proactive_loop_source = read("ui/smart-office-ui/src/vision/proactiveReceptionVoiceLoop.ts")
    semantic_source = read("ui/smart-office-ui/src/interaction/semanticInteractionInterpreter.ts")
    protected_results_source = read("ui/smart-office-ui/src/interaction/ProtectedResultCenterApp.tsx")
    voice_command_source = read("ui/smart-office-ui/src/interaction/interactionVoiceCommandInterpreter.ts")
    panel_bridge_source = read("ui/smart-office-ui/src/interaction/interactionPanelCommandBridge.ts")
    panel_host_source = read("ui/smart-office-ui/src/interaction/InteractionPanelHost.tsx")
    coordinator_source = read("ui/smart-office-ui/src/voice/preemptiveTurnCoordinator.ts")
    acceptance = read("PHASE4_5_EXHIBITION_ACCEPTANCE.md")

    assert_contains(
        app_source,
        "<ApprovalOverlay",
        "<OperatorDrawer",
        "controller.stopSpeaking",
        "controller.approve('cancel')",
        "VirtualHostPhase4.css",
    )
    assert_not_contains(
        app_source,
        "controller.route",
        "controller.permission",
        "controller.tool",
        "controller.verified",
        "controller.taskId",
    )

    assert_contains(
        overlay_source,
        "停止朗读",
        "取消整个任务",
        "创建草稿",
        "确认发送",
        "busyAction",
    )
    assert_contains(
        drawer_source,
        "界面语言",
        "演示身份",
        "办公控制请使用 Employee",
        "语音识别",
        "GPT Realtime",
        "停止朗读",
        "取消当前任务",
        "主屏不会显示任务编号、路由或验证详情",
    )
    assert_not_contains(
        drawer_source,
        "controller.runtime.connectionState",
        "controller.runtime.dataChannelState",
        "controller.taskId",
        "controller.route",
        "controller.permission",
        "controller.verified",
    )

    assert_contains(caption_source, "splitLyrics", "lyric-current", "user-live-caption")
    assert_contains(phase4_css, "exhibition-approval-card", "exhibition-operator-drawer")

    assert_contains(
        realtime_source,
        "class PersistentRealtimeAgent",
        "await this.stopOutput()",
        "AUDIO_COMPLETION_MAX_MS = 45_000",
        "async abortCapture",
        "async stopContinuousCapture",
    )
    assert_contains(
        visit_bridge_source,
        "realtimeAgent.shutdown()",
        "smartoffice:visit-revoked",
    )
    assert_contains(
        command_recovery_source,
        "one\\s*b",
        "active visitor language is Chinese",
        "command-transcript-recovered",
        "commandLanguage",
    )
    assert_contains(
        proactive_loop_source,
        "recoverTurnState",
        "resolveInteractionVoiceCommand",
        "executeInteractionPanelCommand",
        "preemptiveTurnCoordinator",
        "preferLatestUtterance",
    )
    assert_contains(
        semantic_source,
        "visitor_service_intent_classification",
        "我想登记",
        "contact",
        "recording",
        "transcript",
        "results",
    )
    assert_contains(
        protected_results_source,
        "管理员验证",
        "/api/result-center/admin/login",
        "X-SmartOffice-Visit-Id",
        "panel_instance_id",
        "每个新访客 Session 都必须重新输入管理员密码",
    )
    assert_not_contains(protected_results_source, "sessionStorage")
    assert_contains(
        voice_command_source,
        "stop_save_summarize",
        "show_contacts",
        "export_csv",
        "play_latest_recording",
        "open_directory",
    )
    assert_contains(
        panel_bridge_source,
        "startRecording",
        "stopAndSaveRecording",
        "summarizeRecording",
        "resultCenterAuthenticated",
    )
    assert_contains(
        panel_host_source,
        "smartoffice:visit-activated",
        "smartoffice:visit-revoked",
        "pendingCommands",
    )
    assert_contains(
        coordinator_source,
        "smartoffice:realtime-vad-speech-started",
        "preempt('visitor_barge_in')",
        "/cancel",
        "preferLatestUtterance",
    )
    assert_contains(controller_source, "await voiceOutputManager.stop()")
    assert_contains(controller_source, "smartoffice_voice_conversation_id")

    for scenario in (
        "Ordinary self-introduction",
        "Company / solution introduction",
        "Open PowerPoint",
        "Next slide",
        "Adjust volume",
        "Summarize the presentation",
        "Create Outlook draft",
        "Approve and send",
        "Interrupt speech",
        "Five-round continuous voice run",
    ):
        assert scenario in acceptance

    result_center_admin_contract.main()

    print(
        "PASS: Phase 4-5 includes Visit-scoped result protection, full panel voice "
        "actions, latest-command preemption, and existing exhibition guarantees."
    )
    print(
        "NOTE: Windows Office, Outlook, rightmost-display placement, real microphone "
        "barge-in timing, MediaRecorder, and Word opening remain local acceptance tests."
    )


if __name__ == "__main__":
    main()
