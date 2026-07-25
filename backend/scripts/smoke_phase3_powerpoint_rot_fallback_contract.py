from __future__ import annotations

import sys
import types
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.tools.presentation_controller as presentation_controller_module  # noqa: E402
from app.powerpoint_rot_connection import (  # noqa: E402
    RotResilientPowerPointController,
    install_powerpoint_rot_fallback,
)


class FakePythonCom(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("pythoncom")
        self.initialized = 0
        self.uninitialized = 0

    def CoInitialize(self) -> None:  # noqa: N802
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
    fake_application = object()
    fake_pythoncom = FakePythonCom()
    fake_client = FakeClient(fake_application)
    fake_win32com = types.ModuleType("win32com")
    fake_win32com.client = fake_client

    saved_modules = {
        name: sys.modules.get(name)
        for name in ("pythoncom", "win32com", "win32com.client")
    }
    original_controller = presentation_controller_module.presentation_controller

    sys.modules["pythoncom"] = fake_pythoncom
    sys.modules["win32com"] = fake_win32com
    sys.modules["win32com.client"] = fake_client

    try:
        controller = RotResilientPowerPointController(original_controller.config)
        controller._powerpoint_process_running = lambda: True

        with controller._com_application(create=False) as application:
            assert application is fake_application
        assert fake_client.get_active_calls == 1
        assert fake_client.dispatch_calls == 1

        controller._powerpoint_process_running = lambda: False
        with controller._com_application(create=False) as application:
            assert application is None
        assert fake_client.dispatch_calls == 1

        with controller._com_application(create=True) as application:
            assert application is fake_application
        assert fake_client.dispatch_calls == 2
        assert fake_pythoncom.initialized == 3
        assert fake_pythoncom.uninitialized == 3

        install_powerpoint_rot_fallback()
        installed = presentation_controller_module.presentation_controller
        assert isinstance(installed, RotResilientPowerPointController)
        install_powerpoint_rot_fallback()
        assert presentation_controller_module.presentation_controller is installed
    finally:
        presentation_controller_module.presentation_controller = original_controller
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    print(
        "PASS: PowerPoint status/control reconnects through Dispatch only when "
        "POWERPNT.EXE is already running, while status queries remain non-launching."
    )


if __name__ == "__main__":
    main()
