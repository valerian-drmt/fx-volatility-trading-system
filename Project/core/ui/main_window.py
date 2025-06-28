from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout
import core.ui.widgets.candlestick_widget as candlestick_widget
import pandas as pd

# 🔧 config import
import os
from core.config.logger_config import colored_logger
logger = colored_logger()
current_file = os.path.basename(__file__) if '__file__' in globals() else "Notebook"
logger.info(f"Logger initialized ({current_file})")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        logger.info("Initializing MainWindow...")

        self.setWindowTitle("Raw data Candlestick Viewer")
        self.resize(1200, 800)

        self.candle_widget = candlestick_widget.CandlestickWidget()

        central_widget = QWidget()
        layout = QVBoxLayout()
        layout.addWidget(self.candle_widget)
        central_widget.setLayout(layout)

        self.setCentralWidget(central_widget)

        logger.info("MainWindow successfully initialized.")

    def load_and_plot(self, df: pd.DataFrame):
        if df is None or df.empty:
            logger.error("Provided DataFrame is empty or None. Cannot plot candles.")
            return

        logger.info("Loading DataFrame into candlestick widget...")

        try:
            self.candle_widget.plot_candles(df)
            logger.info("Candlestick plot updated successfully.")

        except Exception as e:
            logger.exception(f"Failed to plot candlesticks: {e}")
