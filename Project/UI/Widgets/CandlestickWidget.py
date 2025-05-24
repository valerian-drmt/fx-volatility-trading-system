from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QUrl
import vectorbt as vbt
import pandas as pd
import tempfile
import os

class CandlestickWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.browser = QWebEngineView()
        layout = QVBoxLayout()
        layout.addWidget(self.browser)
        self.setLayout(layout)

    def plot_candles(self, df: pd.DataFrame, datetime_col: str = "Date"):

        df = df.copy()
        df[datetime_col] = pd.to_datetime(df[datetime_col])
        df.set_index(datetime_col, inplace=True)

        # Create Plotly candlestick chart using vectorbt
        fig = vbt.IndicatorFactory.from_pandas(df).plot()

        # Save to temporary HTML file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmpfile:
            fig.write_html(tmpfile.name)
            self.browser.load(QUrl.fromLocalFile(tmpfile.name))

