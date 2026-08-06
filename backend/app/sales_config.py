from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from app.sales_models import (
    CapabilityCatalog,
    CapabilityEntry,
    SalesClaimsConfig,
    SalesPersonaConfig,
    SalesPlaybooksConfig,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SalesConfigBundle:
    persona: SalesPersonaConfig
    capabilities: CapabilityCatalog
    playbooks: SalesPlaybooksConfig
    claims: SalesClaimsConfig


class SalesConfigService:
    """Lazy, hot-reloadable source of approved Phase 1 sales configuration.

    Invalid configuration is reported through status APIs and blocks only the sales
    runtime. Existing deterministic Office control remains available.
    """

    def __init__(self, config_root: Path | None = None) -> None:
        self.config_root = config_root or (_REPO_ROOT / "config")
        self._lock = RLock()
        self._bundle: SalesConfigBundle | None = None
        self._errors: dict[str, str] = {}
        self._fingerprints: dict[str, tuple[int, int]] = {}

    @property
    def paths(self) -> dict[str, Path]:
        return {
            "persona": self.config_root / "sales_persona.json",
            "capabilities": self.config_root / "capability_catalog.json",
            "playbooks": self.config_root / "sales_playbooks.json",
            "claims": self.config_root / "sales_claims.json",
        }

    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Sales configuration must contain a JSON object: {path}")
        return payload

    def _current_fingerprints(self) -> dict[str, tuple[int, int]]:
        values: dict[str, tuple[int, int]] = {}
        for name, path in self.paths.items():
            if path.exists():
                values[name] = self._fingerprint(path)
        return values

    def load(self, *, force: bool = False) -> SalesConfigBundle | None:
        with self._lock:
            fingerprints = self._current_fingerprints()
            if (
                not force
                and self._bundle is not None
                and fingerprints == self._fingerprints
            ):
                return self._bundle

            self._errors = {}
            parsed: dict[str, Any] = {}
            model_types = {
                "persona": SalesPersonaConfig,
                "capabilities": CapabilityCatalog,
                "playbooks": SalesPlaybooksConfig,
                "claims": SalesClaimsConfig,
            }
            for name, path in self.paths.items():
                try:
                    if not path.exists():
                        raise FileNotFoundError(f"Configuration file does not exist: {path}")
                    parsed[name] = model_types[name].model_validate(self._read_json(path))
                except Exception as exc:
                    self._errors[name] = f"{type(exc).__name__}: {exc}"

            if self._errors:
                self._bundle = None
                self._fingerprints = fingerprints
                return None

            self._bundle = SalesConfigBundle(
                persona=parsed["persona"],
                capabilities=parsed["capabilities"],
                playbooks=parsed["playbooks"],
                claims=parsed["claims"],
            )
            self._fingerprints = fingerprints
            return self._bundle

    def require_valid(self) -> SalesConfigBundle:
        bundle = self.load()
        if bundle is None:
            details = "; ".join(
                f"{name}: {message}" for name, message in sorted(self._errors.items())
            )
            raise RuntimeError(f"Sales configuration is invalid. {details}")
        return bundle

    def reload(self) -> SalesConfigBundle | None:
        return self.load(force=True)

    def capability(self, capability_id: str) -> CapabilityEntry | None:
        clean_id = str(capability_id or "").strip()
        if not clean_id:
            return None
        bundle = self.load()
        if bundle is None:
            return None
        return next(
            (
                capability
                for capability in bundle.capabilities.capabilities
                if capability.capability_id == clean_id
            ),
            None,
        )

    def status(self) -> dict[str, Any]:
        bundle = self.load()
        paths = self.paths
        configured = {name: path.exists() for name, path in paths.items()}
        result: dict[str, Any] = {
            "ok": bundle is not None,
            "phase": "phase1_sales_runtime",
            "configured": configured,
            "paths": {name: str(path) for name, path in paths.items()},
            "errors": dict(self._errors),
        }
        if bundle is not None:
            status_counts: dict[str, int] = {}
            for item in bundle.capabilities.capabilities:
                status_counts[item.status] = status_counts.get(item.status, 0) + 1
            result.update(
                {
                    "persona_version": bundle.persona.persona_version,
                    "catalog_version": bundle.capabilities.catalog_version,
                    "playbook_version": bundle.playbooks.playbook_version,
                    "claims_version": bundle.claims.claims_version,
                    "capability_count": len(bundle.capabilities.capabilities),
                    "capability_status_counts": status_counts,
                    "humour_theme_count": len(bundle.claims.humour_themes),
                    "appointment_first_default": (
                        bundle.capabilities.default_status == "appointment_demo"
                    ),
                    "quality_baseline": bundle.persona.model_policy.get(
                        "initial_quality_baseline",
                        "gpt-realtime-2.1",
                    ),
                }
            )
        return result


sales_config = SalesConfigService()
