from __future__ import annotations

import base64
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import msgpack
import requests
from cryptography.hazmat.primitives import hashes, serialization

from xtalflow.infrastructure import operations_log
from xtalflow.domain.mxlive import (
    MxLiveLabwork,
    MxLiveReadError,
    MxLiveSample,
    MxLivePartialWriteError,
    MxLiveUncertainWriteError,
    MxLiveWriteError,
)


class JsonHttpTransport(Protocol):
    def get_json(
        self, url: str, *, timeout_seconds: float, ca_bundle: Path | None
    ) -> object: ...


class MsgpackHttpTransport(Protocol):
    def post_msgpack(
        self, url: str, payload: object, *, timeout_seconds: float,
        ca_bundle: Path | None,
    ) -> object: ...


class RequestsJsonTransport:
    def get_json(
        self, url: str, *, timeout_seconds: float, ca_bundle: Path | None
    ) -> object:
        try:
            response = requests.get(
                url,
                timeout=timeout_seconds,
                verify=str(ca_bundle) if ca_bundle is not None else True,
            )
            response.raise_for_status()
            return response.json()
        except ImportError as error:
            raise MxLiveReadError(
                "Python SSL support is unavailable; use a Python build with the ssl module"
            ) from error
        except requests.RequestException as error:
            if isinstance(error, ValueError):
                # requests raises a JSON error that is both a RequestException
                # and a ValueError, and this handler is ahead of the one for
                # ValueError, so it would otherwise read as a failed request.
                # raise_for_status has already let the status through, so what
                # failed is reading a body that did arrive.
                raise MxLiveReadError("MxLive returned invalid JSON") from None
            status = error.response.status_code if error.response is not None else None
            detail = f"HTTP {status}" if status is not None else type(error).__name__
            # Not from error: the account's signature is in the URL, and the
            # message of a requests error carries that URL whole. Chaining it
            # would keep it out of this message and put it in every traceback
            # instead, which is the one place it must not be - the operations
            # log writes tracebacks with their causes, from the excepthooks and
            # from a worker thread that dies. The status or the error's class is
            # kept above, which is what a reader needs.
            raise MxLiveReadError(f"MxLive request failed ({detail})") from None
        except ValueError:
            raise MxLiveReadError("MxLive returned invalid JSON") from None

    def post_msgpack(
        self, url: str, payload: object, *, timeout_seconds: float,
        ca_bundle: Path | None,
    ) -> object:
        try:
            response = requests.post(
                url,
                data=msgpack.packb(payload, use_bin_type=True),
                timeout=timeout_seconds,
                verify=str(ca_bundle) if ca_bundle is not None else True,
            )
            response.raise_for_status()
            return response.json()
        except ImportError as error:
            raise MxLiveWriteError(
                "Python SSL support is unavailable; use a Python build with the ssl module"
            ) from error
        except requests.RequestException as error:
            if isinstance(error, ValueError):
                # The same overlap as in get_json, and here it decides what
                # may be done next. raise_for_status let the status through,
                # so the request arrived and MxLive answered: the record is
                # very likely stored, and this must not be reported as a
                # write that definitely did not happen.
                raise MxLiveUncertainWriteError(
                    "MxLive returned invalid JSON after accepting the upload"
                ) from None
            status = error.response.status_code if error.response is not None else None
            detail = f"HTTP {status}" if status is not None else type(error).__name__
            # Not from error, for the reason given in get_json: the signed URL
            # travels in a requests error's message. Severing it here also ends
            # the chain that MxLivePartialWriteError would otherwise extend.
            if _write_may_have_been_stored(error, status):
                raise MxLiveUncertainWriteError(
                    f"MxLive upload result is unknown ({detail})"
                ) from None
            raise MxLiveWriteError(f"MxLive upload failed ({detail})") from None
        except ValueError:
            raise MxLiveUncertainWriteError(
                "MxLive returned invalid JSON after accepting the upload"
            ) from None


def _write_may_have_been_stored(
    error: requests.RequestException, status: int | None
) -> bool:
    """Only errors raised before the request reached the server are safe to retry."""
    if status is not None:
        return status >= 500
    if isinstance(
        error,
        (requests.ConnectTimeout, requests.exceptions.SSLError,
         requests.exceptions.ProxyError, requests.exceptions.InvalidURL),
    ):
        return False
    return isinstance(error, (requests.Timeout, requests.ConnectionError))


