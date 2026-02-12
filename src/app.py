import sys

from PyQt5.QtWidgets import QApplication
from ib_insync import IB

from ui.main_window import LiveTickWindow


def main():
    app = QApplication(sys.argv)

    ib = IB()
    window = LiveTickWindow(ib, max_candles=500)
    window.resize(900, 600)
    window.show()

    exit_code = app.exec_()

    if ib.isConnected():
        ib.disconnect()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
