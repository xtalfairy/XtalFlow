from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QAbstractListModel, QModelIndex, Qt

from xtalflow.domain import Project, ProjectImageSet, plate_format_by_id


class ProjectImageSetListModel(QAbstractListModel):
    ImageSetIdRole = Qt.UserRole + 1

    def __init__(self, target_count: Callable[[str], int]) -> None:
        super().__init__()
        self._project: Project | None = None
        self._target_count = target_count

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
        if role == Qt.DisplayRole:
            count = self._target_count(image_set.id)
            plate_format = plate_format_by_id(image_set.plate_format_id)
            format_label = (
                plate_format.display_name if plate_format else "Unsupported plate format"
            )
            return (
                f"Plate {image_set.plate_code}\n"
                f"Batch {image_set.batch_id} · {image_set.profile} · {count} targets\n"
                f"{format_label}"
            )
        if role == self.ImageSetIdRole:
            return image_set.id
        return None

    def refresh_counts(self) -> None:
        if self.image_sets:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self.image_sets) - 1, 0), [Qt.DisplayRole]
            )
