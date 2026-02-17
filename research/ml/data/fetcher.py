from datetime import datetime
import ccxt
import pandas as pd
from io import BytesIO
import requests
import gzip

# 🔧 config import
import os
from core.logging.logger_config import colored_logger
logger = colored_logger()
current_file = os.path.basename(__file__) if '__file__' in globals() else "Notebook"
logger.info(f"Logger initialized ({current_file})")

class DataFetcher:
    def __init__(self, symbol, start_date, end_date, interval):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.interval = interval
        self.raw_data = None

    def get_binance_data_ohlcv(self):
        binance = ccxt.binance({
            'options': {'defaultType': 'future'},
            'enableRateLimit': True
        })

        logger.info(
            f"Starting data fetch for {self.symbol} from {self.start_date} to {self.end_date} with interval {self.interval}")

        start = datetime.strptime(self.start_date, "%d-%m-%Y")
        end = datetime.strptime(self.end_date, "%d-%m-%Y")
        since = int(start.timestamp() * 1000)
        end_timestamp = int(end.timestamp() * 1000)

        interval_to_milliseconds = {
            '1m': 60 * 1000,
            '5m': 5 * 60 * 1000,
            '15m': 15 * 60 * 1000,
            '1h': 60 * 60 * 1000,
            '4h': 4 * 60 * 60 * 1000,
            '1d': 24 * 60 * 60 * 1000
        }

        ms_per_candle = interval_to_milliseconds[self.interval]
        limit = 1000
        all_data = []
        request_count = 0

        while since < end_timestamp:
            try:
                ohlcv = binance.fetch_ohlcv(
                    symbol=self.symbol,
                    timeframe=self.interval,
                    since=since,
                    limit=limit
                )
                request_count += 1

                if not ohlcv:
                    logger.info("No more data returned by Binance.")
                    break

                all_data.extend(ohlcv)
                last_timestamp = ohlcv[-1][0]

                if last_timestamp == since:
                    since += ms_per_candle
                else:
                    since = last_timestamp + ms_per_candle

            except Exception as e:
                logger.error(f"Error fetching data from Binance: {e}")
                break

        if all_data:
            df = pd.DataFrame(all_data, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df = df.drop_duplicates(subset='time').reset_index(drop=True)
            self.raw_data = df
            logger.info(f"Successfully fetched {len(df)} candles in total.")
        else:
            self.raw_data = pd.DataFrame(columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            logger.warning("No data was fetched — the resulting DataFrame is empty.")

        return self

    def get_bybit_data_dom(self):

        base_url = f"https://public.bybit.com/spot/{self.symbol}/"
        dates = pd.date_range(start=self.start_date, end=self.end_date, freq='D').strftime('%Y-%m-%d')
        dfs = []

        for date in dates:
            fname = f"{self.symbol}_{date}.csv.gz"
            url = base_url + fname
            logger.info(f"Trying: {url}")
            response = requests.get(url)
            if response.status_code == 200:
                logger.info(f"Downloaded: {fname}")
                with gzip.GzipFile(fileobj=BytesIO(response.content)) as gz:
                    temp_df = pd.read_csv(gz)
                    temp_df['time'] = pd.to_datetime(temp_df['timestamp'], unit='ms')
                    dfs.append(temp_df)
            else:
                logger.warning(f"File not found or not available for: {date}")

        if dfs:
            full_df = pd.concat(dfs, ignore_index=True)
            drop_cols = [col for col in ['timestamp', 'rpi', 'id'] if col in full_df.columns]
            full_df = full_df.drop(columns=drop_cols)
            #full_df = full_df.set_index('time')
            logger.info(f"Shape: {full_df.shape}")
            self.raw_data = full_df
            return self
        else:
            logger.error("No data files were found or loaded.")
            return None

    def save_to_csv_ohlcv(self, directory="./"):
        if self.raw_data is None or self.raw_data.empty:
            raise ValueError("raw_data is empty. Please fetch data first.")

        filename = f"{self.symbol}_{self.start_date}_{self.end_date}_{self.interval.replace(' ', '')}_ohlcv.csv"
        path = f"{directory.rstrip('/')}/{filename}"
        self.raw_data.to_csv(path, index=False)
        logger.info(f"📁 Data saved to: {path}")

    def save_to_csv_dom(self, directory="./"):
        if self.raw_data is None or self.raw_data.empty:
            raise ValueError("raw_data is empty. Please fetch data first.")

        filename = f"{self.symbol}_{self.start_date}_{self.end_date}_{self.interval.replace(' ', '')}_dom.csv"
        path = f"{directory.rstrip('/')}/{filename}"
        self.raw_data.to_csv(path, index=False)
        logger.info(f"📁 Data saved to: {path}")

    def load_from_csv_ohlcv_binance(self, directory="./"):

        filename = f"{self.symbol}_{self.start_date}_{self.end_date}_{self.interval.replace(' ', '')}_ohlcv.csv"
        path = f"{directory.rstrip('/')}/{filename}"

        if not os.path.exists(path):
            logger.error(f"CSV file not found: {path}")
            return

        try:
            df_raw = pd.read_csv(path, low_memory=False)
            expected_cols = ['time', 'open', 'high', 'low', 'close', 'volume']

            missing = [col for col in expected_cols if col not in df_raw.columns]
            if missing:
                logger.error(f"Missing columns in CSV: {missing}")
                return

            df = df_raw[expected_cols].copy()
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            df = df[df['time'].notna()]
            df = df[df['time'].apply(lambda x: isinstance(x, pd.Timestamp))]

            if df.empty:
                logger.error("Loaded CSV file is empty after cleaning.")
                return

            self.raw_data = df
            logger.info(f"📥 Data loaded from: {path}")
        except Exception as e:
            logger.exception(f"Failed to load CSV: {e}")
            
    def load_from_csv_dom_bybit(self, directory="./"):

        filename = f"{self.symbol}_{self.start_date}_{self.end_date}_{self.interval.replace(' ', '')}_dom.csv"
        path = f"{directory.rstrip('/')}/{filename}"

        if not os.path.exists(path):
            logger.error(f"CSV file not found: {path}")
            return

        try:
            df_raw = pd.read_csv(path, low_memory=False)
            expected_cols = ['time', 'open', 'high', 'low', 'close','ask_size','bid_size']

            missing = [col for col in expected_cols if col not in df_raw.columns]
            if missing:
                logger.error(f"Missing columns in CSV: {missing}")
                return

            df = df_raw[expected_cols].copy()
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            df = df[df['time'].notna()]
            df = df[df['time'].apply(lambda x: isinstance(x, pd.Timestamp))]

            if df.empty:
                logger.error("Loaded CSV file is empty after cleaning.")
                return

            self.raw_data = df
            logger.info(f"📥 Data loaded from: {path}")
        except Exception as e:
            logger.exception(f"Failed to load CSV: {e}")
            
    def load_from_csv_ib(self, directory="./"):
        required_attrs = [self.symbol, self.start_date, self.end_date, self.interval]
        if any(attr is None for attr in required_attrs):
            logger.error("Missing metadata attributes to build the CSV filename.")
            return

        filename = f"{self.symbol}_{self.start_date}_{self.end_date}_{self.interval.replace(' ', '')}.csv"
        path = f"{directory.rstrip('/')}/{filename}"

        if not os.path.exists(path):
            logger.error(f"CSV file not found: {path}")
            return

        try:
            df_raw = pd.read_csv(path, low_memory=False)
            expected_cols = ['time', 'bid', 'ask', 'bidSize', 'askSize']

            missing = [col for col in expected_cols if col not in df_raw.columns]
            if missing:
                logger.error(f"Missing columns in CSV: {missing}")
                return

            if df_raw.empty:
                logger.error("Loaded CSV file is empty after cleaning.")
                return

            df_raw.set_index('time', inplace=True)
            self.raw_data = df_raw.dropna()
            logger.info(f"📥 Data loaded from: {path}")
        except Exception as e:
            logger.exception(f"Failed to load CSV: {e}")

    def resample_to_1m_ohlcv_ib(self):
        df = self.raw_data.copy()
        logger.debug("Starting resample_to_1m...")

        # Convert index to datetime if it's not already
        if not isinstance(df.index, pd.DatetimeIndex):
            logger.debug("Index is not a DatetimeIndex. Attempting to convert...")
            try:
                df.index = pd.to_datetime(df.index)
                logger.debug("Index successfully converted to DatetimeIndex.")
            except Exception as e:
                logger.error(f"Failed to convert index: {e}")
                raise TypeError("Index could not be converted to DatetimeIndex.")

        # Compute mid price
        df['mid'] = (df['bid'] + df['ask']) / 2
        logger.debug("Mid price computed.")

        # Resample mid price to 1-minute OHLC
        ohlc = df['mid'].resample('1min').ohlc()
        logger.debug("Resampled mid prices to 1-minute OHLC.")

        # Sum bidSize and askSize per minute
        bid_size = df['bidSize'].resample('1min').sum() / 1000
        ask_size = df['askSize'].resample('1min').sum() / 1000
        logger.debug("Resampled bidSize and askSize to 1-minute sums (in thousands).")

        # Total volume
        volume = bid_size + ask_size
        logger.debug("Volume computed as sum of bidSize and askSize.")

        # Combine all into a final DataFrame
        result = pd.concat([ohlc, volume, bid_size, ask_size], axis=1)
        result.columns = ['Open', 'High', 'Low', 'Close', 'Volume', 'bidSize', 'askSize']
        logger.debug("Concatenated OHLC and volume columns.")

        result.dropna(inplace=True)
        logger.info(f"Resampling complete. Final shape: {result.shape}")

        self.raw_data = result
        return self

    def dom_to_ohlcv(self):
        # Ensure 'time' is datetime
        if not pd.api.types.is_datetime64_any_dtype(self.raw_data['time']):
            self.raw_data['time'] = pd.to_datetime(self.raw_data['time'])
        # Set 'time' as index if not already
        if 'time' in self.raw_data.columns:
            self.raw_data.set_index('time', inplace=True)

        # Resample price to OHLC
        ohlc = self.raw_data['price'].resample(self.interval).ohlc()
        # Resample volume for buy and sell sides
        bid_size = self.raw_data[self.raw_data['side'] == 'buy']['volume'].resample(self.interval).sum().rename(
            'bid_size')
        ask_size = self.raw_data[self.raw_data['side'] == 'sell']['volume'].resample(self.interval).sum().rename(
            'ask_size')
        # Merge
        result = pd.concat([ohlc, ask_size, bid_size], axis=1).fillna(0).reset_index()
        self.raw_data = result
        return self
















