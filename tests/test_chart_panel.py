import math

import pytest

from ui.panels.chart_panel import ChartPanel


@pytest.mark.unit
def test_chart_panel_plots_bid_and_ask_series(qapp):
    panel = ChartPanel(max_points=20)

    panel.update(
        {
            "ticks": [
                {"time": "13:51:04.803", "bid": 1.1000, "ask": 1.1002},
                {"time": "13:51:05.011", "bid": 1.1001, "ask": 1.1003},
            ]
        }
    )
    panel._flush_redraw()

    assert list(panel._x) == [1, 2]
    assert list(panel._bid_y) == [1.1000, 1.1001]
    assert list(panel._ask_y) == [1.1002, 1.1003]


@pytest.mark.unit
def test_chart_panel_reuses_last_side_when_one_side_missing(qapp):
    panel = ChartPanel(max_points=20)

    panel.update({"ticks": [{"time": "10:00:00.000", "bid": 1.2000, "ask": 1.2002}]})
    panel._flush_redraw()
    panel.update({"ticks": [{"time": "10:00:00.100", "bid": 1.2001}]})
    panel._flush_redraw()
    panel.update({"ticks": [{"time": "10:00:00.200", "ask": 1.2004}]})
    panel._flush_redraw()

    bid_series = list(panel._bid_y)
    ask_series = list(panel._ask_y)
    assert list(panel._x) == [1, 2, 3]
    assert bid_series[0] == 1.2000
    assert ask_series[0] == 1.2002
    assert bid_series[1] == 1.2001
    assert ask_series[1] == 1.2002
    assert bid_series[2] == 1.2001
    assert ask_series[2] == 1.2004
    assert not any(math.isnan(value) for value in bid_series + ask_series)


@pytest.mark.unit
def test_chart_panel_limits_ticks_processed_per_update(qapp):
    panel = ChartPanel(max_points=500)
    ticks = [{"bid": 1.1000 + i * 1e-5, "ask": 1.1002 + i * 1e-5} for i in range(100)]

    panel.update({"ticks": ticks})
    panel._flush_redraw()

    assert len(panel._x) == panel.MAX_TICKS_PER_UPDATE
    assert panel._x[0] == 1
    assert panel._x[-1] == panel.MAX_TICKS_PER_UPDATE


@pytest.mark.unit
def test_chart_panel_applies_right_shift_on_x_axis(qapp):
    panel = ChartPanel(max_points=100)
    panel.update({"ticks": [{"bid": 1.1000, "ask": 1.1002}]})
    panel._flush_redraw()

    x_min, x_max = panel.plot.viewRange()[0]
    assert x_min <= (1 - panel.X_RANGE_LEFT_PADDING_POINTS + 0.1)
    assert x_max >= (1 + panel.X_RANGE_RIGHT_SHIFT_POINTS - 0.1)


@pytest.mark.unit
def test_chart_panel_title_displays_fx_symbol(qapp):
    panel = ChartPanel(max_points=100)
    panel.update({"symbol": "gbpusd"})

    assert panel._title_label.text() == "Live Tick Chart - GBPUSD"
