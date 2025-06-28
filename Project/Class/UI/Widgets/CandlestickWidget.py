from PySide6.QtWidgets import QWidget, QVBoxLayout
from lightweight_charts.widgets import QtChart
import pandas as pd
import os
import sys
# Set up project root in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(project_root)

# Imports internes
from Class.Config import colored_logger

logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")

class CandlestickWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.chart = QtChart(self)
        layout = QVBoxLayout()
        layout.addWidget(self.chart.get_webview())
        self.setLayout(layout)

    def plot_candles(self, df: pd.DataFrame, datetime_col: str = 'timestamp'):
        if df.empty:
            logger.error("The DataFrame is empty.")
            return

        if datetime_col not in df.columns:
            logger.error(f"Missing required datetime column: '{datetime_col}'")
            return

        logger.info("Starting candle plot preparation.")

        df = df.copy()
        df[datetime_col] = pd.to_datetime(df[datetime_col])
        df.sort_values(by=datetime_col, inplace=True)
        df.rename(columns={datetime_col: 'time'}, inplace=True)
        df['time'] = df['time'].dt.strftime('%Y-%m-%dT%H:%M:%S')

        logger.info("DataFrame prepared successfully. Sending to chart.")
        self.chart.set(df)
        logger.info("Candlestick chart updated.")
