from __future__ import annotations

from pathlib import Path

import msgpack
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa

from xtalflow.domain.mxlive import (
    MxLivePartialWriteError, MxLiveReadError, MxLiveWriteError,
)
from xtalflow.infrastructure.mxlive_client import LegacyMxLiveReadClient, LegacyMxLiveWriteClient
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

    def post_msgpack(
        self, url: str, payload: object, *, timeout_seconds: float,
        ca_bundle: Path | None,
    ) -> object:
        self.calls.append((url, timeout_seconds, ca_bundle))
        self.posted_payload = payload
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


def test_legacy_mxlive_writer_posts_labwork_list_to_signed_endpoint(
    tmp_path: Path,
) -> None:
    transport = FakeTransport([{"created": 2}])
    client = LegacyMxLiveWriteClient(
        "https://mxlive.example", "BL-5C", "fbdd",
        _key_file(tmp_path / "keys.dsa"), transport=transport,
    )
    records = ({"expri_id": "RawCrystal-1", "project_id": "fbdd"},)

    response = client.upload_labworks(records)

    assert response == {"uploaded_count": 1, "responses": [{"created": 2}]}
    assert transport.posted_payload == records[0]
    assert transport.calls[0][0].startswith(
        "https://mxlive.example/api/v2/fbdd:"
    )
    assert transport.calls[0][0].endswith("/upload_labworks/BL-5C/")
    assert client.endpoint == "https://mxlive.example/upload_labworks/BL-5C/"


def test_writer_rejects_non_object_response(tmp_path: Path) -> None:
    client = LegacyMxLiveWriteClient(
        "https://mxlive.example", "BL-5C", "fbdd",
        _key_file(tmp_path / "keys.dsa"), transport=FakeTransport([["unexpected"]]),
    )

    with pytest.raises(MxLiveWriteError, match="must be an object"):
        client.upload_labworks(({"expri_id": "RawCrystal-1"},))


def test_writer_posts_each_legacy_record_separately(tmp_path: Path) -> None:
    transport = FakeTransport([{"id": 1}, {"id": 2}])
    transport.posted_payloads = []
    original_post = transport.post_msgpack

    def capture(*args, **kwargs):
        result = original_post(*args, **kwargs)
        transport.posted_payloads.append(transport.posted_payload)
        return result

    transport.post_msgpack = capture
    client = LegacyMxLiveWriteClient(
        "https://mxlive.example", "BL-5C", "fbdd",
        _key_file(tmp_path / "keys.dsa"), transport=transport,
    )

    result = client.upload_labworks(({"crystal_no": 1}, {"crystal_no": 2}))

    assert transport.posted_payloads == [{"crystal_no": 1}, {"crystal_no": 2}]
    assert result["uploaded_count"] == 2


def test_writer_reports_partial_batch_without_suggesting_blind_retry(
    tmp_path: Path,
) -> None:
    class PartialTransport(FakeTransport):
        def post_msgpack(self, *args, **kwargs):
            if not self.replies:
                raise MxLiveWriteError("HTTP 500")
            return super().post_msgpack(*args, **kwargs)

    client = LegacyMxLiveWriteClient(
        "https://mxlive.example", "BL-5C", "fbdd",
        _key_file(tmp_path / "keys.dsa"),
        transport=PartialTransport([{"id": 1}]),
    )

    with pytest.raises(MxLivePartialWriteError) as captured:
        client.upload_labworks(({"crystal_no": 1}, {"crystal_no": 2}))

    assert captured.value.completed_count == 1
    assert "do not retry" in str(captured.value)


class _Response:
    def __init__(self, status_code: int, body: object = None) -> None:
        self.status_code = status_code
        self.body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)

    def json(self) -> object:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


@pytest.mark.parametrize(
    ("outcome", "uncertain"),
    [
        ("read-timeout", True),
        ("connection-reset", True),
        ("http-502", True),
        ("invalid-json", True),
        ("connect-timeout", False),
        ("http-400", False),
    ],
)
def test_upload_errors_that_may_have_stored_records_are_uncertain(
    monkeypatch, outcome: str, uncertain: bool
) -> None:
    import requests

    from xtalflow.domain.mxlive import MxLiveUncertainWriteError

    def post(*args, **kwargs):
        if outcome == "read-timeout":
            raise requests.ReadTimeout()
        if outcome == "connection-reset":
            raise requests.ConnectionError("connection reset by peer")
        if outcome == "connect-timeout":
            raise requests.ConnectTimeout()
        if outcome == "invalid-json":
            return _Response(200, ValueError("not JSON"))
        return _Response(int(outcome.split("-")[1]))

    monkeypatch.setattr("xtalflow.infrastructure.mxlive_client.requests.post", post)

    with pytest.raises(MxLiveWriteError) as captured:
        RequestsJsonTransport().post_msgpack(
            "https://mxlive.example", {"expri_id": "RawCrystal-1"},
            timeout_seconds=10, ca_bundle=None,
        )

    assert isinstance(captured.value, MxLiveUncertainWriteError) is uncertain
    assert "mxlive.example" not in str(captured.value)


