from __future__ import annotations

import sys
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402
from app.models import TaskGraph, TaskStep  # noqa: E402
from app.state_store import state_store, utc_now  # noqa: E402
from app.visitor_memory_store import visitor_memory_store  # noqa: E402


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def detection(
    visit_id: str,
    *,
    language: str = "en",
    greeting_kind: str = "new_anonymous",
    identity_id: str | None = None,
    display_name: str | None = None,
) -> dict:
    return {
        "language": language,
        "actor_type": "visitor",
        "body_area_ratio": 0.31,
        "body_confidence": 0.91,
        "face_area_ratio": 0.04,
        "face_confidence": 0.94,
        "face_inside_body": True,
        "confidence": 0.91,
        "frontal_score": 1.0,
        "center_x": 0.5,
        "center_y": 0.5,
        "stable_frames": 4,
        "detector": "contract-person+face",
        "track_id": 7,
        "visitor_session_id": visit_id,
        "visit_id": visit_id,
        "session_stable": True,
        "session_age_seconds": 1.2,
        "session_recovery_count": 0,
        "returning_visitor": greeting_kind != "new_anonymous",
        "greeting_kind": greeting_kind,
        "identity_id": identity_id,
        "display_name": display_name,
        "identity_similarity": 0.81 if identity_id else None,
    }


