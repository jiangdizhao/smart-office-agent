from __future__ import annotations

import numpy as np

from app.camera_runtime import FramePacket, LatestFrameBuffer
from app.config import PresenceSettings
from app.person_detector import _demo_postprocess, _nms
from app.presence import PresenceStateMachine, point_in_polygon


def test_latest_frame_buffer_returns_only_newer_packet() -> None:
    buffer = LatestFrameBuffer()
    assert buffer.wait_for_new(0, timeout=0.001) is None
    packet = FramePacket(1, 1.0, 1.0, np.zeros((2, 2, 3), dtype=np.uint8))
    buffer.publish(packet)
    assert buffer.latest() is packet
    assert buffer.wait_for_new(0, timeout=0.001) is packet
    assert buffer.wait_for_new(1, timeout=0.001) is None


def test_point_in_polygon() -> None:
    polygon = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert point_in_polygon(0.5, 0.5, polygon)
    assert not point_in_polygon(1.5, 0.5, polygon)


def _person(area: float = 0.2) -> dict:
    return {
        "area_ratio": area,
        "bottom_center": {"x": 0.5, "y": 0.8},
        "bbox": {"x": 0.3, "y": 0.1, "width": 0.4, "height": 0.7},
        "score": 0.9,
    }


def test_presence_enter_engage_and_leave() -> None:
    settings = PresenceSettings(
        enter_confirm_frames=2,
        engage_confirm_frames=2,
        left_timeout_seconds=1.0,
        engagement_min_area_ratio=0.1,
    )
    machine = PresenceStateMachine(settings)
    assert machine.update([_person()], now_monotonic=0.0) == []
    events = machine.update([_person()], now_monotonic=0.1)
    assert [event[0] for event in events] == ["visitor_entered", "visitor_engaged"]
    assert machine.state == "engaged"
    assert machine.update([], now_monotonic=0.5) == []
    events = machine.update([], now_monotonic=1.2)
    assert [event[0] for event in events] == ["visitor_left"]
    assert machine.state == "absent"


def test_nms_keeps_best_overlapping_box() -> None:
    boxes = np.array(
        [[0, 0, 100, 100], [5, 5, 100, 100], [200, 200, 220, 220]],
        dtype=np.float32,
    )
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    assert _nms(boxes, scores, 0.5) == [0, 2]


def test_yolox_postprocess_shape() -> None:
    outputs = np.zeros((1, 3549, 85), dtype=np.float32)
    decoded = _demo_postprocess(outputs, (416, 416))
    assert decoded.shape == outputs.shape
    assert decoded[0, 0, 0] == 0
    assert decoded[0, 1, 0] == 8
