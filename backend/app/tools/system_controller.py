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
    """Initialise COM in the worker thread that accesses Windows Core Audio."""
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
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    speakers = AudioUtilities.GetSpeakers()
    if speakers is None:
        raise RuntimeError("Windows did not return a default audio output device.")

    endpoint = getattr(speakers, "EndpointVolume", None)
    if endpoint is not None:
        return endpoint

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
            IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None
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
                    "SELECT Active, CurrentBrightness, InstanceName "
                    "FROM WmiMonitorBrightness"
                )
            )
            active_rows = [row for row in rows if bool(getattr(row, "Active", True))]
            selected = active_rows or rows
            if not selected:
                return {
                    "available": False,
                    "brightness_percent": None,
                    "instances": [],
                    "error": (
                        "No WMI brightness-capable display was found. External monitors may "
                        "not expose Windows WMI brightness control."
                    ),
                }
            values = [_clamp_percent(row.CurrentBrightness) for row in selected]
            return {
                "available": True,
                "brightness_percent": int(round(sum(values) / len(values))),
                "instances": [str(row.InstanceName) for row in selected],
                "error": None,
            }
        finally:
            pythoncom.CoUninitialize()
    except Exception as exc:
        return {
            "available": False,
            "brightness_percent": None,
            "instances": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _set_wmi_brightness(service: Any, target: int) -> list[dict[str, Any]]:
    """Invoke WmiSetBrightness and leave final success to observed-state readback.

    Some pywin32/SWbem provider combinations return ``None`` even when the
    provider accepts the call. The target machine demonstrates this behaviour:
    PowerShell prints no return object but CurrentBrightness changes. Therefore
    a missing output object is diagnostic information, not an execution failure.
    A non-zero ReturnValue is still rejected when the provider supplies one.
    """
    instances = list(
        service.ExecQuery(
            "SELECT Active, InstanceName FROM WmiMonitorBrightnessMethods"
        )
    )
    active_instances = [
        instance for instance in instances if bool(getattr(instance, "Active", True))
    ]
    selected = active_instances or instances
    if not selected:
        raise RuntimeError(
            "No WMI brightness-capable display was found. The selected external "
            "monitor may not support Windows brightness control."
        )

    class_definition = service.Get("WmiMonitorBrightnessMethods")
    method_definition = class_definition.Methods_.Item("WmiSetBrightness")
    results: list[dict[str, Any]] = []

    for instance in selected:
        input_parameters = method_definition.InParameters.SpawnInstance_()
        input_parameters.Properties_.Item("Timeout").Value = 1
        input_parameters.Properties_.Item("Brightness").Value = target

        exec_on_instance = getattr(instance, "ExecMethod_", None)
        if callable(exec_on_instance):
            output_parameters = exec_on_instance(
                "WmiSetBrightness",
                input_parameters,
            )
            invocation = "SWbemObject.ExecMethod_"
        else:
            exec_on_service = getattr(service, "ExecMethod", None)
            if not callable(exec_on_service):
                raise RuntimeError(
                    "Neither SWbemObject.ExecMethod_ nor SWbemServices.ExecMethod is available."
                )
            output_parameters = exec_on_service(
                instance.Path_.RelPath,
                "WmiSetBrightness",
                input_parameters,
            )
            invocation = "SWbemServices.ExecMethod"

        return_value: int | None = None
        properties = (
            getattr(output_parameters, "Properties_", None)
            if output_parameters is not None
            else None
        )
        if properties is not None:
            return_property = properties.Item("ReturnValue")
            raw_return_value = getattr(return_property, "Value", None)
            if raw_return_value is not None:
                return_value = int(raw_return_value)

        results.append(
            {
                "instance_name": str(instance.InstanceName),
                "return_value": return_value,
                "provider_output_present": output_parameters is not None,
                "invocation": invocation,
            }
        )
        if return_value not in (None, 0):
            raise RuntimeError(
                f"WmiSetBrightness returned {return_value} for {instance.InstanceName}."
            )

    return results


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
            method_results = _set_wmi_brightness(service, target)
        finally:
            pythoncom.CoUninitialize()

        time.sleep(0.35)
        observed = _read_brightness()
        observed_percent = observed.get("brightness_percent")
        verified = bool(
            observed.get("available")
            and observed_percent is not None
            and abs(int(observed_percent) - target) <= 1
        )
        return ToolResult(
            tool_name="system_set_brightness",
            ok=verified,
            message=(
                f"Display brightness set to {target}%."
                if verified
                else (
                    f"WMI brightness command completed, but the observed value was "
                    f"{observed_percent} instead of {target}%."
                )
            ),
            data={
                "execution_mode": "real" if verified else "failed_verification",
                "requested_state": {"brightness_percent": target},
                "brightness": observed,
                "brightness_percent": observed_percent,
                "wmi_method_results": method_results,
            },
        )
    except Exception as exc:
        return ToolResult(
            tool_name="system_set_brightness",
            ok=False,
            message=(
                "Display brightness could not be changed: "
                f"{type(exc).__name__}: {exc}"
            ),
            data={
                "execution_mode": "failed",
                "requested_state": {"brightness_percent": target},
                "error": f"{type(exc).__name__}: {exc}",
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
