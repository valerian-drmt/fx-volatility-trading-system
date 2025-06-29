from PySide6.QtWidgets import QWidget, QVBoxLayout
from lightweight_charts.widgets import QtChart
import pandas as pd

# 🔧 config import
import os
from core.config.logger_config import colored_logger
logger = colored_logger()
current_file = os.path.basename(__file__) if '__file__' in globals() else "Notebook"
logger.info(f"Logger initialized ({current_file})")

class CandlestickWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.chart = QtChart(self)
        self.layout.addWidget(self.chart.get_webview())

    def plot_candles(self, df: pd.DataFrame):
        """
        Expects df with columns: Open, High, Low, Close, Volume,
        and index named 'Timestamp' (datetime or comparable).
        """
        if df is None or df.empty:
            raise ValueError("Empty DataFrame passed to plot_candles")

        # Prepare DataFrame for QtChart: ensure correct column names and a 'time' column
        df = df.copy()
        df['time'] = df.index  # add time column from index
        df = df.rename(columns={
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        })

        # Select the required columns, in order
        df_plot = df[['time', 'open', 'high', 'low', 'close', 'volume']]

        # Pass the DataFrame to the chart
        self.chart.set(df_plot)
