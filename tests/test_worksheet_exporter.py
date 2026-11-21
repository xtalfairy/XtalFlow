import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from xtalflow.domain import SWISSCI_MIDI_3_LENS
from xtalflow.domain.fragment_screening import (
    CrystalTarget,
    Fragment,
    FragmentLibrary,
    SelectedCrystal,
    build_fragment_screen_plan,
)
from xtalflow.domain.raw_crystal import build_raw_crystal_plan
from xtalflow.infrastructure.worksheet_exporter import (
    InstrumentLocationUnavailable,
    WorksheetDestinationUnavailable,
    WorksheetExporter,
)
from xtalflow.settings import DEFAULT_SETTINGS, standard_instruments


def fragment_plan():
    fragment = Fragment(
        "Vendor",
        "Library",
        "1",
        "CMP-1",
        "C2H6O",
        Decimal("46.07"),
        "CCO",
        Decimal("100"),
        "DMSO",
        "SRC-1",
        "A01",
    )
    crystal = SelectedCrystal(
        "image",
        "2069",
        "A01d",
        (CrystalTarget("target", Decimal("0.25"), Decimal("-0.5"), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    return build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment,)), (crystal,), Decimal("25")
    )


def test_development_export_writes_user_scoped_echo_and_shifter_files(
    tmp_path: Path,
) -> None:
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650",
            tmp_path / "shifter1",
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )
    exporter = WorksheetExporter(settings, "scientist")

    first = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")
    second = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    assert first.path_for("echo650") == (
        tmp_path / "echo650" / "scientist" / "FragSC-202607-BRD4-01.csv"
    )
    assert first.path_for("echo650").is_file()
    assert first.path_for("shifter1").is_file()
    assert first.path_for("shifter2").is_file()
    assert second.file_stem == "FragSC-202607-BRD4-01_01"
    with first.path_for("shifter1").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.reader(stream))
    assert len(rows[0]) == 15
    assert len(rows[1]) == 15
    assert rows[1][6:] == [""] * 9


def test_missing_operating_mount_requires_explicit_alternate_root(
    tmp_path: Path,
) -> None:
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "missing-echo",
            tmp_path / "missing-shifter1",
            tmp_path / "missing-shifter2",
        ),
        create_missing_instrument_roots=False,
    )
    exporter = WorksheetExporter(settings, "scientist")

    with pytest.raises(WorksheetDestinationUnavailable, match="unavailable"):
        exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    result = exporter.export(
        fragment_plan(), "FragSC-202607-BRD4-01", tmp_path / "chosen"
    )
    assert result.path_for("echo650") == (
        tmp_path
        / "chosen"
        / "echo650"
        / "scientist"
        / "FragSC-202607-BRD4-01.csv"
    )


def test_permission_error_preparing_output_is_reported_without_crashing(
    tmp_path: Path, monkeypatch
) -> None:
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650",
            tmp_path / "shifter1",
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )
    original_mkdir = Path.mkdir

    def deny_runtime_directory(path, *args, **kwargs):
        if path == tmp_path / "echo650" / "scientist":
            raise PermissionError("permission denied for test")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_runtime_directory)

    with pytest.raises(
        WorksheetDestinationUnavailable, match="could not prepare instrument"
    ):
        WorksheetExporter(settings, "scientist").export(
            fragment_plan(), "FragSC-202607-BRD4-01"
        )


