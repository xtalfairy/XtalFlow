from pathlib import Path

from xtalflow.infrastructure import mxlive_config
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


def test_the_key_file_is_looked_at_once_and_again_when_it_matters(
    tmp_path: Path, monkeypatch
) -> None:
    """The key path is network storage at BL-5C, and this is asked constantly.

    ``upload_blockers`` and ``upload_ready`` are both read on every refresh
    of the MxLive preview, which is every keystroke in the protein, rows and
    volume fields, in all three editors. Each read used to stat the key file
    - ``/data/users/<user>/.config/mxdc/keys.dsa`` - on the thread the window
    is drawn on. On a dropped mount one look is the best part of a minute.
    """
    key = tmp_path / "keys.dsa"
    looks = []
    real_is_file = Path.is_file

    def counting_is_file(self):
        if key == self:
            looks.append(self)
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", counting_is_file)
    account = resolve_mxlive_account(
        None, os_username="fbdd", base_url="https://mxlive.example", key_path=key,
    )

    for _ in range(40):
        assert not account.upload_ready
        assert "key file is not available" in account.upload_blockers[0]

    assert len(looks) == 1

    # The key file is put in place while the application is running - or
    # /data/users is mounted, which is the same thing. A refusal kept for
    # the session was worse than the freeze it replaced: the upload button
    # is disabled from this answer and the button is the only caller of
    # rechecked(), so the key file could never be believed.
    key.write_bytes(b"a key")

    assert not account.upload_ready  # still inside the recheck window

    monkeypatch.setattr(mxlive_config, "RECHECK_BLOCKERS_SECONDS", 0.0)

    assert account.upload_ready
    assert account.upload_blockers == ()

    # A clear answer is kept for good: a key file nobody took away is still
    # there, and one that goes away under an upload is caught by the
    # recheck at the upload itself.
    looked = len(looks)
    for _ in range(40):
        assert account.upload_ready

    assert len(looks) == looked
    assert account.rechecked().upload_ready
    assert len(looks) == looked + 1

