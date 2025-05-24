# 🔧 Config import
import logging
import sys
import os
from PySide6.QtWidgets import QApplication
import pandas as pd

# Set up project root in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(project_root)

# Imports internes
from Config.LoggerConfig import colored_logger
from Data.Data_Fetcher import DataFetcher
from UI.MainWindow import MainWindow

logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")

def main():
    # 📊 Data parameters
    symbol = "BTC"
    currency = "USD"
    start_date = "01-01-2025"
    end_date = "24-04-2025"
    interval = "1m"

    # -----------------------------------------------------------------
    # 📥 Load data
    try:
        logger.info(f"Initializing DataFetcher for {symbol}/{currency} from {start_date} to {end_date} at {interval} interval.")
        fetcher = DataFetcher(symbol, currency, start_date, end_date, interval)
        fetcher.load_from_csv(directory="./Data")
        logger.info(f"✅ Data successfully loaded. Shape: {fetcher.raw_data.shape}")
        print(fetcher.raw_data.head())
    except Exception as e:
        logger.error(f"❌ Failed to load data: {e}")
        return

    # -----------------------------------------------------------------
    # 🖥️ Start application
    try:
        logger.info("Starting QApplication...")
        app = QApplication(sys.argv)

        window = MainWindow()
        logger.info("MainWindow initialized.")

        df = fetcher.raw_data.copy()
        logger.info("Raw data copied for plotting.")

        if 'timestamp' not in df.columns:
            logger.error("❌ 'timestamp' column missing from DataFrame.")
            sys.exit(1)

        df['timestamp'] = pd.to_datetime(df['timestamp'])
        logger.info("✅ 'timestamp' column converted to datetime format.")

        window.load_and_plot(df)
        logger.info("Candlestick plot loaded into MainWindow.")

        window.show()
        logger.info("MainWindow shown. Entering application loop.")
        sys.exit(app.exec())

    except Exception as e:
        logger.exception(f"❌ Application startup failed: {e}")
        sys.exit(1)



if __name__ == "__main__":
    main()