def test_uncertain_failure_after_accepted_records_reports_minimum_count(
    tmp_path: Path,
) -> None:
    from xtalflow.domain.mxlive import MxLiveUncertainWriteError

    class TimeoutTransport(FakeTransport):
        def post_msgpack(self, *args, **kwargs):
            if not self.replies:
                raise MxLiveUncertainWriteError("ReadTimeout")
            return super().post_msgpack(*args, **kwargs)

    client = LegacyMxLiveWriteClient(
        "https://mxlive.example", "BL-5C", "fbdd",
        _key_file(tmp_path / "keys.dsa"),
        transport=TimeoutTransport([{"id": 1}]),
    )

    with pytest.raises(MxLivePartialWriteError) as captured:
        client.upload_labworks(({"crystal_no": 1}, {"crystal_no": 2}))

    assert captured.value.outcome_uncertain
    assert "at least 1 of 2" in str(captured.value)


def test_beamline_progress_counts_harvests_and_collects_pucks() -> None:
    from xtalflow.domain.mxlive import MxLiveLabwork, beamline_progress

    records = [
        {"expri_id": "A", "puck_name": "5C-0029", "harvest_time": "2026-06-02 10:00"},
        {"expri_id": "A", "puck_name": "5C-0029", "harvest_time": "2026-06-02 10:05"},
        {"expri_id": "B", "puck_name": None, "harvest_time": None},
        {"expri_id": "C", "puck_name": "5C-0030", "harvest_time": "2026-06-02 11:00"},
        {"expri_id": "C", "puck_name": "None", "harvest_time": "None"},
    ]
    progress = beamline_progress(
        MxLiveLabwork.from_mapping(record) for record in records
    )

    assert progress["A"].fully_harvested
    assert progress["A"].pucks == ("5C-0029",)
    assert not progress["B"].fully_harvested and progress["B"].records == 1
    # MxLive writes the string "None" for an empty field; it is not a puck.
    assert progress["C"] == progress["C"].__class__(2, 1, ("5C-0030",))
    assert not progress["C"].fully_harvested


def _rendered(error: BaseException) -> str:
    import traceback

    return "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )


SIGNED = "jjh:AAAAEXAMPLESIGNATUREBBBB"
SIGNED_URL = f"https://mxlive.example/api/v2/{SIGNED}/labworks/BL-5C/2026/"


def _refusing(*, status: int | None):
    """A requests call that fails the way the network really fails.

    Either before the request arrives, which requests reports as a connection
    error, or with a status, which it reports as an HTTPError carrying the
    response. Both messages quote the URL whole, which is how the signature
    gets into them.
    """
    import requests

    message = f"HTTPSConnectionPool: Max retries exceeded with url: {SIGNED_URL}"
    if status is None:
        def refuse(*args: object, **kwargs: object) -> object:
            raise requests.ConnectionError(message)

        return refuse

    class Response:
        status_code = status

        def raise_for_status(self) -> None:
            raise requests.HTTPError(
                f"{status} Client Error for url: {SIGNED_URL}", response=self
            )

    def answer(*args: object, **kwargs: object) -> object:
        return Response()

    return answer


def _assert_hidden(error: BaseException, expected: str) -> None:
    rendered = _rendered(error)
    assert SIGNED not in rendered, rendered
    assert "EXAMPLESIGNATURE" not in rendered, rendered
    # What a reader is left with instead, and needs.
    assert expected in rendered, rendered


def test_a_refused_read_keeps_the_signature_out_of_its_traceback(monkeypatch) -> None:
    """The one place the account's signature must never reach is a log.

    It travels in the URL, and a requests error's own message quotes that URL
    whole. The operations log writes tracebacks with their causes - from both
    excepthooks and from a worker thread that dies - so chaining the original
    error would put the signature in the log by a path nobody would think to
    look at. What is kept instead is the status, or the error's class.
    """
    import requests

    monkeypatch.setattr(requests, "get", _refusing(status=None))
    with pytest.raises(MxLiveReadError) as raised:
        RequestsJsonTransport().get_json(
            SIGNED_URL, timeout_seconds=1.0, ca_bundle=None
        )
    _assert_hidden(raised.value, "ConnectionError")


def test_a_read_refused_with_a_status_keeps_the_signature_out_too(monkeypatch) -> None:
    """The other half: an answer from the server, whose message also has it."""
    import requests

    monkeypatch.setattr(requests, "get", _refusing(status=404))
    with pytest.raises(MxLiveReadError) as raised:
        RequestsJsonTransport().get_json(
            SIGNED_URL, timeout_seconds=1.0, ca_bundle=None
        )
    _assert_hidden(raised.value, "HTTP 404")


def test_a_write_refused_outright_keeps_the_signature_out_of_its_traceback(
    monkeypatch,
) -> None:
    """The write matters more: its body is the harvest, and 400 is definite.

    A status under 500 is the branch that says nothing was stored, which is
    the one a reader acts on, so it is the one checked here. The connection
    error below is the other branch, where it is unknown.
    """
    import requests

    from xtalflow.domain.mxlive import MxLiveUncertainWriteError

    monkeypatch.setattr(requests, "post", _refusing(status=400))
    with pytest.raises(MxLiveWriteError) as raised:
        RequestsJsonTransport().post_msgpack(
            SIGNED_URL, {"plate_well": "A01a"}, timeout_seconds=1.0, ca_bundle=None
        )
    assert not isinstance(raised.value, MxLiveUncertainWriteError)
    _assert_hidden(raised.value, "HTTP 400")


