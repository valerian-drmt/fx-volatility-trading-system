# 🔧 config import
import os
from src.core.config.logger_config import colored_logger
logger = colored_logger()
current_file = os.path.basename(__file__) if '__file__' in globals() else "Notebook"
logger.info(f"Logger initialized ({current_file})")

import sys
import PyQt5
from PyQt5.QtWidgets import QApplication, QWidget

class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setGeometry(500, 500, 1000, 1000)
        self.setWindowTitle("Trading Project")
        self.show()



if __name__ == "__main__":
    App = QApplication(sys.argv)
    window = Window()
    sys.exit(App.exec())