def test_offline_instrument_share_is_reported_as_unavailable_at_export(
    tmp_path: Path, monkeypatch
) -> None:
    offline = tmp_path / "shifter1"
    settings = replace(
        DEFAULT_SETTINGS,
        instruments=standard_instruments(
            tmp_path / "echo650",
            offline,
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=False,
    )
    original_is_dir = Path.is_dir

    def host_down(path):
        if path == offline or path == offline / "scientist":
            raise OSError(112, "Host is down")
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", host_down)
    exporter = WorksheetExporter(settings, "scientist")

    with pytest.raises(WorksheetDestinationUnavailable, match="unavailable"):
        exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")


def test_raw_crystal_export_writes_only_shifter_files(tmp_path: Path) -> None:
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650",
            tmp_path / "shifter1",
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )
    fragment = fragment_plan()
    raw_plan = build_raw_crystal_plan(fragment.selection)

    result = WorksheetExporter(settings, "scientist").export(
        raw_plan, "RawCrystal-202607-BRD4-01"
    )

    assert result.path_for("shifter1").is_file()
    assert result.path_for("shifter2").is_file()
    assert not (tmp_path / "echo650").exists()


def _development_settings(tmp_path: Path):
    return replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650",
            tmp_path / "shifter1",
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )


def _worksheet_files(tmp_path: Path) -> list[Path]:
    return sorted(
        path
        for name in ("echo650", "shifter1", "shifter2")
        for path in (tmp_path / name).rglob("*")
        if path.is_file()
    )


def test_failed_copy_leaves_no_worksheet_and_retry_reuses_experiment_id(
    tmp_path: Path, monkeypatch
) -> None:
    import xtalflow.infrastructure.worksheet_exporter as exporter_module

    original_copyfile = exporter_module.shutil.copyfile

    def shifter1_share_drops(source, destination, **kwargs):
        if "shifter1" in str(destination):
            raise OSError(112, "Host is down")
        return original_copyfile(source, destination, **kwargs)

    exporter = WorksheetExporter(_development_settings(tmp_path), "scientist")
    monkeypatch.setattr(exporter_module.shutil, "copyfile", shifter1_share_drops)

    with pytest.raises(WorksheetDestinationUnavailable, match="Host is down"):
        exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    assert _worksheet_files(tmp_path) == []
    monkeypatch.setattr(exporter_module.shutil, "copyfile", original_copyfile)
    result = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")
    assert result.file_stem == "FragSC-202607-BRD4-01"
    assert _worksheet_files(tmp_path) == sorted(
        (result.path_for("echo650"), result.path_for("shifter1"), result.path_for("shifter2"))
    )


def test_failed_rename_removes_worksheets_already_published(
    tmp_path: Path, monkeypatch
) -> None:
    import xtalflow.infrastructure.worksheet_exporter as exporter_module

    original_replace = exporter_module.os.replace

    def shifter2_rename_fails(source, destination):
        if "shifter2" in str(destination):
            raise PermissionError("permission denied for test")
        return original_replace(source, destination)

    monkeypatch.setattr(exporter_module.os, "replace", shifter2_rename_fails)

    with pytest.raises(WorksheetDestinationUnavailable, match="permission denied"):
        WorksheetExporter(_development_settings(tmp_path), "scientist").export(
            fragment_plan(), "FragSC-202607-BRD4-01"
        )

    assert _worksheet_files(tmp_path) == []


def test_invalid_plan_does_not_leave_staging_that_blocks_export(
    tmp_path: Path, monkeypatch
) -> None:
    import xtalflow.infrastructure.worksheet_exporter as exporter_module

    def invalid_plan(plan):
        raise ValueError("invalid plan for test")

    exporter = WorksheetExporter(_development_settings(tmp_path), "scientist")
    with monkeypatch.context() as patch:
        patch.setattr(exporter_module, "build_echo_worksheet", invalid_plan)
        with pytest.raises(ValueError, match="invalid plan"):
            exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    result = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    assert result.file_stem == "FragSC-202607-BRD4-01"
    assert list((tmp_path / "staging" / "scientist").iterdir()) == []