def test_a_write_of_unknown_outcome_keeps_the_signature_out_as_well(
    monkeypatch,
) -> None:
    """An upload that may or may not have been stored says so, and no more."""
    import requests

    from xtalflow.domain.mxlive import MxLiveUncertainWriteError

    monkeypatch.setattr(requests, "post", _refusing(status=None))
    with pytest.raises(MxLiveUncertainWriteError) as raised:
        RequestsJsonTransport().post_msgpack(
            SIGNED_URL, {"plate_well": "A01a"}, timeout_seconds=1.0, ca_bundle=None
        )
    _assert_hidden(raised.value, "ConnectionError")


def _answering_with_html(*, raising: bool = True):
    """A 2xx whose body is not JSON: a proxy or a login page in the way.

    requests raises its own JSONDecodeError for this, which subclasses both
    RequestException and ValueError. The handler for RequestException comes
    first, so without care this reads as a request that failed.
    """
    import requests

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            if raising:
                raise requests.exceptions.JSONDecodeError(
                    "Expecting value", "<html>Sign in</html>", 0
                )
            return {"id": 1}

    def answer(*args: object, **kwargs: object) -> object:
        return Response()

    return answer


def test_a_body_that_is_not_json_is_said_to_be_that(monkeypatch) -> None:
    """Rather than reported as a failed request, which it was not."""
    import requests

    monkeypatch.setattr(requests, "get", _answering_with_html())
    with pytest.raises(MxLiveReadError, match="invalid JSON"):
        RequestsJsonTransport().get_json(
            "https://mxlive.example/x", timeout_seconds=1.0, ca_bundle=None
        )


def test_an_upload_answered_with_a_non_json_body_is_uncertain_not_failed(
    monkeypatch,
) -> None:
    """The one classification that decides what may be done next.

    MxLive answered, so the record is very likely stored. Calling that a
    write which definitely did not happen is the dangerous direction: the
    upload would be recorded as failed while MxLive holds the records, and
    then nothing could be added to them - a harvest needs an upload that
    succeeded, and a second attempt is refused because the records are
    already there. Uncertain instead sends it to the path that reads MxLive
    back and settles what really happened.
    """
    import requests

    from xtalflow.domain.mxlive import MxLiveUncertainWriteError

    monkeypatch.setattr(requests, "post", _answering_with_html())
    with pytest.raises(MxLiveUncertainWriteError, match="invalid JSON"):
        RequestsJsonTransport().post_msgpack(
            "https://mxlive.example/x", {"plate_well": "A01a"},
            timeout_seconds=1.0, ca_bundle=None,
        )


def _raising(error_name: str):
    import requests

    def refuse(*args: object, **kwargs: object) -> object:
        raise getattr(requests.exceptions, error_name)("refused")

    return refuse


def test_a_url_requests_refused_to_build_is_a_write_that_did_not_happen(
    monkeypatch,
) -> None:
    """Nothing left this machine, so nothing is stored and saying so is right.

    These are also ValueError subclasses, so while the body was read inside
    the same try block they arrived as "MxLive returned invalid JSON after
    accepting the upload" - a sentence claiming MxLive had answered when no
    request had been sent.
    """
    import requests

    from xtalflow.domain.mxlive import MxLiveUncertainWriteError

    for name in (
        "MissingSchema", "InvalidSchema", "InvalidURL", "InvalidProxyURL",
        "InvalidHeader",
    ):
        monkeypatch.setattr(requests, "post", _raising(name))
        with pytest.raises(MxLiveWriteError) as raised:
            RequestsJsonTransport().post_msgpack(
                "https://mxlive.example/x", {"plate_well": "A01a"},
                timeout_seconds=1.0, ca_bundle=None,
            )
        assert not isinstance(raised.value, MxLiveUncertainWriteError), name
        assert "invalid JSON" not in str(raised.value), name
        assert name in str(raised.value), name


def test_a_reply_that_broke_while_arriving_leaves_the_outcome_unknown(
    monkeypatch,
) -> None:
    """The server had the request, so it may have stored the record.

    Both of these are raised while reading the reply, and both were called
    definite failures by a rule that listed what it knew to be safe and
    assumed the rest. For an endpoint with no delete the assumption has to go
    the other way.
    """
    import requests

    from xtalflow.domain.mxlive import MxLiveUncertainWriteError

    for name in ("ChunkedEncodingError", "ContentDecodingError"):
        monkeypatch.setattr(requests, "post", _raising(name))
        with pytest.raises(MxLiveUncertainWriteError) as raised:
            RequestsJsonTransport().post_msgpack(
                "https://mxlive.example/x", {"plate_well": "A01a"},
                timeout_seconds=1.0, ca_bundle=None,
            )
        assert name in str(raised.value), name
