from pathlib import Path

from xtalflow.domain.mxlive import MxLiveReadError
from xtalflow.mxlive_read import build_parser, main


def test_read_cli_requires_url_and_key_in_development() -> None:
    args = build_parser().parse_args(
        ["--url", "https://mxlive.example", "--key", "/keys.dsa"]
    )
    assert args.url == "https://mxlive.example"
    assert args.key == Path("/keys.dsa")


def test_read_cli_reports_safe_error_without_traceback(monkeypatch, capsys) -> None:
    class BrokenClient:
        def __init__(self, *args, **kwargs):
            raise MxLiveReadError("cannot read MxLive key file")

    monkeypatch.setattr("xtalflow.mxlive_read.LegacyMxLiveReadClient", BrokenClient)

    result = main(["--url", "https://mxlive.example", "--key", "/missing"])

    assert result == 2
    assert "cannot read MxLive key file" in capsys.readouterr().err
