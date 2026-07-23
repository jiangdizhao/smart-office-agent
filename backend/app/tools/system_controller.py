from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from ctypes import POINTER, cast
from typing import Any, Iterator

from app.models import ToolResult


def _clamp_percent(value: int | float) -> int:
    return max(0, min(100, int(round(float(value)))))


@contextmanager
def _audio_com_apartment() -> Iterator[None]:
    """Initialise COM in the thread that accesses Windows Core Audio.

    Office actions are executed through ``asyncio.to_thread``. Those worker
    threads do not inherit COM initialisation from the FastAPI main thread, so
    PyCAW's ``CoCreateInstance`` calls fail unless the worker initialises its
    own COM apartment.
    """

    try:
        import pythoncom
    except ImportError:
        import comtypes

        comtypes.CoInitialize()
        try:
            yield
        finally:
            comtypes.CoUninitialize()
        return

    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def _volume_endpoint():
    """Return the default render endpoint's IAudioEndpointVolume interface.

    PyCAW 20251023 returns an ``AudioDevice`` wrapper whose supported public
    access path is ``EndpointVolume``. A legacy activation fallback is retained
    for older PyCAW releases.
    """

    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    speakers = AudioUtilities.GetSpeakers()
    if speakers is None:
        raise RuntimeError("Windows did not return a default audio output device.")

    endpoint = getattr(speakers, "EndpointVolume", None)
    if endpoint is not None:
        return endpoint

    # Compatibility with older PyCAW versions that returned the raw IMMDevice.
    activate = getattr(speakers, "Activate", None)
    if callable(activate):
        from comtypes import CLSCTX_ALL

        interface = activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        query_interface = getattr(interface, "QueryInterface", None)
        if callable(query_interface):
            return query_interface(IAudioEndpointVolume)
        return cast(interface, POINTER(IAudioEndpointVolume))

    raw_device = getattr(speakers, "_dev", None)
    if raw_device is not None:
        import comtypes

        interface = raw_device.Activate(
            IAudioEndpointVolume._iid_,
            comtypes.CLSCTX_ALL,
            None,
        )
        return interface.QueryInterface(IAudioEndpointVolume)

    raise RuntimeError(
        "PyCAW returned an audio device without an EndpointVolume interface."
    )


def _audio_error(exc: Exception) -> str:
    return (
        "Windows Core Audio initialization failed: "
        f"{type(exc).__name__}: {exc}. Backend Python: {sys.executable}"
    )


def _read_volume() -> dict[str, Any]:
    try:
        with _audio_com_apartment():
            endpoint = _volume_endpoint()
            volume_percent = _clamp_percent(
                endpoint.GetMasterVolumeLevelScalar() * 100
            )
            muted = bool(endpoint.GetMute())
        return {
            "available": True,
            "volume_percent": volume_percent,
            "muted": muted,
            "error": None,
            "backend_python": sys.executable,
        }
    except Exception as exc:
        return {
            "available": False,
            "volume_percent": None,
            "muted": None,
            "error": _audio_error(exc),
            "backend_python": sys.executable,
        }


def _brightness_service():
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:  # pragma: no cover - Windows-only dependency
        raise RuntimeError("pywin32 is required for Windows brightness control.") from exc

    pythoncom.CoInitialize()
    try:
        service = win32com.client.GetObject(r"winmgmts:\\.\root\WMI")
        return pythoncom, service
    except Exception:
        pythoncom.CoUninitialize()
        raise


def _read_brightness() -> dict[str, Any]:
    try:
        pythoncom, service = _brightness_service()
        try:
            rows = list(
                service.ExecQuery(
                    "SELECT CurrentBrightness, InstanceName FROM WmiMonitorBrightness"
                )
            )
            if not rows:
                return {
                    "available": False,
                    "brightness_percent": None,
                    "instances": [],
                    "error": (
                        "No WMI brightness-capable display was found. External monitors may "
                        "not expose Windows WMI brightness control."
                    ),
                }
            values = [_clamp_percent(row.CurrentBrightness) for row in rows]
            return {
                "available": True,
                "brightness_percent": int(round(sum(values) / len(values))),
                "instances": [str(row.InstanceName) for row in rows],
                "error": None,
            }
        finally:
            pythoncom.CoUninitialize()
    except Exception as exc:
        return {
            "available": False,
            "brightness_percent": None,
            "instances": [],
            "error": str(exc),
        }


def get_system_control_status() -> ToolResult:
    volume = _read_volume()
    brightness = _read_brightness()
    return ToolResult(
        tool_name="system_get_status",
        ok=bool(volume["available"] or brightness["available"]),
        message="Windows volume and brightness status inspected.",
        data={
            "execution_mode": "real",
            "volume": volume,
            "brightness": brightness,
            "volume_percent": volume.get("volume_percent"),
            "brightness_percent": brightness.get("brightness_percent"),
            "requested_state": {},
        },
    )