def wait_for_memory(identity_id: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        value = visitor_memory_store.load(identity_id)
        if value is not None:
            return value
        time.sleep(0.05)
    raise AssertionError(f"Background visitor memory was not saved for {identity_id}")


def create_owned_dummy_task(conversation_id: str, visit_id: str) -> str:
    now = utc_now()
    graph = TaskGraph(
        source="contract",
        created_at=now,
        steps=[
            TaskStep(
                step_id=f"step-{uuid4().hex}",
                index=1,
                title="Contract pending task",
                tool_name="presentation_get_status",
                status="pending",
                created_at=now,
            )
        ],
    )
    task = state_store.create_task(
        user_request="contract pending task",
        execute=True,
        task_graph=graph,
        owner_conversation_id=conversation_id,
        owner_visit_id=visit_id,
        owner_actor_type="visitor",
    )
    state_store.set_status(task.task_id, "running", summary="contract task running")
    return task.task_id


def main() -> None:
    client = TestClient(app)
    suffix = uuid4().hex[:10]
    anonymous_conversation = f"conversation-visit-lifecycle-{suffix}"
    visit_a = f"visit-anonymous-a-{suffix}"
    visit_b = f"visit-anonymous-b-{suffix}"

    initial = client.get(
        f"/api/conversations/{anonymous_conversation}",
        params={"language": "en", "actor_type": "visitor"},
    )
    initial.raise_for_status()
    assert initial.json()["state"]["conversation_phase"] == "standby"

    greeting_a = client.post(
        f"/api/conversations/{anonymous_conversation}/proximity-greeting",
        json=detection(visit_a),
    )
    greeting_a.raise_for_status()
    payload_a = greeting_a.json()
    assert payload_a["triggered"] is True
    assert payload_a["visit_id"] == visit_a
    assert payload_a["greeting"].startswith("Welcome to our office.")
    assert "I am Sara" in payload_a["greeting"]

    duplicate = client.post(
        f"/api/conversations/{anonymous_conversation}/proximity-greeting",
        json={**detection(visit_a), "stable_frames": 5},
    )
    duplicate.raise_for_status()
    assert duplicate.json()["triggered"] is False
    assert duplicate.json()["reason"] == "visit_already_greeted"

    started = client.post(
        f"/api/conversations/{anonymous_conversation}/turn-start",
        json={
            "language": "en",
            "actor_type": "visitor",
            "text": "Yes, please.",
            "source": "voice",
            "visit_id": visit_a,
        },
    )
    started.raise_for_status()
    assert started.json()["conversation_phase"] == "engaged"

    completed = client.post(
        f"/api/conversations/{anonymous_conversation}/turn-complete",
        json={
            "text": "Great. Which Smart Office feature would you like to try?",
            "route": "general_chat",
            "expect_reply": True,
            "source": "contract",
            "visit_id": visit_a,
        },
    )
    completed.raise_for_status()

    owned_task_id = create_owned_dummy_task(anonymous_conversation, visit_a)

    # Visit B may preempt A before A's delayed archive request arrives.
    greeting_b = client.post(
        f"/api/conversations/{anonymous_conversation}/proximity-greeting",
        json=detection(visit_b),
    )
    greeting_b.raise_for_status()
    payload_b = greeting_b.json()
    assert payload_b["triggered"] is True
    assert payload_b["visit_id"] == visit_b

    delayed_end_a = client.post(
        f"/api/conversations/{anonymous_conversation}/visit-end",
        json={
            "visitor_session_id": visit_a,
            "language": "en",
            "reason": "contract_replaced",
        },
    )
    delayed_end_a.raise_for_status()
    delayed_payload = delayed_end_a.json()
    assert delayed_payload["accepted"] is True
    assert delayed_payload["ended"] is True
    assert delayed_payload["anonymous_history_discarded"] is True
    assert owned_task_id in delayed_payload["cancelled_task_ids"]
    assert state_store.get_task(owned_task_id).status == "cancelled"  # type: ignore[union-attr]

    current = client.get(
        f"/api/conversations/{anonymous_conversation}",
        params={"language": "en", "actor_type": "visitor"},
    )
    current.raise_for_status()
    current_state = current.json()["state"]
    assert current_state["visit_id"] == visit_b
    assert current_state["conversation_phase"] == "awaiting_user"

    stale_completion = client.post(
        f"/api/conversations/{anonymous_conversation}/turn-complete",
        json={
            "text": "This answer belongs to the old visitor.",
            "route": "stale_contract",
            "expect_reply": True,
            "source": "contract",
            "visit_id": visit_a,
        },
    )
    assert stale_completion.status_code == 409

    end_b = client.post(
        f"/api/conversations/{anonymous_conversation}/visit-end",
        json={"visitor_session_id": visit_b, "language": "en"},
    )
    end_b.raise_for_status()
    assert end_b.json()["anonymous_history_discarded"] is True

    registered_conversation = f"conversation-registered-{suffix}"
    registered_identity = f"person-contract-rico-{suffix}"
    registered_visit_1 = f"visit-rico-1-{suffix}"
    registered_visit_2 = f"visit-rico-2-{suffix}"

    first_registered = client.post(
        f"/api/conversations/{registered_conversation}/proximity-greeting",
        json=detection(
            registered_visit_1,
            greeting_kind="registered_identity",
            identity_id=registered_identity,
            display_name="Rico",
        ),
    )
    first_registered.raise_for_status()
    assert first_registered.json()["greeting"] == "Welcome back, Rico."
    assert "I am Sara" not in first_registered.json()["greeting"]

    client.post(
        f"/api/conversations/{registered_conversation}/turn-start",
        json={
            "language": "en",
            "actor_type": "visitor",
            "text": "Please remember that I prefer PowerPoint demonstrations.",
            "source": "voice",
            "visit_id": registered_visit_1,
        },
    ).raise_for_status()
    client.post(
        f"/api/conversations/{registered_conversation}/turn-complete",
        json={
            "text": "Certainly. We can continue with PowerPoint next time.",
            "route": "general_chat",
            "expect_reply": True,
            "source": "contract",
            "visit_id": registered_visit_1,
        },
    ).raise_for_status()

    registered_end = client.post(
        f"/api/conversations/{registered_conversation}/visit-end",
        json={
            "visitor_session_id": registered_visit_1,
            "identity_id": registered_identity,
            "display_name": "Rico",
            "language": "en",
            "reason": "contract_primary_absent",
        },
    )
    registered_end.raise_for_status()
    registered_end_payload = registered_end.json()
    assert registered_end_payload["accepted"] is True
    assert registered_end_payload["memory_saved"] is False
    assert registered_end_payload["memory_queued"] is True
    assert registered_end_payload["anonymous_history_discarded"] is False

    saved_memory = wait_for_memory(registered_identity)
    assert "PowerPoint" in saved_memory["memory_summary"]

    second_registered = client.post(
        f"/api/conversations/{registered_conversation}/proximity-greeting",
        json=detection(
            registered_visit_2,
            greeting_kind="registered_identity",
            identity_id=registered_identity,
            display_name="Rico",
        ),
    )
    second_registered.raise_for_status()
    second_payload = second_registered.json()
    assert second_payload["triggered"] is True
    assert second_payload["visit_id"] == registered_visit_2
    assert second_payload["greeting"] == "Welcome back, Rico."
    assert second_payload["registered_memory_loaded"] is True

    controller = read("ui/smart-office-ui/src/voice/useOfficeVoiceController.ts")
    main_tsx = read("ui/smart-office-ui/src/main.tsx")
    lease_registry = read("ui/smart-office-ui/src/vision/visitLeaseRegistry.ts")
    orchestrator = read("ui/smart-office-ui/src/vision/visitOrchestrator.ts")
    proximity_hook = read("ui/smart-office-ui/src/vision/useProximityGreeting.ts")
    proactive_loop = read("ui/smart-office-ui/src/vision/proactiveReceptionVoiceLoop.ts")
    realtime_runtime = read("ui/smart-office-ui/src/voice/realtimeAgentRuntime.ts")
    voice_output = read("ui/smart-office-ui/src/voice/voiceOutputManager.ts")
    remote_client = read("ui/smart-office-ui/src/vision/remoteVisionClient.ts")
    conversation_store = read("backend/app/conversation_store.py")
    reception_api = read("backend/app/reception_api.py")
    state_store_source = read("backend/app/state_store.py")
    worker_process = read("backend/app/office_worker_process.py")
    office_actions = read("backend/app/office_actions.py")

    for needle in (
        "visitLeaseRegistry",
        "epoch",
        "AbortController",
        "smartoffice:visit-activated",
        "smartoffice:visit-revoked",
    ):
        assert needle in lease_registry, f"Missing Visit lease contract: {needle}"

    for needle in (
        "finishCurrent",
        "onPreempt",
        "onArchive",
        "onFarewell",
        "nextVisitCanStartImmediately",
    ):
        assert needle in orchestrator, f"Missing preemptive orchestrator contract: {needle}"

    assert "visitClosingPromiseRef" not in proximity_hook
    assert "queuedGreetingRef" not in proximity_hook
    assert "proactiveLoopPromiseRef" not in proximity_hook
    assert "PRIMARY_ABSENCE_GRACE_MS = 2_000" in proximity_hook
    assert "VISIT_ARCHIVE_TIMEOUT_MS = 3_000" in proximity_hook
    assert "proactive-reception-farewell" in proximity_hook

    # The current proactive loop delegates microphone ownership to the persistent
    # Realtime agent. It never acquires a second browser stream.
    for needle in (
        "realtimeAgent.startContinuousCapture",
        "realtimeAgent.nextContinuousUtterance",
        "current.submit(transcript, 'voice')",
        "recoverTurnState",
    ):
        assert needle in proactive_loop, f"Missing single-microphone contract: {needle}"
    assert "navigator.mediaDevices.getUserMedia" not in proactive_loop

    for needle in (
        "private generation = 0",
        "assertGeneration",
        "RealtimeSpeechError",
        "output_audio_buffer.stopped",
        "Microphone acquisition timed out",
        "currentMicrophoneStream",
    ):
        assert needle in realtime_runtime, f"Missing cancellable Realtime contract: {needle}"
    assert "operationQueue" not in realtime_runtime

    assert "audioStarted || options.allowLocalFallback === false" in voice_output
    assert "stopInternal(true)" in voice_output
    assert "safeRealtimeAgentRuntime" not in main_tsx
    assert "visitFarewellSessionPatch" not in main_tsx
    assert "realtimeMetadataCompatibility" not in main_tsx

    for needle in (
        "serverInstanceId",
        "snapshot_revision",
        "REMOTE_MESSAGE_STALE_MS",
        "vision_stale",
        "publishStale",
    ):
        assert needle in remote_client, f"Missing reliable RTX client contract: {needle}"

    for needle in (
        "expected_visit_id",
        "_replaced_archives",
        "stale_visit",
        "bind_task_owner",
    ):
        assert needle in conversation_store, f"Missing Backend Visit fence: {needle}"

    for needle in (
        "memory_queued",
        "cancel_tasks_for_visit",
        "visitor_memory_queue",
    ):
        assert needle in reception_api, f"Missing nonblocking Visit archive contract: {needle}"

    for needle in (
        "owner_visit_id",
        "cancel_tasks_for_visit",
        "approval_deadline_at",
    ):
        assert needle in state_store_source, f"Missing task ownership contract: {needle}"

    assert "multiprocessing.get_context(\"spawn\")" in worker_process
    assert "process.terminate()" in worker_process
    assert "daemon=False" in worker_process
    assert "execute_office_tool_call_direct" in office_actions
    assert "execute_office_tool_isolated" in office_actions

    for needle in (
        "visitLeaseRegistry",
        "assertLeaseCurrent",
        "owner_visit_id",
        "visit_id: lease?.visitId",
    ):
        assert needle in controller, f"Missing controller fencing contract: {needle}"

    print(
        "PASS: new Visits preempt old Visits; stale results are fenced; Visit-end is nonblocking; "
        "registered memory is queued; owned tasks are cancelled; Realtime owns one microphone stream."
    )
    print(
        "NOTE: Real LAN ordering, browser audio permission, Windows COM worker termination, camera "
        "freshness, and GPT Realtime event timing still require two-machine acceptance testing."
    )


if __name__ == "__main__":
    main()