def test_unmounted_share_directory_is_not_used_for_instrument_output(
    tmp_path: Path, monkeypatch
) -> None:
    settings = replace(
        _development_settings(tmp_path),
        create_missing_instrument_roots=False,
        require_network_instrument_mounts=True,
    )
    for name in ("echo650", "shifter1", "shifter2"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(
        "xtalflow.infrastructure.worksheet_exporter.is_network_mount",
        lambda path: path.name != "shifter2",
    )

    with pytest.raises(
        WorksheetDestinationUnavailable, match="shifter2 \\(network share is not mounted\\)"
    ):
        WorksheetExporter(settings, "scientist").export(
            fragment_plan(), "FragSC-202607-BRD4-01"
        )

    assert _worksheet_files(tmp_path) == []


def _configured_settings(tmp_path: Path, instruments):
    return replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=instruments,
        create_missing_instrument_roots=True,
    )


def test_each_worksheet_goes_to_every_instrument_that_reads_it(tmp_path: Path) -> None:
    from xtalflow.domain.instruments import WorksheetKind
    from xtalflow.settings import InstrumentDestination

    instruments = (
        *standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        InstrumentDestination("shifter3", WorksheetKind.SHIFTER, tmp_path / "shifter3"),
    )
    exporter = WorksheetExporter(_configured_settings(tmp_path, instruments), "scientist")

    fragment = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")
    raw = exporter.export(
        build_raw_crystal_plan(fragment_plan().selection), "RawCrystal-202607-BRD4-01"
    )
    alternate = exporter.export(
        fragment_plan(), "FragSC-202607-BRD4-02", tmp_path / "chosen"
    )

    assert [output.instrument for output in fragment.outputs] == [
        "echo650", "shifter1", "shifter2", "shifter3"
    ]
    assert fragment.path_for("shifter3").read_text(encoding="utf-8") == (
        fragment.path_for("shifter1").read_text(encoding="utf-8")
    )
    assert fragment.path_for("echo650").read_text(encoding="utf-8") != (
        fragment.path_for("shifter1").read_text(encoding="utf-8")
    )
    assert [output.instrument for output in raw.outputs] == [
        "shifter1", "shifter2", "shifter3"
    ]
    assert alternate.path_for("shifter3") == (
        tmp_path / "chosen" / "shifter3" / "scientist" / "FragSC-202607-BRD4-02.csv"
    )


def test_plan_needing_an_unconfigured_worksheet_is_not_partly_exported(
    tmp_path: Path,
) -> None:
    from xtalflow.domain.instruments import WorksheetKind
    from xtalflow.settings import InstrumentDestination

    shifter_only = (
        InstrumentDestination("shifter1", WorksheetKind.SHIFTER, tmp_path / "shifter1"),
    )
    exporter = WorksheetExporter(_configured_settings(tmp_path, shifter_only), "scientist")

    with pytest.raises(
        WorksheetDestinationUnavailable, match="no instrument is configured for echo"
    ):
        exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    assert not (tmp_path / "shifter1").exists()
    raw = exporter.export(
        build_raw_crystal_plan(fragment_plan().selection), "RawCrystal-202607-BRD4-01"
    )
    assert [output.instrument for output in raw.outputs] == ["shifter1"]