def set_system_volume(value_percent: int) -> ToolResult:
    target = _clamp_percent(value_percent)
    try:
        with _audio_com_apartment():
            endpoint = _volume_endpoint()
            endpoint.SetMasterVolumeLevelScalar(target / 100.0, None)
            if target > 0 and bool(endpoint.GetMute()):
                endpoint.SetMute(0, None)
            time.sleep(0.1)
            observed_percent = _clamp_percent(
                endpoint.GetMasterVolumeLevelScalar() * 100
            )
            observed_muted = bool(endpoint.GetMute())

        observed = {
            "available": True,
            "volume_percent": observed_percent,
            "muted": observed_muted,
            "error": None,
            "backend_python": sys.executable,
        }
        return ToolResult(
            tool_name="system_set_volume",
            ok=True,
            message=f"System volume set to {target}%.",
            data={
                "execution_mode": "real",
                "requested_state": {"volume_percent": target},
                "volume": observed,
                "volume_percent": observed_percent,
            },
        )
    except Exception as exc:
        return ToolResult(
            tool_name="system_set_volume",
            ok=False,
            message=f"System volume could not be changed: {_audio_error(exc)}",
            data={
                "execution_mode": "failed",
                "requested_state": {"volume_percent": target},
                "error": _audio_error(exc),
                "backend_python": sys.executable,
            },
        )


def adjust_system_volume(delta_percent: int) -> ToolResult:
    current = _read_volume()
    if not current["available"] or current["volume_percent"] is None:
        return ToolResult(
            tool_name="system_adjust_volume",
            ok=False,
            message=f"System volume could not be read: {current.get('error')}",
            data={
                "execution_mode": "failed",
                "requested_state": {"volume_delta_percent": int(delta_percent)},
                "volume": current,
            },
        )
    target = _clamp_percent(int(current["volume_percent"]) + int(delta_percent))
    result = set_system_volume(target)
    return result.model_copy(
        update={
            "tool_name": "system_adjust_volume",
            "message": (
                f"System volume adjusted by {int(delta_percent):+d} points to {target}%."
                if result.ok
                else result.message
            ),
            "data": {
                **result.data,
                "requested_state": {
                    "volume_percent": target,
                    "volume_delta_percent": int(delta_percent),
                },
                "previous_volume_percent": current["volume_percent"],
            },
        }
    )


def set_system_brightness(value_percent: int) -> ToolResult:
    target = _clamp_percent(value_percent)
    try:
        pythoncom, service = _brightness_service()
        try:
            methods = list(service.ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods"))
            if not methods:
                raise RuntimeError(
                    "No WMI brightness-capable display was found. The selected external "
                    "monitor may not support Windows brightness control."
                )
            for method in methods:
                method.WmiSetBrightness(1, target)
        finally:
            pythoncom.CoUninitialize()
        time.sleep(0.25)
        observed = _read_brightness()
        return ToolResult(
            tool_name="system_set_brightness",
            ok=bool(observed["available"]),
            message=f"Display brightness set to {target}%.",
            data={
                "execution_mode": "real",
                "requested_state": {"brightness_percent": target},
                "brightness": observed,
                "brightness_percent": observed.get("brightness_percent"),
            },
        )
    except Exception as exc:
        return ToolResult(
            tool_name="system_set_brightness",
            ok=False,
            message=f"Display brightness could not be changed: {exc}",
            data={
                "execution_mode": "failed",
                "requested_state": {"brightness_percent": target},
                "error": str(exc),
            },
        )


def adjust_system_brightness(delta_percent: int) -> ToolResult:
    current = _read_brightness()
    if not current["available"] or current["brightness_percent"] is None:
        return ToolResult(
            tool_name="system_adjust_brightness",
            ok=False,
            message=f"Display brightness could not be read: {current.get('error')}",
            data={
                "execution_mode": "failed",
                "requested_state": {"brightness_delta_percent": int(delta_percent)},
                "brightness": current,
            },
        )
    target = _clamp_percent(int(current["brightness_percent"]) + int(delta_percent))
    result = set_system_brightness(target)
    return result.model_copy(
        update={
            "tool_name": "system_adjust_brightness",
            "message": (
                f"Display brightness adjusted by {int(delta_percent):+d} points to {target}%."
                if result.ok
                else result.message
            ),
            "data": {
                **result.data,
                "requested_state": {
                    "brightness_percent": target,
                    "brightness_delta_percent": int(delta_percent),
                },
                "previous_brightness_percent": current["brightness_percent"],
            },
        }
    )