class DsaUrlSigner:
    """Legacy MxLive v2 URL signer without its Django/MXDC dependency."""

    def __init__(self, private_key: bytes, salt: str = "ca.clsi.cmcf") -> None:
        try:
            self._private_key = serialization.load_der_private_key(
                private_key, password=None
            )
        except (TypeError, ValueError) as error:
            raise MxLiveReadError("MxLive private key is invalid") from error
        self.salt = salt

    def sign(self, username: str, timestamp: int | None = None) -> str:
        if not username.strip() or "/" in username:
            raise ValueError("invalid MxLive username")
        encoded_time = _base62_encode(int(time.time()) if timestamp is None else timestamp)
        timed_value = f"{username}:{encoded_time}"
        payload = f"{self.salt}:{timed_value}".encode()
        signature = self._private_key.sign(payload, hashes.SHA256())
        return f"{timed_value}:{base64.urlsafe_b64encode(signature).decode()}"


def load_legacy_mxlive_private_key(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            data = msgpack.load(stream, raw=True)
    except (OSError, ValueError, msgpack.UnpackException) as error:
        raise MxLiveReadError(f"cannot read MxLive key file: {path}") from error
    if not isinstance(data, Mapping):
        raise MxLiveReadError("MxLive key file must contain a mapping")
    private = data.get(b"private", data.get("private"))
    if not isinstance(private, bytes):
        raise MxLiveReadError("MxLive key file has no private key")
    return private


class LegacyMxLiveReadClient:
    def __init__(
        self,
        base_url: str,
        beamline: str,
        username: str,
        key_path: Path,
        *,
        ca_bundle: Path | None = None,
        timeout_seconds: float = 10.0,
        transport: JsonHttpTransport | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("MxLive base URL must use HTTPS")
        if not beamline.strip() or "/" in beamline:
            raise ValueError("invalid MxLive beamline")
        if timeout_seconds <= 0:
            raise ValueError("MxLive timeout must be positive")
        if ca_bundle is not None and not ca_bundle.is_file():
            raise MxLiveReadError(f"MxLive CA bundle does not exist: {ca_bundle}")
        self.base_url = base_url.rstrip("/")
        self.beamline = beamline
        self.username = username
        self.ca_bundle = ca_bundle
        self.timeout_seconds = timeout_seconds
        self.transport = transport or RequestsJsonTransport()
        self.signer = DsaUrlSigner(load_legacy_mxlive_private_key(key_path))

    def labworks(self, experiment_or_year: str) -> tuple[MxLiveLabwork, ...]:
        value = experiment_or_year.strip()
        if not value or "/" in value:
            raise ValueError("invalid MxLive experiment/year")
        payload = self._get(f"labworks/{quote(self.beamline)}/{quote(value)}/")
        return tuple(MxLiveLabwork.from_mapping(item) for item in payload)

    def experiment_ids(self, year: int) -> tuple[str, ...]:
        if year < 2000 or year > 9999:
            raise ValueError("invalid experiment year")
        seen: set[str] = set()
        result: list[str] = []
        for item in self.labworks(str(year)):
            if item.experiment_id not in seen:
                seen.add(item.experiment_id)
                result.append(item.experiment_id)
        return tuple(result)

    def samples(self) -> tuple[MxLiveSample, ...]:
        payload = self._get(f"samples/{quote(self.beamline)}/")
        return tuple(MxLiveSample.from_mapping(item) for item in payload)

    def _get(self, path: str) -> tuple[Mapping[str, Any], ...]:
        signed_user = quote(self.signer.sign(self.username), safe=":=_-")
        url = f"{self.base_url}/api/v2/{signed_user}/{path}"
        # The path, never the URL: the signature of the account's private
        # key is in the URL, and a log is the one place it must not be.
        started = time.monotonic()
        try:
            payload = self.transport.get_json(
                url, timeout_seconds=self.timeout_seconds, ca_bundle=self.ca_bundle
            )
        except Exception as error:
            operations_log.problem(
                "mxlive-read",
                path=path,
                seconds=f"{time.monotonic() - started:.1f}",
                outcome=type(error).__name__,
                reason=str(error),
            )
            raise
        operations_log.event(
            "mxlive-read",
            path=path,
            seconds=f"{time.monotonic() - started:.1f}",
            records=len(payload) if isinstance(payload, list) else None,
        )
        if not isinstance(payload, list) or not all(
            isinstance(item, Mapping) for item in payload
        ):
            raise MxLiveReadError("MxLive response must be a list of objects")
        return tuple(payload)


class LegacyMxLiveWriteClient:
    """Write only the legacy labworks endpoint using authenticated HTTPS."""

    def __init__(
        self, base_url: str, beamline: str, username: str, key_path: Path, *,
        ca_bundle: Path | None = None, timeout_seconds: float = 10.0,
        transport: MsgpackHttpTransport | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("MxLive base URL must use HTTPS")
        if not beamline.strip() or "/" in beamline:
            raise ValueError("invalid MxLive beamline")
        if timeout_seconds <= 0:
            raise ValueError("MxLive timeout must be positive")
        if ca_bundle is not None and not ca_bundle.is_file():
            raise MxLiveWriteError(f"MxLive CA bundle does not exist: {ca_bundle}")
        self.base_url = base_url.rstrip("/")
        self.beamline = beamline
        self.username = username
        self.ca_bundle = ca_bundle
        self.timeout_seconds = timeout_seconds
        self.transport = transport or RequestsJsonTransport()
        try:
            private_key = load_legacy_mxlive_private_key(key_path)
            self.signer = DsaUrlSigner(private_key)
        except MxLiveReadError as error:
            raise MxLiveWriteError(str(error)) from error

    @property
    def endpoint(self) -> str:
        return labworks_endpoint(self.base_url, self.beamline)

    def upload_labworks(
        self, records: tuple[Mapping[str, Any], ...]
    ) -> Mapping[str, Any]:
        if not records:
            raise ValueError("at least one labwork record is required")
        if not all(isinstance(record, Mapping) for record in records):
            raise ValueError("labwork records must be mappings")
        responses: list[Mapping[str, Any]] = []
        started = time.monotonic()
        operations_log.event(
            "mxlive-upload-begin",
            endpoint=labworks_endpoint(self.base_url, self.beamline),
            records=len(records),
        )
        for record in records:
            signed_user = quote(self.signer.sign(self.username), safe=":=_-")
            url = (
                f"{self.base_url}/api/v2/{signed_user}/upload_labworks/"
                f"{quote(self.beamline)}/"
            )
            try:
                response = self.transport.post_msgpack(
                    url, dict(record), timeout_seconds=self.timeout_seconds,
                    ca_bundle=self.ca_bundle,
                )
                if not isinstance(response, Mapping):
                    raise MxLiveUncertainWriteError(
                        "MxLive upload response must be an object"
                    )
            except MxLiveWriteError as error:
                # How many went in before it stopped: the one number that
                # decides what can be done next, and the one nobody could
                # find afterwards.
                operations_log.problem(
                    "mxlive-upload-stopped",
                    stored=len(responses),
                    of=len(records),
                    seconds=f"{time.monotonic() - started:.1f}",
                    outcome=type(error).__name__,
                    uncertain=isinstance(error, MxLiveUncertainWriteError),
                    reason=str(error),
                )
                if responses:
                    raise MxLivePartialWriteError(
                        len(responses), len(records), str(error),
                        outcome_uncertain=isinstance(error, MxLiveUncertainWriteError),
                    ) from error
                raise
            responses.append(dict(response))
        operations_log.event(
            "mxlive-upload-done",
            records=len(responses),
            seconds=f"{time.monotonic() - started:.1f}",
        )
        return {"uploaded_count": len(responses), "responses": responses}


def labworks_endpoint(base_url: str, beamline: str) -> str:
    """Public labworks endpoint shown to users and stored in upload audits."""
    return f"{base_url.rstrip('/')}/upload_labworks/{beamline}/"


def _base62_encode(value: int) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    if value < 0:
        raise ValueError("base62 value must not be negative")
    if value == 0:
        return alphabet[0]
    encoded = ""
    while value:
        value, remainder = divmod(value, 62)
        encoded = alphabet[remainder] + encoded
    return encoded
