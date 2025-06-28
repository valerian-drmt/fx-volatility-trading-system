from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout
from Class.UI.Widgets.CandlestickWidget import CandlestickWidget
import os
import sys
import pandas as pd

# Set up project root in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(project_root)

# Internal imports
from Class.Config import colored_logger

logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        logger.info("Initializing MainWindow...")

        self.setWindowTitle("Raw Data Candlestick Viewer")
        self.resize(1200, 800)

        self.candle_widget = CandlestickWidget()

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
