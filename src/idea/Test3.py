from lightweight_charts import Chart
import pandas as pd
import os

# Read the data
csv_path = "BTCUSDT_23-06-2025_27-06-2025_1m_ohlcv.csv"
if not os.path.exists(csv_path):
    raise FileNotFoundError(f"CSV file not found: {csv_path}")

df = pd.read_csv(csv_path)
print(df)

# Calculate MA20 and add it to the DataFrame
df['MA20'] = df['close'].rolling(20).mean()

try:
    chart.exit()
except Exception:
    pass

# Create chart with title including symbol and interval, and launch maximized
chart = Chart(
    title="BTCUSDT – 1m",
    toolbox=True,
    maximize=True
)

# Plot candlestick + volume
chart.set(df[['time', 'open', 'high', 'low', 'close', 'volume']])

# Overlay MA20 indicator
ma_line = chart.create_line(
    name="MA20",  # must match DataFrame column name
    color="#FF9900",
    style="solid",
    width=2,
    price_line=False,
    price_label=True
)
ma_line.set(df[['time', 'MA20']].dropna())

# Toggle MA20 line
def on_toggle(chart_obj):
    btn = chart_obj.topbar['ma20_toggle']
    if btn.value == "on":
        ma_line.show_data()
    else:
        ma_line.hide_data()
    chart_obj.fit()

# Add toggle button (toggle=True enables state)
chart.topbar.button(
    name="ma20_toggle",
    button_text="MA20",
    toggle=True,
    separator=False,
    align="right",
    func=on_toggle
)

# Display chart without blocking
chart.show(block=False)
