from __future__ import annotations

import sys
import types
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.powerpoint_bootstrap as bootstrap_module  # noqa: E402
import app.powerpoint_rot_connection as rot_module  # noqa: E402
import app.presentation_monitor as monitor_module  # noqa: E402
import app.tools.presentation_controller as presentation_controller_module  # noqa: E402


class FakeApplication:
    def __init__(self) -> None:
        self.Visible = 0


class FakePythonCom(types.ModuleType):
    COINIT_APARTMENTTHREADED = 2

    def __init__(self) -> None:
        super().__init__("pythoncom")
        self.initialized = 0
        self.uninitialized = 0

    def CoInitialize(self) -> None:  # noqa: N802
        self.initialized += 1

    def CoInitializeEx(self, _mode: int) -> None:  # noqa: N802
        self.initialized += 1

    def CoUninitialize(self) -> None:  # noqa: N802
        self.uninitialized += 1


class FakeClient(types.ModuleType):
    def __init__(self, application: object) -> None:
        super().__init__("win32com.client")
        self.application = application
        self.get_active_calls = 0
        self.dispatch_calls = 0

    def GetActiveObject(self, _progid: str) -> object:  # noqa: N802
        self.get_active_calls += 1
        raise RuntimeError("ROT entry unavailable")

    def Dispatch(self, _progid: str) -> object:  # noqa: N802
        self.dispatch_calls += 1
        return self.application


def main() -> None:
    fake_application = FakeApplication()
    fake_pythoncom = FakePythonCom()
    fake_client = FakeClient(fake_application)
    fake_win32com = types.ModuleType("win32com")
    fake_win32com.client = fake_client

    saved_modules = {
        name: sys.modules.get(name)
        for name in ("pythoncom", "win32com", "win32com.client")
    }
    original_controller = presentation_controller_module.presentation_controller
    original_rot_process_probe = rot_module.powerpoint_process_running
    original_bootstrap_process_probe = bootstrap_module._powerpoint_process_running
    original_bootstrap_launcher = bootstrap_module._launch_powerpoint_desktop
    original_bootstrap_os = bootstrap_module.os
    original_monitor_enumerator = monitor_module._enumerate_powerpoint_slideshow_hwnd

    sys.modules["pythoncom"] = fake_pythoncom
    sys.modules["win32com"] = fake_win32com
    sys.modules["win32com.client"] = fake_client

    try:
        controller = rot_module.RotResilientPowerPointController(
            original_controller.config
        )

        rot_module.powerpoint_process_running = lambda: True
        with controller._com_application(create=False) as application:
            assert application is fake_application
        assert fake_client.get_active_calls == 1
        assert fake_client.dispatch_calls == 1

        rot_module.powerpoint_process_running = lambda: False
        with controller._com_application(create=False) as application:
            assert application is None
        assert fake_client.dispatch_calls == 1

        with controller._com_application(create=True) as application:
            assert application is fake_application
        assert fake_client.dispatch_calls == 2

        rot_module.install_powerpoint_rot_fallback()
        installed = presentation_controller_module.presentation_controller
        assert isinstance(installed, rot_module.RotResilientPowerPointController)
        rot_module.install_powerpoint_rot_fallback()
        assert presentation_controller_module.presentation_controller is installed

        # The bootstrap must mirror the known-good local command: Dispatch first.
        # It must not launch a separate blank POWERPNT.EXE when Dispatch works.
        bootstrap_module.os = types.SimpleNamespace(name="nt")
        bootstrap_module._powerpoint_process_running = lambda: False

        def unexpected_desktop_launch():
            raise AssertionError("desktop launcher must not run before Dispatch")

        bootstrap_module._launch_powerpoint_desktop = unexpected_desktop_launch
        dispatch_before = fake_client.dispatch_calls
        cold_result = bootstrap_module.ensure_powerpoint_desktop_running()
        assert cold_result.ok is True
        assert cold_result.launched is True
        assert cold_result.launch_method == "com_dispatch"
        assert fake_client.dispatch_calls == dispatch_before + 1
        assert fake_application.Visible == -1

        bootstrap_module._powerpoint_process_running = lambda: True
        dispatch_before = fake_client.dispatch_calls
        warm_result = bootstrap_module.ensure_powerpoint_desktop_running()
        assert warm_result.ok is True
        assert warm_result.already_running is True
        assert warm_result.launched is False
        assert warm_result.launch_method == "com_dispatch_existing"
        assert fake_client.dispatch_calls == dispatch_before + 1

        # Monitor placement must be able to discover the slide-show HWND directly
        # from Windows markers before consulting COM/ROT.
        monitor_module._enumerate_powerpoint_slideshow_hwnd = lambda **_kwargs: 12345
        assert monitor_module._active_slideshow_hwnd() == 12345

        assert fake_pythoncom.initialized == fake_pythoncom.uninitialized
    finally:
        presentation_controller_module.presentation_controller = original_controller
        rot_module.powerpoint_process_running = original_rot_process_probe
        bootstrap_module._powerpoint_process_running = original_bootstrap_process_probe
        bootstrap_module._launch_powerpoint_desktop = original_bootstrap_launcher
        bootstrap_module.os = original_bootstrap_os
        monitor_module._enumerate_powerpoint_slideshow_hwnd = original_monitor_enumerator
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    print(
        "PASS: PowerPoint uses fast Dispatch without duplicate desktop launch, "
        "status reconnects when ROT is absent, and slideshow HWND discovery is ROT-independent."
    )


if __name__ == "__main__":
    main()
