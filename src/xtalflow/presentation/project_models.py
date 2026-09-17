from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt5.QtCore import QAbstractListModel, QModelIndex, Qt

from xtalflow.domain import Project, ProjectImageSet, plate_format_by_id


class ProjectImageSetListModel(QAbstractListModel):
    """Image sets of the active workspace, shown as plate cards."""

    ImageSetIdRole = Qt.UserRole + 1
    PlateCodeRole = Qt.UserRole + 2
    DetailRole = Qt.UserRole + 3
    ProgressRole = Qt.UserRole + 4
    ActiveRole = Qt.UserRole + 5

    def __init__(self, target_count: Callable[[str], int]) -> None:
        super().__init__()
        self._project: Project | None = None
        self._target_count = target_count
        self._statistics: Mapping[str, object] = {}

    def set_project(self, project: Project | None) -> None:
        self.beginResetModel()
        self._project = project
        self.endResetModel()

    @property
    def image_sets(self) -> tuple[ProjectImageSet, ...]:
        return self._project.active_image_sets if self._project is not None else ()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self.image_sets)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.image_sets)):
            return None
        image_set = self.image_sets[index.row()]
        if role == self.ImageSetIdRole:
            return image_set.id
        if role == self.PlateCodeRole:
            return image_set.plate_code
        if role == self.DetailRole:
            return self._detail(image_set)
        if role == self.ProgressRole:
            return self._progress(image_set)
        if role == self.ActiveRole:
            return (
                self._project is not None
                and self._project.active_image_set_id == image_set.id
            )
        if role in (Qt.DisplayRole, Qt.AccessibleTextRole, Qt.ToolTipRole):
            return (
                f"Plate {image_set.plate_code}\n{self._detail(image_set)}\n"
                f"{self._progress(image_set)}"
            )
        return None

    def set_statistics(self, statistics: Mapping[str, object]) -> None:
        """Use per-image-set review statistics computed once for the whole list."""
        self._statistics = statistics
        self.refresh_counts()

    def refresh_counts(self) -> None:
        if self.image_sets:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self.image_sets) - 1, 0)
            )

    def _detail(self, image_set: ProjectImageSet) -> str:
        plate_format = plate_format_by_id(
            image_set.plate_format_id, image_set.plate_format_version
        )
        format_label = (
            plate_format.display_name if plate_format else "Unsupported plate format"
        )
        return f"Batch {image_set.batch_id} · {image_set.profile} · {format_label}"

    def _progress(self, image_set: ProjectImageSet) -> str:
        statistics = self._statistics.get(image_set.id)
        positions = self._target_count(image_set.id)
        if statistics is None:
            return f"{positions} positions"
        return (
            f"{statistics.reviewed_images}/{statistics.total_images} seen · "
            f"{statistics.target_images} "
            f"well{'' if statistics.target_images == 1 else 's'} selected"
        )
