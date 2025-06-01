from datetime import datetime
import ccxt
import pandas as pd
import time

# 🔧 Config import
import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(project_root)
from Config.LoggerConfig import colored_logger
logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")

class DataFetcher:
    def __init__(self, symbol, start_date, end_date, interval):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.interval = interval
        self.raw_data = None

    def get_binance_data(self):
        binance = ccxt.binance({
            'options': {'defaultType': 'future'},
            'enableRateLimit': True
        })

        binance_symbol = f"{self.symbol}"

        # Vérifier l'intervalle
        interval_map_ms = {
            "1m": 60 * 1000,
            "3m": 3 * 60 * 1000,
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "30m": 30 * 60 * 1000,
            "1h": 60 * 60 * 1000
        }

        if self.interval not in interval_map_ms:
            raise ValueError(f"Interval '{self.interval}' not supported. Choose from {list(interval_map_ms.keys())}")

        start = datetime.strptime(self.start_date, "%d-%m-%Y")
        end = datetime.strptime(self.end_date, "%d-%m-%Y")
        since = int(start.timestamp() * 1000)
        end_ts = int(end.timestamp() * 1000)
        interval_ms = interval_map_ms[self.interval]
        limit = 1000

        all_data = []
        logger.info(f"Fetching {self.interval} Binance Futures data for {self.symbol}")

        while since < end_ts:
            try:
                ohlcv = binance.fetch_ohlcv(
                    symbol=binance_symbol,
                    timeframe=self.interval,
                    since=since,
                    limit=limit
                )
                if not ohlcv:
                    logger.warning("No more data returned by Binance.")
                    break

                all_data.extend(ohlcv)
                since = ohlcv[-1][0] + interval_ms
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"Binance API error: {e}")
                break

        if not all_data:
            logger.warning("No data collected from Binance.")
            self.raw_data = pd.DataFrame()
            return self.raw_data

        df = pd.DataFrame(all_data, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
        df = df[df['Timestamp'] <= pd.to_datetime(end)]

        self.raw_data = df
        logger.info(f"✅ Data fetch completed: {len(df)} rows.")
        return self.raw_data

    def save_to_csv(self, directory="./"):
        if self.raw_data is None or self.raw_data.empty:
            raise ValueError("raw_data is empty. Please fetch data first.")

        filename = f"{self.symbol}_{self.start_date}_{self.end_date}_{self.interval.replace(' ', '')}.csv"
        path = f"{directory.rstrip('/')}/{filename}"
        self.raw_data.to_csv(path, index=False)
        logger.info(f"📁 Data saved to: {path}")

    def load_from_csv(self, directory="./"):
        required_attrs = [self.symbol, self.start_date, self.end_date, self.interval]
        if any(attr is None for attr in required_attrs):
            logger.error("Missing metadata attributes to build the CSV filename.")
            return

        filename = f"{self.symbol}_{self.start_date}_{self.end_date}_{self.interval.replace(' ', '')}.csv"
        path = f"{directory.rstrip('/')}/{filename}"

        if not os.path.exists(path):
            logger.error(f"CSV file not found: {path}")
            return

        df_raw = pd.read_csv(path)
        df = pd.DataFrame(df_raw, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Timestamp'] = pd.to_datetime(df['Timestamp'])
        df.set_index('Timestamp', inplace=True)

        if df.empty:
            logger.error("Loaded CSV file is empty.")
            return

        self.raw_data = df
        logger.info(f"📥 Data loaded from: {path}")














