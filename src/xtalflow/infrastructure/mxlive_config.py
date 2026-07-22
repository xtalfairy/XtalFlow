from __future__ import annotations

from dataclasses import dataclass
import getpass
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ImportError:  # pragma: no cover - exercised on Python 3.9/3.10
    import tomli as tomllib


class MxLiveConfigurationError(ValueError):
    """The external MxLive account configuration is invalid."""


@dataclass(frozen=True)
class MxLiveAccount:
    os_username: str
    username: str
    account_id: str
    key_path: Path
    base_url: str | None
    beamline: str
    ca_bundle: Path | None
    explicitly_mapped: bool

    @property
    def upload_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if self.os_username == "root" and not self.explicitly_mapped:
            blockers.append("root requires an explicit account mapping")
        if not self.base_url:
            blockers.append("MxLive URL is not configured")
        if not self.key_path.is_file():
            blockers.append(f"key file is not available: {self.key_path}")
        if self.ca_bundle is not None and not self.ca_bundle.is_file():
            blockers.append(f"CA bundle is not available: {self.ca_bundle}")
        return tuple(blockers)

    @property
    def upload_ready(self) -> bool:
        return not self.upload_blockers


def resolve_mxlive_account(
    config_path: Path | None,
    *,
    os_username: str | None = None,
    base_url: str | None = None,
    beamline: str = "BL-5C",
    key_path: Path | None = None,
    ca_bundle: Path | None = None,
) -> MxLiveAccount:
    """Resolve an OS user to MxLive identity, with TOML overrides."""
    local_user = os_username or getpass.getuser()
    document: Mapping[str, Any] = {}
    if config_path is not None and config_path.is_file():
        try:
            with config_path.open("rb") as handle:
                document = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise MxLiveConfigurationError(
                f"cannot read MxLive configuration {config_path}: {error}"
            ) from error

    mxlive = _mapping(document.get("mxlive"), "mxlive")
    accounts = _mapping(mxlive.get("accounts"), "mxlive.accounts")
    account_value = accounts.get(local_user)
    explicitly_mapped = account_value is not None
    account = _mapping(account_value, f"mxlive.accounts.{local_user}")

    resolved_username = _text(account.get("username"), local_user)
    resolved_account_id = _text(account.get("account_id"), resolved_username)
    resolved_key = Path(_text(
        account.get("key_path"),
        str(key_path or Path("/data/users") / local_user / ".config/mxdc/keys.dsa"),
    )).expanduser()
    resolved_url = _optional_text(mxlive.get("base_url")) or base_url
    resolved_beamline = _text(mxlive.get("beamline"), beamline)
    configured_ca = _optional_text(mxlive.get("ca_bundle"))
    resolved_ca = Path(configured_ca).expanduser() if configured_ca else ca_bundle
    return MxLiveAccount(
        local_user, resolved_username, resolved_account_id, resolved_key,
        resolved_url, resolved_beamline, resolved_ca, explicitly_mapped,
    )


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MxLiveConfigurationError(f"{name} must be a TOML table")
    return value


def _text(value: object, default: str) -> str:
    result = default if value is None else str(value).strip()
    if not result:
        raise MxLiveConfigurationError("MxLive identity values must not be empty")
    return result


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
