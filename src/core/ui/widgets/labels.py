# 🔧 config import
import os
from src.core.config.logger_config import colored_logger
logger = colored_logger()
current_file = os.path.basename(__file__) if '__file__' in globals() else "Notebook"
logger.info(f"Logger initialized ({current_file})")

import sys
import PyQt5
from PyQt5.QtWidgets import *

class Window(QWidget):
    def __init__(self, parent = None):
        super().__init__()
        self.setWindowTitle("Using Labels")
        self.setGeometry(500, 500, 1000, 1000)

        self.ui()

    def ui(self):
        text1 = QLabel("XXXXXXXXXXXXXXXXXXXXXXXXXXXX",self)
        text2 = QLabel("YYYYYYYYYYYYYYYYYYYYYYYYYYYY", self)
        text3 = QLabel("ZZZZZZZZZZZZZZZZZZZZZZZZZZZZ", self)
        text1.move(100,50)
        text2.move(200,100)
        text3.move(300,150)

        print("a")
        self.show()

def main():
    app = QApplication(sys.argv)
    window = Window()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()