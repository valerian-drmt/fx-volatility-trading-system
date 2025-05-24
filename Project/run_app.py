# 🔧 Config import
import logging
import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(project_root)
from Config.LoggerConfig import colored_logger
logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")

# 📊 Data import
from Data.Data_Fetcher import DataFetcher

def main():

    symbol = "BTC"
    currency = "USD"
    start_date = "01-01-2025"
    end_date = "24-04-2025"
    interval = "1m"

    try:
        fetcher = DataFetcher(symbol, currency, start_date, end_date, interval)
        #fetcher.get_binance_data()
        #fetcher.save_to_csv(directory="./Data")
        fetcher.load_from_csv(directory="./Data")
        print(fetcher.raw_data.head())
        logger.info(f"Excel Size: {fetcher.raw_data.shape}")
        logger.info("✅ Pipeline completed successfully.")

    except Exception as e:
        logger.error(f"❌ Pipeline failed: {e}")




















if __name__ == "__main__":
    main()
