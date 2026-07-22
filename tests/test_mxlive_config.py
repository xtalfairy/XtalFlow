from pathlib import Path

from xtalflow.infrastructure.mxlive_config import resolve_mxlive_account


def test_account_defaults_to_os_username_without_mapping(tmp_path: Path) -> None:
    account = resolve_mxlive_account(
        tmp_path / "missing.toml",
        os_username="fbdd",
        base_url="https://mxlive.example",
        key_path=tmp_path / "keys.dsa",
    )

    assert account.username == "fbdd"
    assert account.account_id == "fbdd"
    assert not account.explicitly_mapped


def test_external_config_maps_os_user_to_different_mxlive_identity(
    tmp_path: Path,
) -> None:
    key = tmp_path / "remote.dsa"
    key.write_bytes(b"key")
    config = tmp_path / "xtalflow.toml"
    config.write_text(
        '[mxlive]\nbase_url = "https://configured.example"\nbeamline = "BL-X"\n'
        '[mxlive.accounts.local]\nusername = "remote"\naccount_id = "owner-42"\n'
        f'key_path = "{key}"\n',
        encoding="utf-8",
    )

    account = resolve_mxlive_account(config, os_username="local")

    assert account.username == "remote"
    assert account.account_id == "owner-42"
    assert account.key_path == key
    assert account.base_url == "https://configured.example"
    assert account.beamline == "BL-X"
    assert account.explicitly_mapped
    assert account.upload_ready


def test_unmapped_root_is_never_upload_ready(tmp_path: Path) -> None:
    key = tmp_path / "keys.dsa"
    key.write_bytes(b"key")
    account = resolve_mxlive_account(
        None, os_username="root", base_url="https://mxlive.example", key_path=key
    )

    assert not account.upload_ready
    assert "root requires" in account.upload_blockers[0]
