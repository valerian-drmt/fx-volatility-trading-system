import pandas as pd

# 🔧 Config import
import os

logger = colored_logger()
current_file = os.path.basename(__file__)
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

                current_volume_low_pivot = self.data.iloc[i]['Volume_Low_Pivot']
                current_volume_high_pivot = self.data.iloc[i]['Volume_High_Pivot']

                low_condition = (future_data['VWAP_5m'] < current_volume_low_pivot).any()
                high_condition = (future_data['VWAP_5m'] > current_volume_high_pivot).any()

                vw_below.append(int(low_condition))
                vw_above.append(int(high_condition))

            self.data['VWAP_Below_Volume_Low'] = vw_below
            self.data['VWAP_Above_Volume_High'] = vw_above

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

                current_low_pivot = self.data.iloc[i]['Low_Pivot']
                current_high_pivot = self.data.iloc[i]['High_Pivot']

                low_condition = (future_data['Low'] < current_low_pivot).any()
                high_condition = (future_data['High'] > current_high_pivot).any()

                low_below.append(int(low_condition))
                high_above.append(int(high_condition))

            self.data['Low_Below_Pivot'] = low_below
            self.data['High_Above_Pivot'] = high_above

            logger.info("Categorize_Pivot_Points completed successfully.")
            return self

        except Exception as e:
            logger.error(f"Error in Categorize_Pivot_Points: {e}", exc_info=True)
            raise