def test_condition_test_sends_one_echo_file_per_dispense_time(tmp_path: Path) -> None:
    from xtalflow.domain import crystal_selection_from_selected_crystals
    from xtalflow.domain.condition_test import (
        Additive,
        ConditionTestDesign,
        Treatment,
        build_condition_test_plan,
        new_series,
    )
    from xtalflow.domain.plan_lifecycle import PlanRevision, WorksheetExportEvent
    from xtalflow.infrastructure import SQLiteReviewStore

    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1")
    glycerol = Additive("gly", "Glycerol", Decimal("50"), "OCC(O)CO", "LDV-1", "B1")
    series = new_series(
        glycerol.id, (Decimal("20"),), (0,), 1, before=(Treatment(dmso.id, Decimal("5"), 60),)
    )
    design = ConditionTestDesign((dmso, glycerol), (series,), Decimal("200"))
    crystal = SelectedCrystal(
        "image", "2069", "A01a",
        (CrystalTarget("target", Decimal("0"), Decimal("0"), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    plan = build_condition_test_plan(
        design, crystal_selection_from_selected_crystals("plan", (crystal,))
    )
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
    )

    result = WorksheetExporter(settings, "scientist").export(plan, "PreTest-202609-BRD4-01")

    echo_files = [Path(output.path).name for output in result.outputs if output.instrument == "echo650"]
    assert echo_files == ["PreTest-202609-BRD4-01_R1.csv", "PreTest-202609-BRD4-01_R2.csv"]
    with (tmp_path / "echo650" / "scientist" / echo_files[1]).open(encoding="utf-8") as stream:
        rows = list(csv.reader(stream))
    assert rows[1][:3] == ["LDV-1", "B1", "140.0"]

    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    event = WorksheetExportEvent(
        "export", "revision", "scientist", datetime.now(timezone.utc), "succeeded",
        result.outputs,
    )
    project_id = "workspace"
    from xtalflow.domain import Project
    from xtalflow.domain.plan_lifecycle import PlanningDraft

    workspace = Project("workspace", "Workspace", datetime.now(timezone.utc), datetime.now(timezone.utc))
    store.workspace.save_project(workspace)
    now = datetime.now(timezone.utc)
    store.planning.save_planning_draft(
        PlanningDraft("plan", project_id, "condition_test", "Test", None, "", "BRD4", "0",
                      "selection", now, now, None, None, design.to_json())
    )
    assert store.planning.load_planning_drafts(project_id)[0].details_json == design.to_json()
    revision = store.planning.finalize_plan_revision(
        PlanRevision("revision", "plan", 0, "PreTest-202609-BRD4-01", "{}", "scientist", now)
    )
    store.audit.record_worksheet_export(replace(event, revision_id=revision.id))
    (stored,) = store.audit.list_worksheet_exports(revision.id)
    assert [Path(output.path).name for output in stored.outputs] == [
        Path(output.path).name for output in result.outputs
    ]
    store.close()


def test_an_instrument_that_is_down_says_which_ones_are_up(tmp_path: Path) -> None:
    # The usual beamline case: one SHIFTER workstation is off, the other is not.
    (tmp_path / "shifter1").mkdir()
    (tmp_path / "echo650").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "missing-shifter2"
        ),
        create_missing_instrument_roots=False,
    )
    exporter = WorksheetExporter(settings, "scientist")

    with pytest.raises(InstrumentLocationUnavailable) as raised:
        exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    assert raised.value.unreachable == ("shifter2",)
    assert raised.value.reachable == ("echo650", "shifter1")
    assert raised.value.reachable_covers_every_worksheet


def test_saving_to_the_instruments_that_are_up_leaves_the_others_out(
    tmp_path: Path,
) -> None:
    (tmp_path / "shifter1").mkdir()
    (tmp_path / "echo650").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "missing-shifter2"
        ),
        create_missing_instrument_roots=False,
    )
    exporter = WorksheetExporter(settings, "scientist")

    result = exporter.export(
        fragment_plan(), "FragSC-202607-BRD4-01", instruments=("echo650", "shifter1")
    )

    assert [output.instrument for output in result.outputs] == ["echo650", "shifter1"]
    assert result.path_for("shifter1").is_file()
    assert not (tmp_path / "missing-shifter2").exists()


def test_leaving_out_the_only_instrument_of_a_kind_is_refused(tmp_path: Path) -> None:
    (tmp_path / "shifter1").mkdir()
    (tmp_path / "echo650").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
    )
    exporter = WorksheetExporter(settings, "scientist")

    # A fragment screen dispenses as well as harvests, so dropping the ECHO
    # would leave its worksheet with nowhere to go.
    with pytest.raises(WorksheetDestinationUnavailable, match="no instrument is configured"):
        exporter.export(
            fragment_plan(), "FragSC-202607-BRD4-01", instruments=("shifter1",)
        )


