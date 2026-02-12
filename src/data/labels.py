import pandas as pd

# 🔧 config import
import os
from core.logging.logger_config import colored_logger
logger = colored_logger()
current_file = os.path.basename(__file__) if '__file__' in globals() else "Notebook"
logger.info(f"Logger initialized ({current_file})")

class Labels:
    def __init__(self, data : pd.DataFrame):
        self.data = data

    def categorize_colume_pivot_points(self, look_forward: int):
        try:
            logger.info(f"Starting Categorize_Volume_Pivot_Points with look_forward={look_forward}")

            vw_below = []
            vw_above = []

            for i in range(len(self.data)):
                if i + 1 + look_forward > len(self.data):
                    vw_below.append(0)
                    vw_above.append(0)
                    continue

                future_data = self.data.iloc[i + 1:i + 1 + look_forward]

                current_volume_low_pivot = self.data.iloc[i]['volume_low_Pivot']
                current_volume_high_pivot = self.data.iloc[i]['volume_high_Pivot']

                low_condition = (future_data['VWAP_5m'] < current_volume_low_pivot).any()
                high_condition = (future_data['VWAP_5m'] > current_volume_high_pivot).any()

                vw_below.append(int(low_condition))
                vw_above.append(int(high_condition))

            self.data['vwap_below_volume_low'] = vw_below
            self.data['vwap_above_volume_high'] = vw_above

            logger.info("Categorize_Volume_Pivot_Points completed successfully.")
            return self

        except Exception as e:
            logger.error(f"Error in Categorize_Volume_Pivot_Points: {e}", exc_info=True)
            raise

    def categorize_pivot_points(self, look_forward: int):
        try:
            logger.info(f"Starting Categorize_Pivot_Points with look_forward={look_forward}")

            low_below = []
            high_above = []

            for i in range(len(self.data)):
                if i + 1 + look_forward > len(self.data):
                    low_below.append(0)
                    high_above.append(0)
                    continue

                future_data: pd.DataFrame = self.data.iloc[i + 1:i + 1 + look_forward]

                current_low_pivot = self.data.iloc[i]['low_pivot']
                current_high_pivot = self.data.iloc[i]['high_pivot']

                low_condition = (future_data['low'] < current_low_pivot).any()
                high_condition = (future_data['high'] > current_high_pivot).any()

                low_below.append(int(low_condition))
                high_above.append(int(high_condition))

            self.data['low_below_pivot'] = low_below
            self.data['high_above_pivot'] = high_above

            logger.info("Categorize_Pivot_Points completed successfully.")
            return self

        except Exception as e:
            logger.error(f"Error in Categorize_Pivot_Points: {e}", exc_info=True)
            raise
