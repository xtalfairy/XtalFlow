"""Past experiments folded from what MxLive holds, and the page that shows them."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from xtalflow.application.beamline_history import (
    ELSEWHERE,
    XTALFLOW,
    past_experiments,
)
from xtalflow.domain import PlanType
from xtalflow.domain.mxlive import MxLiveLabwork
from xtalflow.ui.mxlive_history_page import MxLiveHistoryPage


def _records(*rows) -> tuple[MxLiveLabwork, ...]:
    return tuple(MxLiveLabwork.from_mapping(row) for row in rows)


FRAGMENT = {
    "expri_id": "FragSC-202607-BRD4-01", "protein_name": "BRD4",
    "plate_code": "2069", "plate_well": "A01a", "crystal_no": 1,
    "soak_id": "Prestw-Frag-1001", "name": "jjh",
    "staff_comments": "Uploaded by XtalFlow · Fragment Screening",
    "harvest_time": "2026-07-02 09:10", "puck_name": "5C-0029", "puck_nohole": 1,
}


def test_records_fold_into_one_row_for_each_experiment() -> None:
    history = past_experiments(_records(
        FRAGMENT,
        {**FRAGMENT, "plate_well": "A02a", "crystal_no": 2,
         "harvest_time": None, "puck_name": None},
        {"expri_id": "PreTest-202504-CRPA-01", "protein_name": "CypA",
         "plate_code": "1803", "crystal_no": 1, "name": "fbdd",
         "staff_comments": "solvent pretest", "harvest_time": "2025-04-02",
         "puck_name": "5C-0012"},
    ))

    assert [item.experiment_id for item in history] == [
        "FragSC-202607-BRD4-01", "PreTest-202504-CRPA-01"
    ]
    fragment, pretest = history
    assert fragment.plan_type is PlanType.FRAGMENT_SCREENING
    assert (fragment.crystals, fragment.harvested) == (2, 1)
    assert not fragment.fully_harvested
    assert fragment.pucks == ("5C-0029",)
    assert fragment.protein == "BRD4" and fragment.plate_codes == ("2069",)
    # The month is the one in the ID: the month the ID was handed out.
    assert fragment.month == "2026-07"
    # XtalFlow signs its own uploads in the one field it writes.
    assert fragment.source == XTALFLOW
    assert fragment.uploaded_by == "jjh"

    # An older upload from XtalViewer: the prefix still gives the kind.
    assert pretest.plan_type is PlanType.CONDITION_TEST
    assert pretest.source == ELSEWHERE
    assert pretest.fully_harvested


def test_an_identifier_from_nowhere_is_shown_as_it_is() -> None:
    (unknown,) = past_experiments(_records({"expri_id": "Weird-01", "name": "x"}))

    assert unknown.plan_type is None and unknown.month == ""
    assert unknown.crystals == 1 and unknown.pucks == ()


def test_the_page_shows_the_account_and_never_offers_to_open_anything() -> None:
    app = QApplication.instance() or QApplication([])
    page = MxLiveHistoryPage()
    page.show_account("jjh", "BL-5C")
    page.show_years((2026, 2025))
    page.show_experiments(past_experiments(_records(
        FRAGMENT, {**FRAGMENT, "crystal_no": 2, "harvest_comment": "cracked"},
    )))

    assert page.title_label.text() == "jjh's experiments on BL-5C"
    assert page.years_label.text() == "2026 · 2025"
    row = [
        page.experiment_table.item(0, column).text()
        for column in range(page.experiment_table.columnCount())
    ]
    assert row == [
        "FragSC-202607-BRD4-01", "Fragment Screening", "BRD4", "2", "2 of 2",
        "5C-0029", "2026-07", "XtalFlow",
    ]
    # The crystals of the chosen experiment, including what the beamline wrote.
    page.experiment_table.selectRow(0)
    assert page.crystal_table.rowCount() == 2
    assert page.crystal_table.item(1, 9).text() == "cracked"
    assert "uploaded from the account jjh" in page.detail_label.text()
    # Read-only: there is nothing here that acts on an experiment.
    assert not hasattr(page, "resume_button") and not hasattr(page, "delete_button")
    page.close()
    app.processEvents()


def test_the_page_says_when_it_has_nothing_and_why() -> None:
    app = QApplication.instance() or QApplication([])
    page = MxLiveHistoryPage()
    page.show_experiments(())

    assert page.experiment_table.isHidden()
    assert "uploaded under your own MxLive key" in page.empty_label.text()
    page.show_status("No MxLive key is configured, so nothing can be read.", "attention")
    assert "No MxLive key" in page.status_label.text()
    page.close()
    app.processEvents()
