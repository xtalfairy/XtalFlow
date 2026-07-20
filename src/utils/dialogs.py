from PyQt5.QtWidgets import QMessageBox

def show_warning(message):
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Warning)
    msg.setWindowTitle("Warning")
    msg.setText(message)
    msg.setStandardButtons(QMessageBox.Ok)
    msg.exec_()



