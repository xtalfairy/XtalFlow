from __future__ import annotations

from dataclasses import dataclass, replace
import getpass
import time
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ImportError:  # pragma: no cover - exercised on Python 3.9/3.10
    import tomli as tomllib


class MxLiveConfigurationError(ValueError):
    """The external MxLive account configuration is invalid."""


# How long a "you cannot upload" answer is kept before the filesystem is
# asked again. Long enough that typing never reaches the network storage
# twice, short enough that somebody mounting /data/users or copying their
# key in does not have to restart to be believed.
RECHECK_BLOCKERS_SECONDS = 30.0


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
        """What stands between this account and an upload.

        Asked on every refresh of the preview - twice, through
        ``upload_ready`` as well - which is twice per keystroke in the
        protein, rows and volume fields. Two of the answers come from the
        filesystem, and the key path is ``/data/users/<user>/...`` at BL-5C,
        which is network storage: on a dropped mount each look takes the
        best part of a minute, on the thread the window is drawn on.

        So the answer is kept - but a refusal is only kept for
        ``RECHECK_BLOCKERS_SECONDS``. Kept forever it was worse than the
        freeze it fixed: the upload button is disabled from this answer, and
        the button is the only thing that calls ``rechecked``, so an account
        that started without its key file could never get its key file
        believed. Somebody whose /data/users was not mounted when the
        application started was locked out of MxLive for the session.

        A clear answer is kept as long as the account lives: a key file
        nobody has taken away is still there, and the one that matters - it
        going away under an upload - is caught by ``rechecked`` at the
        upload itself.
        """
        kept = self.__dict__.get("_upload_blockers")
        if kept is not None:
            blockers, asked_at = kept
            if not blockers or time.monotonic() - asked_at < RECHECK_BLOCKERS_SECONDS:
                return blockers
        blockers = self._blockers_now()
        # Frozen blocks __setattr__, not the instance dictionary, which is
        # where cached_property kept this too.
        self.__dict__["_upload_blockers"] = (blockers, time.monotonic())
        return blockers

    def _blockers_now(self) -> tuple[str, ...]:
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

    def rechecked(self) -> MxLiveAccount:
        """The same account, asking the filesystem again, now.

        For the upload itself, where neither a kept refusal nor a kept
        clearance will do: the key file may have arrived since, and it may
        equally have gone away with the share it sits on.
        """
        return replace(self)


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