def test_each_account_builds_worksheets_in_a_folder_of_its_own(tmp_path, monkeypatch):
    """Many researchers share the beamline machine and its temporary folder.

    A folder below one they share is made by whoever saves first, and that
    account's umask then decides whether the next one can save at all.
    """
    import os
    import tempfile

    from dataclasses import replace as replace_fields

    from xtalflow.infrastructure.worksheet_exporter import WorksheetExporter
    from xtalflow.scratch import refuse_a_folder_that_is_not_ours

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    settings = replace_fields(
        DEFAULT_SETTINGS, worksheet_staging_directory=None,
    )

    roots = {
        name: WorksheetExporter(settings, name)._staging_root()
        for name in ("jjh", "kim")
    }

    assert roots["jjh"] == tmp_path / "xtalflow-jjh"
    assert roots["kim"] == tmp_path / "xtalflow-kim"
    # Neither sits below a folder the other had to create first.
    assert roots["jjh"].parent == roots["kim"].parent == tmp_path
    assert all(path.is_dir() for path in roots.values())
    assert roots["jjh"].stat().st_mode & 0o077 == 0

    # A folder anyone can write to is refused, whoever made it.
    shared = tmp_path / "xtalflow-shared"
    shared.mkdir(mode=0o777)
    os.chmod(shared, 0o777)
    with pytest.raises(PermissionError, match="writable by others"):
        refuse_a_folder_that_is_not_ours(shared)

    # So is one that belongs to somebody else.
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(roots["jjh"]).st_uid + 1)
    with pytest.raises(PermissionError, match="belongs to another account"):
        refuse_a_folder_that_is_not_ours(roots["jjh"])


def test_the_window_draws_its_arrows_from_a_folder_of_its_own(tmp_path, monkeypatch):
    """The same shared-folder trap, in the one other place that writes files."""
    import tempfile

    from xtalflow.ui import theme

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr("xtalflow.scratch.getpass.getuser", lambda: "kim")

    drawn = theme._chevron("down", theme.TEXT_MUTED)

    assert drawn.startswith(str(tmp_path / "xtalflow-ui-kim"))
    assert Path(drawn).is_file()

    # A folder somebody else left behind is not written into, and the window
    # opens without its arrows rather than borrowing their file.
    monkeypatch.setattr("xtalflow.scratch.os.geteuid", lambda: -1)
    assert theme._chevron("up", theme.TEXT_MUTED) == ""


