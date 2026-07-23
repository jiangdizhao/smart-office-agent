from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.tools.system_controller import _set_wmi_brightness  # noqa: E402


class FakeProperty:
    def __init__(self, value=None):
        self.Value = value


class FakeProperties:
    def __init__(self):
        self.values = {
            "Timeout": FakeProperty(),
            "Brightness": FakeProperty(),
            "ReturnValue": FakeProperty(0),
        }

    def Item(self, name: str):
        return self.values[name]


class FakeInputParameters:
    def __init__(self):
        self.Properties_ = FakeProperties()


class FakeInputDefinition:
    def SpawnInstance_(self):
        return FakeInputParameters()


class FakeMethodDefinition:
    def __init__(self):
        self.InParameters = FakeInputDefinition()


class FakeMethods:
    def Item(self, name: str):
        assert name == "WmiSetBrightness"
        return FakeMethodDefinition()


class FakeClassDefinition:
    def __init__(self):
        self.Methods_ = FakeMethods()


class FakePath:
    RelPath = 'WmiMonitorBrightnessMethods.InstanceName="DISPLAY\\\\TEST"'


class FakeInstance:
    Active = True
    InstanceName = r"DISPLAY\TEST"
    Path_ = FakePath()

    def __init__(self):
        self.calls = []

    def ExecMethod_(self, method_name: str, input_parameters):
        self.calls.append(
            {
                "method_name": method_name,
                "timeout": input_parameters.Properties_.Item("Timeout").Value,
                "brightness": input_parameters.Properties_.Item("Brightness").Value,
            }
        )
        # Reproduce the target Windows/pywin32 behaviour: the provider accepts
        # the command but win32com returns no output object. The production code
        # must rely on the subsequent CurrentBrightness readback.
        return None


class FakeService:
    def __init__(self, instance: FakeInstance):
        self.instance = instance

    def ExecQuery(self, query: str):
        assert "WmiMonitorBrightnessMethods" in query
        return [self.instance]

    def Get(self, class_name: str):
        assert class_name == "WmiMonitorBrightnessMethods"
        return FakeClassDefinition()


def main() -> None:
    instance = FakeInstance()
    service = FakeService(instance)

    results = _set_wmi_brightness(service, 37)

    assert instance.calls == [
        {
            "method_name": "WmiSetBrightness",
            "timeout": 1,
            "brightness": 37,
        }
    ]
    assert results == [
        {
            "instance_name": r"DISPLAY\TEST",
            "return_value": None,
            "provider_output_present": False,
            "invocation": "SWbemObject.ExecMethod_",
        }
    ]
    assert not hasattr(service, "ExecMethod_")

    print(
        "PASS: WMI brightness accepts a missing provider output object and leaves "
        "final success to observed CurrentBrightness verification."
    )


if __name__ == "__main__":
    main()
