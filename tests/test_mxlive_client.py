from __future__ import annotations

from pathlib import Path

import msgpack
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa

from xtalflow.domain.mxlive import MxLiveReadError
from xtalflow.infrastructure.mxlive_client import LegacyMxLiveReadClient
from xtalflow.infrastructure.mxlive_client import RequestsJsonTransport


class FakeTransport:
    def __init__(self, replies: list[object]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, float, Path | None]] = []

    def get_json(
        self, url: str, *, timeout_seconds: float, ca_bundle: Path | None
    ) -> object:
        self.calls.append((url, timeout_seconds, ca_bundle))
        return self.replies.pop(0)


def _key_file(path: Path) -> Path:
    key = dsa.generate_private_key(key_size=1024)
    private = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path.write_bytes(msgpack.packb({"private": private}))
    return path


def test_legacy_mxlive_reader_signs_and_reads_labworks(tmp_path: Path) -> None:
    transport = FakeTransport([[{
        "expri_id": "RawCrystal-202607-BRD4-01",
        "protein_name": "BRD4", "plate_code": "2069", "plate_well": "A01a",
    }]])
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("test CA", encoding="utf-8")
    client = LegacyMxLiveReadClient(
        "https://mxlive.example", "BL-5C", "scientist",
        _key_file(tmp_path / "keys.dsa"), ca_bundle=ca_bundle,
        timeout_seconds=7.5, transport=transport,
    )

    records = client.labworks("RawCrystal-202607-BRD4-01")

    assert records[0].protein_name == "BRD4"
    url, timeout, used_ca = transport.calls[0]
    assert url.startswith("https://mxlive.example/api/v2/scientist:")
    assert url.endswith("/labworks/BL-5C/RawCrystal-202607-BRD4-01/")
    assert (timeout, used_ca) == (7.5, ca_bundle)


def test_experiment_ids_are_unique_and_keep_server_order(tmp_path: Path) -> None:
    transport = FakeTransport([[
        {"expri_id": "EXP-2"}, {"expri_id": "EXP-1"}, {"expri_id": "EXP-2"},
    ]])
    client = LegacyMxLiveReadClient(
        "https://mxlive.example", "BL-5C", "scientist",
        _key_file(tmp_path / "keys.dsa"), transport=transport,
    )

    assert client.experiment_ids(2026) == ("EXP-2", "EXP-1")


def test_samples_are_read_without_discarding_unknown_fields(tmp_path: Path) -> None:
    transport = FakeTransport([[
        {"id": 42, "name": "sample-1", "container": "Puck-1", "port": "A1"}
    ]])
    client = LegacyMxLiveReadClient(
        "https://mxlive.example", "BL-5C", "scientist",
        _key_file(tmp_path / "keys.dsa"), transport=transport,
    )

    sample = client.samples()[0]

    assert sample.sample_id == "42"
    assert sample.raw["container"] == "Puck-1"


def test_missing_key_and_invalid_response_fail_explicitly(tmp_path: Path) -> None:
    with pytest.raises(MxLiveReadError, match="cannot read"):
        LegacyMxLiveReadClient(
            "https://mxlive.example", "BL-5C", "scientist", tmp_path / "missing"
        )

    client = LegacyMxLiveReadClient(
        "https://mxlive.example", "BL-5C", "scientist",
        _key_file(tmp_path / "keys.dsa"), transport=FakeTransport([{"error": "bad"}]),
    )
    with pytest.raises(MxLiveReadError, match="list of objects"):
        client.labworks("2026")


def test_insecure_http_and_missing_ca_bundle_are_rejected(tmp_path: Path) -> None:
    key = _key_file(tmp_path / "keys.dsa")
    with pytest.raises(ValueError, match="HTTPS"):
        LegacyMxLiveReadClient("http://mxlive.example", "BL-5C", "scientist", key)
    with pytest.raises(MxLiveReadError, match="CA bundle"):
        LegacyMxLiveReadClient(
            "https://mxlive.example", "BL-5C", "scientist", key,
            ca_bundle=tmp_path / "missing-ca.pem",
        )


def test_missing_python_ssl_support_has_an_actionable_error(monkeypatch) -> None:
    def no_ssl(*args, **kwargs):
        raise ImportError("SSL module is not available")

    monkeypatch.setattr("xtalflow.infrastructure.mxlive_client.requests.get", no_ssl)

    with pytest.raises(MxLiveReadError, match="Python SSL support"):
        RequestsJsonTransport().get_json(
            "https://mxlive.example", timeout_seconds=10, ca_bundle=None
        )
