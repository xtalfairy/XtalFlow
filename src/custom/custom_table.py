from PyQt5.QtWidgets import QTableView, QMenu, QMessageBox, QAbstractItemView
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QStandardItemModel, QStandardItem


class PlatetTableView(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModel(QStandardItemModel(5, 3))
        self.model().setHorizontalHeaderLabels(['Column 1', 'Column 2', 'Column 3'])
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.open_context_menu)

    def open_context_menu(self, position):
        context_menu = QMenu(self)

        load_action = context_menu.addAction("불러오기")
        save_action = context_menu.addAction("저장하기")
        delete_action = context_menu.addAction("지우기")
        refresh_action = context_menu.addAction("새로고침")

        action = context_menu.exec_(self.viewport().mapToGlobal(position))

        if action == load_action:
            self.load_data()
        elif action == save_action:
            self.save_data()
        elif action == delete_action:
            self.delete_selected_rows()
        elif action == refresh_action:
            self.refresh_table()

    def load_data(self):
        QMessageBox.information(self, "불러오기", "데이터를 불러옵니다!")

    def save_data(self):
        QMessageBox.information(self, "저장하기", "데이터를 저장합니다!")

    def delete_selected_rows(self):
        selected_indexes = self.selectionModel().selectedRows()
        if not selected_indexes:
            QMessageBox.warning(self, "지우기", "선택된 행이 없습니다.")
            return

        for index in sorted(selected_indexes, reverse=True):
            self.model().removeRow(index.row())

        QMessageBox.information(self, "지우기", "선택된 행이 삭제되었습니다.")

    def refresh_table(self):
        self.model().clear()
        self.model().setHorizontalHeaderLabels(['Column 1', 'Column 2', 'Column 3'])
        for row in range(5):
            for col in range(3):
                item = QStandardItem(f"Cell {row + 1}, {col + 1}")
                self.model().setItem(row, col, item)
        QMessageBox.information(self, "새로고침", "테이블이 새로고침되었습니다!")