def test_a_condition_test_harvested_at_several_times_sends_one_shifter_file_each(
    tmp_path: Path,
) -> None:
    """The worksheet has no time column, so the file is where the boundary goes.

    A single file of every well says nothing about where one time point ends,
    and overrunning that boundary is the one mistake a condition test cannot
    tolerate. One file per harvest time makes finishing the file the same
    thing as finishing the time point - which is what the operator at BL-5C
    did by hand before this existed.
    """
    from xtalflow.domain import crystal_selection_from_selected_crystals
    from xtalflow.domain.condition_test import (
        Additive,
        ConditionTestDesign,
        build_condition_test_plan,
        new_series,
    )
    from xtalflow.domain.worksheets import build_shifter_worksheet

    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1")
    design = ConditionTestDesign(
        (dmso,), (new_series(dmso.id, (Decimal("10"),), (0, 90, 960), 1),), Decimal("200")
    )
    now = datetime.now(timezone.utc)
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "2069", f"A{index + 1:02d}a",
            (CrystalTarget(f"target-{index}", Decimal("0"), Decimal("0"),
                           now + timedelta(seconds=index)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(3)
    )
    plan = build_condition_test_plan(
        design, crystal_selection_from_selected_crystals("plan", crystals)
    )
    assert [minute for minute, _group in plan.harvest_groups] == [0, 90, 960]
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
    )

    result = WorksheetExporter(settings, "scientist").export(plan, "PreTest-202611-BRD4-01")

    # Named for the time since the first dispense, ordered by the index so a
    # folder listing is in harvest order rather than alphabetical.
    for station in ("shifter1", "shifter2"):
        assert [
            Path(output.path).name for output in result.outputs
            if output.instrument == station
        ] == [
            "PreTest-202611-BRD4-01_H1_0h00m.csv",
            "PreTest-202611-BRD4-01_H2_1h30m.csv",
            "PreTest-202611-BRD4-01_H3_16h00m.csv",
        ]
    # Dispensed once, so the ECHO keeps one unsuffixed file.
    assert [
        Path(output.path).name for output in result.outputs
        if output.instrument == "echo650"
    ] == ["PreTest-202611-BRD4-01.csv"]

    folder = tmp_path / "shifter1" / "scientist"
    written = []
    for name in sorted(path.name for path in folder.glob("*.csv")):
        with (folder / name).open(encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
        # Each file carries the header and the wells due at that one time.
        assert rows[0][0] == ";PlateType"
        written.append([row[:6] for row in rows[1:]])
    assert [len(rows) for rows in written] == [1, 1, 1]
    # Together they are the whole worksheet, each well once.
    assert [row for rows in written for row in rows] == [
        list(row.values()[:6]) for row in build_shifter_worksheet(plan)
    ]


def test_a_condition_test_harvested_once_keeps_a_single_shifter_file(tmp_path: Path) -> None:
    """A suffix that never varies is noise, as with the single ECHO round."""
    from xtalflow.domain import crystal_selection_from_selected_crystals
    from xtalflow.domain.condition_test import (
        Additive,
        ConditionTestDesign,
        build_condition_test_plan,
        new_series,
    )

    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1")
    design = ConditionTestDesign(
        (dmso,), (new_series(dmso.id, (Decimal("10"),), (60,), 1),), Decimal("200")
    )
    crystal = SelectedCrystal(
        "image", "2069", "A01a",
        (CrystalTarget("target", Decimal("0"), Decimal("0"), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    plan = build_condition_test_plan(
        design, crystal_selection_from_selected_crystals("plan", (crystal,))
    )
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
    )

    result = WorksheetExporter(settings, "scientist").export(plan, "PreTest-202611-BRD4-02")
    assert [Path(output.path).name for output in result.outputs] == [
        "PreTest-202611-BRD4-02.csv"
    ] * 3


def test_a_file_saved_earlier_without_suffixes_is_not_overwritten(tmp_path: Path) -> None:
    """The stem search only looked for names built from this save's suffixes.

    A save whose files all carry a suffix - several ECHO rounds, or a split
    harvest - never asked whether the bare name was taken, so it could take a
    stem an earlier save's files already used. Nothing was overwritten, but
    two saves then shared a stem and only the audit could tell them apart.
    """
    from xtalflow.domain import crystal_selection_from_selected_crystals
    from xtalflow.domain.condition_test import (
        Additive,
        ConditionTestDesign,
        build_condition_test_plan,
        new_series,
    )

    from xtalflow.domain.condition_test import Treatment

    # Two dispense times and two harvest times, so every file of this save
    # carries a suffix and none of them is the bare name.
    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1")
    glycerol = Additive("gly", "Glycerol", Decimal("50"), "OCC(O)CO", "LDV-1", "B1")
    design = ConditionTestDesign(
        (dmso, glycerol),
        (new_series(
            glycerol.id, (Decimal("20"),), (0, 60), 1,
            before=(Treatment(dmso.id, Decimal("5"), 60),),
        ),),
        Decimal("200"),
    )
    now = datetime.now(timezone.utc)
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "2069", f"A{index + 1:02d}a",
            (CrystalTarget(f"target-{index}", Decimal("0"), Decimal("0"),
                           now + timedelta(seconds=index)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(2)
    )
    plan = build_condition_test_plan(
        design, crystal_selection_from_selected_crystals("plan", crystals)
    )
    assert [minute for minute, _group in plan.harvest_groups] == [60, 120]
    assert len(plan.dispense_rounds) == 2
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
    )
    # An earlier save of the same experiment, before harvests were split.
    folder = tmp_path / "shifter1" / "scientist"
    folder.mkdir(parents=True)
    (folder / "PreTest-202611-BRD4-03.csv").write_text("earlier\n", encoding="utf-8")

    result = WorksheetExporter(settings, "scientist").export(plan, "PreTest-202611-BRD4-03")

    assert [
        Path(output.path).name for output in result.outputs
        if output.instrument == "shifter1"
    ] == [
        "PreTest-202611-BRD4-03_01_H1_1h00m.csv",
        "PreTest-202611-BRD4-03_01_H2_2h00m.csv",
    ]
    assert (folder / "PreTest-202611-BRD4-03.csv").read_text(encoding="utf-8") == "earlier\n"


def test_a_held_worksheet_does_not_take_a_name_an_earlier_save_used(
    tmp_path: Path, monkeypatch
) -> None:
    """With every share down, the held files took r1's names exactly.

    A stem is chosen by looking in the folders being written to, and when
    an instrument cannot be reached the folder being written to is one of
    ours - which knows nothing of the file r1 left on the instrument. A
    held worksheet is delivered by somebody carrying it across by hand, so
    all three of r2's files landed on top of r1's without a word.

    It does not show when one share is up, because the stem is shared
    across the destinations and the reachable one still holds r1's file.
    Every share down is the case, and that is an ordinary morning.
    """
    echo, first, second = (
        tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
    )
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        pending_worksheet_directory=tmp_path / "pending",
        instruments=standard_instruments(echo, first, second),
        create_missing_instrument_roots=True,
    )
    experiment_id = "FragSC-202611-BRD4-01"
    saved = WorksheetExporter(settings, "scientist").export(
        fragment_plan(), experiment_id
    )
    on_the_instruments = frozenset(Path(item.path).name for item in saved.outputs)

    assert on_the_instruments == {f"{experiment_id}.csv"}

    real_is_dir = Path.is_dir
    monkeypatch.setattr(
        Path, "is_dir",
        lambda self: False
        if any(self in (share, share / "scientist") for share in (echo, first, second))
        else real_is_dir(self),
    )
    held = WorksheetExporter(
        replace(settings, create_missing_instrument_roots=False), "scientist"
    ).export(
        fragment_plan(), experiment_id, hold_unreachable=True,
        names_already_used=on_the_instruments,
    )

    assert {Path(item.path).name for item in held.outputs} == {
        f"{experiment_id}_01.csv"
    }
    # Each one waits under the instrument it is for, and none of them is
    # named what that instrument already holds.
    assert {
        (Path(item.path).parent.name, Path(item.path).name) for item in held.outputs
    } == {
        ("echo650", f"{experiment_id}_01.csv"),
        ("shifter1", f"{experiment_id}_01.csv"),
        ("shifter2", f"{experiment_id}_01.csv"),
    }



def test_an_experiment_id_that_cannot_be_a_filename_is_refused(tmp_path: Path) -> None:
    """Measured on this beamline: two files named exactly ".csv", from 2021.

    A header line, no rows, saved into both SHIFTER folders by something with
    an empty experiment name. Nothing in this program refused to make one:
    the stem is the experiment id, so an empty id writes ".csv" onto a shared
    instrument mountpoint.

    That is not a file anybody finds again. ``Path(".csv").suffix`` is "", a
    ``*.csv`` listing skips it - the beamline's own sweep of those folders
    missed exactly these two for that reason - and two of them from different
    experiments are one name. The username is checked this way already, and
    for the same reason: it becomes a folder.
    """
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )
    exporter = WorksheetExporter(settings, "scientist")

    for refused in ("", "   ", ".csv", ".hidden", "a/b", "a\\b"):
        with pytest.raises(ValueError, match="invalid worksheet experiment id"):
            exporter.export(fragment_plan(), refused)

    # Nothing was written while being refused, and a real id still works.
    assert not list((tmp_path / "shifter1").rglob("*"))
    written = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")
    assert written.path_for("shifter1").name == "FragSC-202607-BRD4-01.csv"
