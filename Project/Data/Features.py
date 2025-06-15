import numpy as np
import pandas as pd
import warnings
from collections import deque

# 🔧 Config import
import os
from Project.Config.LoggerConfig import *
logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")

class Features:
    def __init__(self, data):
        self.data = data

    def resample_with_vwap(self, resample_period: str):
        try:
            logger.info(f"Starting resampling with period: {resample_period}")

            df = self.data

            if not isinstance(df.index, pd.DatetimeIndex):
                raise ValueError("DataFrame index must be a DatetimeIndex for resampling.")

            resampled = pd.DataFrame()
            resampled['Open'] = df['Open'].resample(resample_period).first()
            resampled['High'] = df['High'].resample(resample_period).max()
            resampled['Low'] = df['Low'].resample(resample_period).min()
            resampled['Close'] = df['Close'].resample(resample_period).last()
            resampled['Volume'] = df['Volume'].resample(resample_period).sum()
            resampled['bidSize'] = df['bidSize'].resample(resample_period).sum()
            resampled['askSize'] = df['askSize'].resample(resample_period).sum()

            logger.info("Basic OHLCV resampling done.")

            vwap_numerator = ((df['High'] + df['Low'] + df['Close']) / 3 * df['Volume']).resample(resample_period).sum()
            vwap_denominator = df['Volume'].resample(resample_period).sum()
            resampled['VWAP_5m'] = vwap_numerator / vwap_denominator

            logger.info("VWAP calculation completed.")

            self.data = resampled  # safe overwrite
            logger.info(f"Resampling successful. Resulting shape: {resampled.shape}")
            return self

        except Exception as e:
            logger.error(f"Resampling failed: {e}", exc_info=True)
            raise

    def market_sessions(self):
        try:
            logger.info("Starting market session flag generation.")

            # Make sure datetime index is correct
            self.data.index = pd.to_datetime(self.data.index)
            self.data.index = self.data.index.tz_localize(None)
            logger.info("Datetime index successfully converted and localized.")

            # Functions to check sessions
            def is_london_open(hour, minute):
                return (9 < hour < 17) or (hour == 17 and minute <= 30)

            def is_ny_open(hour, minute):
                return (16 <= hour < 22) or (hour == 15 and minute >= 30) or (hour == 22 and minute == 0)

            def is_tokyo_open(hour, minute):
                return (1 <= hour < 7)

            # Directly create session flags
            self.data['London_Open'] = self.data.index.map(lambda ts: int(is_london_open(ts.hour, ts.minute)))
            self.data['NY_Open'] = self.data.index.map(lambda ts: int(is_ny_open(ts.hour, ts.minute)))
            self.data['HK_Open'] = self.data.index.map(lambda ts: int(is_tokyo_open(ts.hour, ts.minute)))

            logger.info("Market session flags successfully added.")
            return self

        except Exception as e:
            logger.error(f"Error in market_sessions: {e}", exc_info=True)
            raise

    def Pivot_Points(self, pivot_left, pivot_right):
        try:
            logger.info(f"Starting pivot point detection with left={pivot_left}, right={pivot_right}.")

            def clean_deque(i, k, deq, df, key, isHigh):
                if deq and deq[0] == i - k:
                    deq.popleft()
                if isHigh:
                    while deq and df.iloc[i][key] > df.iloc[deq[-1]][key]:
                        deq.pop()
                else:
                    while deq and df.iloc[i][key] < df.iloc[deq[-1]][key]:
                        deq.pop()

            data = self.data[["High", "Low"]].copy()
            data['H'] = False
            data['L'] = False

            win_size = pivot_left + pivot_right + 1
            deqHigh = deque()
            deqLow = deque()

            max_idx = 0
            min_idx = 0
            i = 0
            j = pivot_left
            pivot_low = None
            pivot_high = None

            for index, row in data.iterrows():
                if i < win_size:
                    clean_deque(i, win_size, deqHigh, data, 'High', True)
                    clean_deque(i, win_size, deqLow, data, 'Low', False)
                    deqHigh.append(i)
                    deqLow.append(i)

                    if data.iloc[i]['High'] > data.iloc[max_idx]['High']:
                        max_idx = i
                    if data.iloc[i]['Low'] < data.iloc[min_idx]['Low']:
                        min_idx = i

                    if i == win_size - 1:
                        if data.iloc[max_idx]['High'] == data.iloc[j]['High']:
                            data.at[data.index[j], 'H'] = True
                            pivot_high = data.iloc[j]['High']
                        if data.iloc[min_idx]['Low'] == data.iloc[j]['Low']:
                            data.at[data.index[j], 'L'] = True
                            pivot_low = data.iloc[j]['Low']
                else:
                    j += 1
                    clean_deque(i, win_size, deqHigh, data, 'High', True)
                    clean_deque(i, win_size, deqLow, data, 'Low', False)
                    deqHigh.append(i)
                    deqLow.append(i)

                    if data.iloc[deqHigh[0]]['High'] == data.iloc[j]['High']:
                        data.at[data.index[j], 'H'] = True
                        pivot_high = data.iloc[j]['High']
                    if data.iloc[deqLow[0]]['Low'] == data.iloc[j]['Low']:
                        data.at[data.index[j], 'L'] = True
                        pivot_low = data.iloc[j]['Low']

                data.at[data.index[j], 'Last_High_Value'] = pivot_high
                data.at[data.index[j], 'Last_Low_Value'] = pivot_low
                i += 1

            logger.info("Initial pivot marking complete.")

            # Low pivot calculation
            lows_list = []
            broken_lows = []
            first_value_low = True
            data["Low_Pivot"] = np.nan

            for idx, row in data.iterrows():
                lows_list = [x for x in lows_list if not np.isnan(x)]
                last_low = row['Last_Low_Value']
                low = row['Low']

                if pd.notna(last_low):
                    if first_value_low:
                        lows_list.append(last_low)
                        data.at[idx, "Low_Pivot"] = last_low
                        first_value_low = False
                        continue

                    if not lows_list:
                        if last_low not in broken_lows:
                            lows_list.append(last_low)
                            data.at[idx, "Low_Pivot"] = last_low
                    elif len(lows_list) > 1:
                        if low < lows_list[-1]:
                            broken_lows.append(lows_list.pop())
                        if last_low not in broken_lows:
                            if last_low != lows_list[-1]:
                                lows_list.append(last_low)
                            data.at[idx, "Low_Pivot"] = lows_list[-1]
                        else:
                            data.at[idx, "Low_Pivot"] = lows_list[-1]
                    else:
                        if low < lows_list[-1]:
                            broken_lows.append(lows_list.pop())
                            if last_low not in broken_lows:
                                lows_list.append(last_low)
                                data.at[idx, "Low_Pivot"] = last_low
                            else:
                                data.at[idx, "Low_Pivot"] = None
                        else:
                            if last_low not in broken_lows:
                                if last_low != lows_list[-1]:
                                    lows_list.append(last_low)
                                data.at[idx, "Low_Pivot"] = lows_list[-1]
                            else:
                                data.at[idx, "Low_Pivot"] = lows_list[-1]
                elif not first_value_low:
                    if lows_list:
                        if low < lows_list[-1]:
                            broken_lows.append(lows_list.pop())
                        data.at[idx, "Low_Pivot"] = lows_list[-1] if lows_list else None

                    # -------------------------------
                    # High pivot calculation
                    highs_list = []
                    broken_highs = []
                    first_value_high = True
                    data["High_Pivot"] = np.nan

                    for idx, row in data.iterrows():
                        highs_list = [x for x in highs_list if not np.isnan(x)]
                        last_high = row['Last_High_Value']
                        high = row['High']

                        if pd.notna(last_high):
                            if first_value_high:
                                highs_list.append(last_high)
                                data.at[idx, "High_Pivot"] = last_high
                                first_value_high = False
                                continue

                            if not highs_list:
                                if last_high not in broken_highs:
                                    highs_list.append(last_high)
                                    data.at[idx, "High_Pivot"] = last_high
                            elif len(highs_list) > 1:
                                if high > highs_list[-1]:
                                    broken_highs.append(highs_list.pop())
                                if last_high not in broken_highs:
                                    if last_high != highs_list[-1]:
                                        highs_list.append(last_high)
                                    data.at[idx, "High_Pivot"] = highs_list[-1]
                                else:
                                    data.at[idx, "High_Pivot"] = highs_list[-1]
                            else:
                                if high > highs_list[-1]:
                                    broken_highs.append(highs_list.pop())
                                    if last_high not in broken_highs:
                                        highs_list.append(last_high)
                                        data.at[idx, "High_Pivot"] = last_high
                                    else:
                                        data.at[idx, "High_Pivot"] = None
                                else:
                                    if last_high not in broken_highs:
                                        if last_high != highs_list[-1]:
                                            highs_list.append(last_high)
                                        data.at[idx, "High_Pivot"] = highs_list[-1]
                                    else:
                                        data.at[idx, "High_Pivot"] = highs_list[-1]
                        elif not first_value_high:
                            if highs_list:
                                if high > highs_list[-1]:
                                    broken_highs.append(highs_list.pop())
                                data.at[idx, "High_Pivot"] = highs_list[-1] if highs_list else None

            logger.info("Pivot filtering logic applied (highs/lows lists, broken levels).")

            colonnes_a_supprimer = ['Last_High_Value', 'Last_Low_Value']
            data = data.drop(colonnes_a_supprimer, axis=1)

            self.data["Dif_Low_Pivot"] = data["Low_Pivot"] - data["Low"]
            self.data["Dif_High_Pivot"] = data["High_Pivot"] - data["High"]
            self.data["Low_Pivot"] = data["Low_Pivot"]
            self.data["High_Pivot"] = data["High_Pivot"]

            logger.info("Pivot point detection completed successfully.")
            return self

        except Exception as e:
            logger.error(f"Error in Pivot_Points: {e}", exc_info=True)
            raise

    def Volume_Pivot_Points(self, duration_min: int, n_cross: int, std_factor: float):
        try:
            logger.info(f"Starting Volume_Pivot_Points with duration_min={duration_min}, "
                        f"n_cross={n_cross}, std_factor={std_factor}")

            df = self.data.copy()
            num_bars = duration_min // 5

            typical_price = (df['High'] + df['Low'] + df['Close']) / 3
            rolling_num = (typical_price * df['Volume']).rolling(window=num_bars, min_periods=1).sum()
            rolling_den = df['Volume'].rolling(window=num_bars, min_periods=1).sum()

            vwap_col = f'Rolling_VWAP_{duration_min}min'
            df[vwap_col] = np.where(rolling_den != 0, rolling_num / rolling_den, np.nan)
            df[f'vitesse_{duration_min}min'] = df[vwap_col].diff()

            vitesse_col = f'vitesse_{duration_min}min'
            std = df[vitesse_col].std()
            mean = df[vitesse_col].mean()

            logger.info("VWAP and speed computed.")

            # UP crosses
            condition_up = df[vitesse_col] > mean + std_factor * std
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                candidate_up = df.index[condition_up.shift(1).fillna(False).infer_objects(copy=False)]

            filtered_up = []
            for idx in candidate_up:
                if filtered_up and (df.index.get_loc(idx) - df.index.get_loc(filtered_up[-1]) <= n_cross):
                    continue
                filtered_up.append(idx)

            df['vwap_cross_up'] = False
            df.loc[filtered_up, 'vwap_cross_up'] = True

            # DOWN crosses
            condition_down = df[vitesse_col] < mean - std_factor * std
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                candidate_down = df.index[condition_down.shift(1).fillna(False).infer_objects(copy=False)]

            filtered_down = []
            for idx in candidate_down:
                if filtered_down and (df.index.get_loc(idx) - df.index.get_loc(filtered_down[-1]) <= n_cross):
                    continue
                filtered_down.append(idx)

            df['vwap_cross_down'] = False
            df.loc[filtered_down, 'vwap_cross_down'] = True

            logger.info(f"Detected {len(filtered_up)} upward and {len(filtered_down)} downward VWAP crosses.")

            # Correction DOWN->UP and UP->DOWN
            down_to_up = df.index[df['vwap_cross_down'] & (df["VWAP_5m"] > df['Rolling_VWAP_240min'])]
            df.loc[down_to_up, 'vwap_cross_down'] = False
            df.loc[down_to_up, 'vwap_cross_up'] = True

            up_to_down = df.index[df['vwap_cross_up'] & (df["VWAP_5m"] < df['Rolling_VWAP_240min'])]
            df.loc[up_to_down, 'vwap_cross_up'] = False
            df.loc[up_to_down, 'vwap_cross_down'] = True

            # Rolling VWAP High/Low
            df['Rolling_Vwap_High'] = df[vwap_col].where(df['vwap_cross_up']).ffill()
            df['Rolling_Vwap_Low'] = df[vwap_col].where(df['vwap_cross_down']).ffill()

            df['plot_up_cross_y'] = df[vwap_col].where(df['vwap_cross_up'])
            df['plot_down_cross_y'] = df[vwap_col].where(df['vwap_cross_down'])

            # Last Cross Prices
            df['last_volume_low_price'] = np.nan
            df['last_volume_high_price'] = np.nan
            last_down_price = np.nan
            last_up_price = np.nan

            for index, row in df.iterrows():
                if row['vwap_cross_down']:
                    last_down_price = row[vwap_col]
                if row['vwap_cross_up']:
                    last_up_price = row[vwap_col]
                df.loc[index, 'last_volume_high_price'] = last_down_price
                df.loc[index, 'last_volume_low_price'] = last_up_price

            logger.info("Calculated rolling pivot boundaries and last cross prices.")

            # -------------------------------
            # Low pivot calculation
            lows_list = []
            broken_lows = []
            first_value_low = True
            df["Volume_Low_Pivot"] = np.nan

            for idx, row in df.iterrows():
                lows_list = [x for x in lows_list if not np.isnan(x)]
                last_low = row['last_volume_low_price']
                low = row['VWAP_5m']

                if pd.notna(last_low):
                    if first_value_low:
                        lows_list.append(last_low)
                        df.at[idx, "Volume_Low_Pivot"] = last_low
                        first_value_low = False
                        continue

                    if not lows_list:
                        if last_low not in broken_lows:
                            lows_list.append(last_low)
                            df.at[idx, "Volume_Low_Pivot"] = last_low
                    elif len(lows_list) > 1:
                        if low < lows_list[-1]:
                            broken_lows.append(lows_list.pop())
                        if last_low not in broken_lows:
                            if last_low != lows_list[-1]:
                                lows_list.append(last_low)
                            df.at[idx, "Volume_Low_Pivot"] = lows_list[-1]
                        else:
                            df.at[idx, "Volume_Low_Pivot"] = lows_list[-1]
                    else:
                        if low < lows_list[-1]:
                            broken_lows.append(lows_list.pop())
                            if last_low not in broken_lows:
                                lows_list.append(last_low)
                                df.at[idx, "Volume_Low_Pivot"] = last_low
                            else:
                                df.at[idx, "Volume_Low_Pivot"] = None
                        else:
                            if last_low not in broken_lows:
                                if last_low != lows_list[-1]:
                                    lows_list.append(last_low)
                                df.at[idx, "Volume_Low_Pivot"] = lows_list[-1]
                            else:
                                df.at[idx, "Volume_Low_Pivot"] = lows_list[-1]
                elif not first_value_low:
                    if lows_list:
                        if low < lows_list[-1]:
                            broken_lows.append(lows_list.pop())
                        df.at[idx, "Volume_Low_Pivot"] = lows_list[-1] if lows_list else None

            # -------------------------------
            # High pivot calculation
            highs_list = []
            broken_highs = []
            first_value_high = True
            df["Volume_High_Pivot"] = np.nan

            for idx, row in df.iterrows():
                highs_list = [x for x in highs_list if not np.isnan(x)]
                last_high = row['last_volume_high_price']
                high = row['VWAP_5m']

                if pd.notna(last_high):
                    if first_value_high:
                        highs_list.append(last_high)
                        df.at[idx, "Volume_High_Pivot"] = last_high
                        first_value_high = False
                        continue

                    if not highs_list:
                        if last_high not in broken_highs:
                            highs_list.append(last_high)
                            df.at[idx, "Volume_High_Pivot"] = last_high
                    elif len(highs_list) > 1:
                        if high > highs_list[-1]:
                            broken_highs.append(highs_list.pop())
                        if last_high not in broken_highs:
                            if last_high != highs_list[-1]:
                                highs_list.append(last_high)
                            df.at[idx, "Volume_High_Pivot"] = highs_list[-1]
                        else:
                            df.at[idx, "Volume_High_Pivot"] = highs_list[-1]
                    else:
                        if high > highs_list[-1]:
                            broken_highs.append(highs_list.pop())
                            if last_high not in broken_highs:
                                highs_list.append(last_high)
                                df.at[idx, "Volume_High_Pivot"] = last_high
                            else:
                                df.at[idx, "Volume_High_Pivot"] = None
                        else:
                            if last_high not in broken_highs:
                                if last_high != highs_list[-1]:
                                    highs_list.append(last_high)
                                df.at[idx, "Volume_High_Pivot"] = highs_list[-1]
                            else:
                                df.at[idx, "Volume_High_Pivot"] = highs_list[-1]
                elif not first_value_high:
                    if highs_list:
                        if high > highs_list[-1]:
                            broken_highs.append(highs_list.pop())
                        df.at[idx, "Volume_High_Pivot"] = highs_list[-1] if highs_list else None

            # Final cleanup and save to self.data
            colonnes_a_supprimer = ['vitesse_240min', 'vwap_cross_up', 'vwap_cross_down',
                                    'Rolling_Vwap_High', 'Rolling_Vwap_Low',
                                    'plot_up_cross_y', 'plot_down_cross_y',
                                    'last_volume_low_price', 'last_volume_high_price']
            df = df.drop(colonnes_a_supprimer, axis=1)

            df["Dif_Volume_Low_Pivot"] = df["VWAP_5m"] - df["Volume_Low_Pivot"]
            df["Dif_Volume_High_Pivot"] = df["Volume_High_Pivot"] - df["VWAP_5m"]

            self.data = df.copy()
            logger.info("Volume pivot point detection completed successfully.")

            return self

        except Exception as e:
            logger.error(f"Error in Volume_Pivot_Points: {e}", exc_info=True)
            raise

    def add_volume_delta(self):
        try:
            logger.info("Adding 'volume_delta' column...")
            self.data['volume_delta'] = self.data['bidSize'] - self.data['askSize']
            logger.info("'volume_delta' column added.")
        except Exception as e:
            logger.exception("Error while adding 'volume_delta'")
        return self

    def add_cvd(self):
        try:
            logger.info("Adding 'CVD' column...")
            if 'volume_delta' not in self.data.columns:
                logger.info("'volume_delta' not found, calling add_volume_delta()...")
                self.add_volume_delta()
            self.data['CVD'] = self.data['volume_delta'].cumsum()
            logger.info("'CVD' column added.")
        except Exception as e:
            logger.exception("Error while adding 'CVD'")
        return self

    def add_obi(self):
        try:
            logger.info("Adding 'obi' column...")
            self.data['obi'] = (self.data['bidSize'] - self.data['askSize']) / (
                    self.data['bidSize'] + self.data['askSize'] + 1e-9)
            logger.info("'obi' column added.")
        except Exception as e:
            logger.exception("Error while adding 'obi'")
        return self

    def add_price_change(self):
        try:
            logger.info("Adding 'price_change' column...")
            self.data['price_change'] = self.data['Close'].diff()
            logger.info("'price_change' column added.")
        except Exception as e:
            logger.exception("Error while adding 'price_change'")
        return self

    def add_reaction_ratio(self, epsilon=1e-6):
        try:
            logger.info("Adding 'reaction_ratio' column...")
            if 'price_change' not in self.data.columns:
                logger.info("'price_change' not found, calling add_price_change()...")
                self.add_price_change()
            if 'CVD' not in self.data.columns:
                logger.info("'CVD' not found, calling add_cvd()...")
                self.add_cvd()
            self.data['reaction_ratio'] = self.data['price_change'] / (self.data['CVD'] + epsilon)
            logger.info("'reaction_ratio' column added.")
        except Exception as e:
            logger.exception("Error while adding 'reaction_ratio'")
        return self

    def add_rolling_std_price(self, std_window):
        try:
            logger.info(f"Adding 'rolling_std_price' column with window={std_window}...")
            self.data['rolling_std_price'] = self.data['Close'].rolling(window=std_window).std()
            logger.info("'rolling_std_price' column added.")
        except Exception as e:
            logger.exception("Error while adding 'rolling_std_price'")
        return self

    def add_rolling_mean_cvd(self, mean_window):
        try:
            logger.info(f"Adding 'rolling_mean_cvd' column with window={mean_window}...")
            if 'CVD' not in self.data.columns:
                logger.info("'CVD' not found, calling add_cvd()...")
                self.add_cvd()
            self.data['rolling_mean_cvd'] = self.data['CVD'].rolling(window=mean_window).mean()
            logger.info("'rolling_mean_cvd' column added.")
        except Exception as e:
            logger.exception("Error while adding 'rolling_mean_cvd'")
        return self


