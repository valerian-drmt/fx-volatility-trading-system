from __future__ import annotations

import time

from ib_insync import IB


class IBClient_bis:
    """Pythonic wrapper around ib_insync.IB."""

    def __init__(self, ib: IB, port: int = 7497) -> None:
        self.ib = ib
        self.port = port
        self.is_connected = bool(self.ib.isConnected())

    def get_environment(self) -> str:
        if self.port in (4002, 7497):
            return "paper"
        if self.port in (4001, 7496):
            return "live"
        return "unknown"

    def get_status_panel_variables_from_ib(self) -> dict[str, object]:
        """
        Return all values needed by the Status panel in one call.

        Keys:
        - ib_connected: bool
        - account: str
        - server_time_text: str
        - latency_text: str
        """
        ib_connected = bool(self.is_connected)
        account = "--"
        server_time_text = "--"
        latency_text = "--"

        if ib_connected:
            try:
                accounts = self.ib.managedAccounts()
                if accounts:
                    account = accounts[0]
            except Exception:
                account = "--"

            try:
                start = time.perf_counter()
                server_time = self.ib.reqCurrentTime()
                latency_ms = int((time.perf_counter() - start) * 1000)
                server_time_text = (
                    server_time.strftime("%H:%M:%S")
                    if hasattr(server_time, "strftime")
                    else str(server_time)
                )
                latency_text = f"{latency_ms} ms"
            except Exception:
                server_time_text = "--"
                latency_text = "--"

        return {
            "ib_connected": ib_connected,
            "account": account,
            "server_time_text": server_time_text,
            "latency_text": latency_text,
            "environment": self.get_environment(),
        }

    def get_portfolio_panel_variables_from_ib(self, account: str = "") -> dict[str, object]:
        """
        Return all values needed by the Portfolio panel in one call.
        """
        data: dict[str, object] = {
            "net_liquidation": "--",
            "total_cash_value": "--",
            "available_funds": "--",
            "unrealized_pnl": "--",
            "realized_pnl": "--",
            "gross_position_value": "--",
            "position_symbol": "--",
            "position_qty": "--",
        }

        tag_to_key = {
            "NetLiquidation": "net_liquidation",
            "TotalCashValue": "total_cash_value",
            "AvailableFunds": "available_funds",
            "UnrealizedPnL": "unrealized_pnl",
            "RealizedPnL": "realized_pnl",
            "GrossPositionValue": "gross_position_value",
        }

        try:
            summary = self.ib.accountSummary(account)
            for item in summary:
                tag = getattr(item, "tag", None)
                key = tag_to_key.get(tag)
                if key is None:
                    continue
                value = getattr(item, "value", None)
                currency = getattr(item, "currency", None)
                if value is None:
                    continue
                data[key] = f"{value} {currency}".strip() if currency else str(value)
        except Exception:
            pass

        try:
            positions = self.ib.positions(account)
            if positions:
                first_position = positions[0]
                contract = getattr(first_position, "contract", None)
                symbol = (
                    getattr(contract, "localSymbol", None)
                    or getattr(contract, "symbol", None)
                    or "?"
                )
                data["position_symbol"] = symbol
                data["position_qty"] = getattr(first_position, "position", "--")
        except Exception:
            pass

        return data

    def get_chart_panel_variables_from_ib(self, contract, subscribe: bool = True, wait_seconds: float = 0.0) -> dict[str, object]:
        """
        Return all values needed by the Chart panel in one call.
        """
        data: dict[str, object] = {
            "bid": None,
            "ask": None,
            "last": None,
            "close": None,
            "volume": None,
            "market_time": None,
        }

        if contract is None:
            return data

        try:
            if subscribe:
                self.ib.reqMktData(contract)
            if wait_seconds > 0:
                self.ib.sleep(wait_seconds)
            ticker = self.ib.ticker(contract)
            if ticker is None:
                return data
            data["bid"] = getattr(ticker, "bid", None)
            data["ask"] = getattr(ticker, "ask", None)
            data["last"] = getattr(ticker, "last", None)
            data["close"] = getattr(ticker, "close", None)
            data["volume"] = getattr(ticker, "volume", None)
            data["market_time"] = getattr(ticker, "time", None)
        except Exception:
            pass

        return data

    def get_orders_panel_variables_from_ib(self) -> dict[str, object]:
        """
        Return all values needed by the Orders panel in one call.
        """
        data: dict[str, object] = {
            "open_order_id": "--",
            "open_order_symbol": "--",
            "open_order_side": "--",
            "open_order_qty": "--",
            "open_order_price": "--",
            "open_order_status": "--",
            "fill_time": "--",
            "fill_symbol": "--",
            "fill_side": "--",
            "fill_qty": "--",
            "fill_price": "--",
        }

        try:
            open_trades = self.ib.openTrades()
            if open_trades:
                trade = open_trades[0]
                order = getattr(trade, "order", None)
                order_status = getattr(trade, "orderStatus", None)
                contract = getattr(trade, "contract", None)

                data["open_order_id"] = getattr(order, "orderId", "--")
                data["open_order_symbol"] = (
                    getattr(contract, "localSymbol", None)
                    or getattr(contract, "symbol", None)
                    or "?"
                )
                data["open_order_side"] = getattr(order, "action", "--")
                data["open_order_qty"] = getattr(order, "totalQuantity", "--")

                limit_price = getattr(order, "lmtPrice", None)
                aux_price = getattr(order, "auxPrice", None)
                data["open_order_price"] = (
                    limit_price
                    if limit_price is not None
                    else (aux_price if aux_price is not None else "--")
                )
                data["open_order_status"] = getattr(order_status, "status", "--")
        except Exception:
            pass

        try:
            fill_items = self.ib.fills()
            if fill_items:
                fill = fill_items[-1]
                execution = getattr(fill, "execution", None)
                contract = getattr(fill, "contract", None)

                data["fill_time"] = getattr(fill, "time", "--")
                data["fill_symbol"] = (
                    getattr(contract, "localSymbol", None)
                    or getattr(contract, "symbol", None)
                    or "?"
                )
                data["fill_side"] = getattr(execution, "side", "--")
                data["fill_qty"] = getattr(execution, "shares", "--")
                data["fill_price"] = getattr(execution, "price", "--")
        except Exception:
            pass

        return data

    def get_performance_panel_variables_from_ib(self, account: str, model_code: str = "", con_id: int | None = None, auto_cancel: bool = False) -> dict[str, object]:
        """
        Return all values needed by the Performance panel in one call.
        """
        data: dict[str, object] = {
            "daily_pnl": None,
            "unrealized_pnl": None,
            "realized_pnl": None,
            "pnl_single_position": None,
            "executions_count": 0,
        }

        if not account:
            return data

        pnl_requested = False
        try:
            pnl_obj = self.ib.reqPnL(account, model_code)
            pnl_requested = pnl_obj is not None
            self.ib.sleep(0)
            data["daily_pnl"] = getattr(pnl_obj, "dailyPnL", None)
            data["unrealized_pnl"] = getattr(pnl_obj, "unrealizedPnL", None)
            data["realized_pnl"] = getattr(pnl_obj, "realizedPnL", None)
        except Exception:
            pass
        finally:
            if auto_cancel and pnl_requested:
                try:
                    self.ib.cancelPnL(account, model_code)
                except Exception:
                    pass

        pnl_single_requested = False
        if con_id is not None:
            try:
                pnl_single_obj = self.ib.reqPnLSingle(account, model_code, con_id)
                pnl_single_requested = pnl_single_obj is not None
                self.ib.sleep(0)
                data["pnl_single_position"] = getattr(pnl_single_obj, "position", None)
            except Exception:
                pass
            finally:
                if auto_cancel and pnl_single_requested:
                    try:
                        self.ib.cancelPnLSingle(account, model_code, con_id)
                    except Exception:
                        pass

        try:
            data["executions_count"] = len(self.ib.reqExecutions())
        except Exception:
            data["executions_count"] = 0

        return data

    def get_risk_panel_variables_from_ib(self, account: str = "", model_code: str = "", contract=None, order=None, preferred_account_tag: str = "NetLiquidation", auto_cancel_pnl: bool = False) -> dict[str, object]:
        """
        Return all values needed by the Risk panel in one call.
        """
        data: dict[str, object] = {
            "account_value_tag": "--",
            "account_value": "--",
            "pnl_daily": None,
            "pnl_unrealized": None,
            "pnl_realized": None,
            "what_if_init_margin_change": None,
            "what_if_maint_margin_change": None,
            "what_if_commission": None,
        }

        try:
            values = self.ib.accountValues(account)
            selected = None
            for item in values:
                if getattr(item, "tag", None) == preferred_account_tag:
                    selected = item
                    break
            if selected is None and values:
                selected = values[0]
            if selected is not None:
                data["account_value_tag"] = getattr(selected, "tag", "--")
                data["account_value"] = getattr(selected, "value", "--")
        except Exception:
            pass

        pnl_requested = False
        if account:
            try:
                pnl_obj = self.ib.reqPnL(account, model_code)
                pnl_requested = pnl_obj is not None
                self.ib.sleep(0)
                data["pnl_daily"] = getattr(pnl_obj, "dailyPnL", None)
                data["pnl_unrealized"] = getattr(pnl_obj, "unrealizedPnL", None)
                data["pnl_realized"] = getattr(pnl_obj, "realizedPnL", None)
            except Exception:
                pass
            finally:
                if auto_cancel_pnl and pnl_requested:
                    try:
                        self.ib.cancelPnL(account, model_code)
                    except Exception:
                        pass

        if contract is not None and order is not None:
            try:
                order_state = self.ib.whatIfOrder(contract, order)
                data["what_if_init_margin_change"] = getattr(order_state, "initMarginChange", None)
                data["what_if_maint_margin_change"] = getattr(order_state, "maintMarginChange", None)
                data["what_if_commission"] = getattr(order_state, "commission", None)
            except Exception:
                pass

        return data

    def get_logs_panel_variables_from_ib(self) -> dict[str, object]:
        """
        Return the latest values needed by the Logs panel in one call.
        """
        data: dict[str, object] = {
            "ib_event_time": "--",
            "ib_event_message": "--",
            "ib_event_type": "--",
        }

        try:
            fill_items = self.ib.fills()
            if fill_items:
                latest_fill = fill_items[-1]
                execution = getattr(latest_fill, "execution", None)
                order_id = getattr(execution, "orderId", "?")
                data["ib_event_time"] = getattr(latest_fill, "time", "--")
                data["ib_event_message"] = f"Order {order_id} filled"
                data["ib_event_type"] = "fill"
                return data
        except Exception:
            pass

        try:
            execution_items = self.ib.executions()
            if execution_items:
                latest_execution = execution_items[-1]
                exec_id = getattr(latest_execution, "execId", "?")
                symbol = getattr(latest_execution, "symbol", "?")
                data["ib_event_time"] = getattr(latest_execution, "time", "--")
                data["ib_event_message"] = f"Execution {exec_id} for {symbol}"
                data["ib_event_type"] = "execution"
                return data
        except Exception:
            pass

        try:
            trade_items = self.ib.trades()
            if trade_items:
                latest_trade = trade_items[-1]
                order = getattr(latest_trade, "order", None)
                order_status = getattr(latest_trade, "orderStatus", None)
                log_items = getattr(latest_trade, "log", None) or []
                latest_log = log_items[-1] if log_items else None

                order_id = getattr(order, "orderId", "?")
                status = getattr(order_status, "status", "Unknown")
                message = getattr(latest_log, "message", None)

                data["ib_event_time"] = getattr(latest_log, "time", "--") if latest_log else "--"
                data["ib_event_message"] = (
                    message if message else f"Order {order_id} status {status}"
                )
                data["ib_event_type"] = "order_status"
        except Exception:
            pass

        return data



























    # Connection and event loop
    def connect(self, host: str = '127.0.0.1', port: int = 7497, clientId: int = 1, timeout: float = 4, readonly: bool = False, account: str = ''):
        self.port = port
        result = self.ib.connect(host, port, clientId, timeout, readonly, account)
        self.is_connected = bool(self.ib.isConnected())
        return result

    def connect_async(self, host: str = '127.0.0.1', port: int = 7497, clientId: int = 1, timeout: Optional[float] = 4, readonly: bool = False, account: str = ''):
        self.port = port
        return self.ib.connectAsync(host, port, clientId, timeout, readonly, account)

    def disconnect(self):
        result = self.ib.disconnect()
        self.is_connected = bool(self.ib.isConnected())
        return result

    def run(self, *awaitables: Awaitable, timeout: Optional[float] = None):
        return self.ib.run(*awaitables, timeout=timeout)

    def set_timeout(self, timeout: float = 60):
        return self.ib.setTimeout(timeout)

    def wait_on_update(self, timeout: float = 0) -> bool:
        return self.ib.waitOnUpdate(timeout)

    def wait_until(self, t: Union[datetime.time, datetime.datetime]) -> bool:
        return self.ib.waitUntil(t)

    def loop_until(self, condition=None, timeout: float = 0) -> Iterator[object]:
        return self.ib.loopUntil(condition, timeout)

    def schedule(self, time: Union[datetime.time, datetime.datetime], callback: Callable, *args):
        return self.ib.schedule(time, callback, *args)

    def time_range(self, start: Union[datetime.time, datetime.datetime], end: Union[datetime.time, datetime.datetime], step: float) -> Iterator[datetime.datetime]:
        return self.ib.timeRange(start, end, step)

    def time_range_async(self, start: Union[datetime.time, datetime.datetime], end: Union[datetime.time, datetime.datetime], step: float) -> AsyncIterator[datetime.datetime]:
        return self.ib.timeRangeAsync(start, end, step)

    # Account, portfolio, and PnL
    def account_summary_async(self, account: str = '') -> List[ib_insync.objects.AccountValue]:
        return self.ib.accountSummaryAsync(account)

    def portfolio(self, account: str = '') -> List[ib_insync.objects.PortfolioItem]:
        return self.ib.portfolio(account)

    def pnl(self, account='', modelCode='') -> List[ib_insync.objects.PnL]:
        return self.ib.pnl(account, modelCode)

    def pnl_single(self, account: str = '', modelCode: str = '', conId: int = 0) -> List[ib_insync.objects.PnLSingle]:
        return self.ib.pnlSingle(account, modelCode, conId)

    def req_account_summary(self):
        return self.ib.reqAccountSummary()

    def req_account_summary_async(self) -> Awaitable[NoneType]:
        return self.ib.reqAccountSummaryAsync()

    def req_account_updates(self, account: str = ''):
        return self.ib.reqAccountUpdates(account)

    def req_account_updates_async(self, account: str) -> Awaitable[NoneType]:
        return self.ib.reqAccountUpdatesAsync(account)

    def req_account_updates_multi(self, account: str = '', modelCode: str = ''):
        return self.ib.reqAccountUpdatesMulti(account, modelCode)

    def req_account_updates_multi_async(self, account: str, modelCode: str = '') -> Awaitable[NoneType]:
        return self.ib.reqAccountUpdatesMultiAsync(account, modelCode)

    def req_positions(self) -> List[ib_insync.objects.Position]:
        return self.ib.reqPositions()

    def req_positions_async(self) -> Awaitable[List[ib_insync.objects.Position]]:
        return self.ib.reqPositionsAsync()

    # Orders and executions
    def place_order(self, contract: ib_insync.contract.Contract, order: ib_insync.order.Order) -> ib_insync.order.Trade:
        return self.ib.placeOrder(contract, order)

    def cancel_order(self, order: ib_insync.order.Order, manualCancelOrderTime: str = '') -> Optional[ib_insync.order.Trade]:
        return self.ib.cancelOrder(order, manualCancelOrderTime)

    def what_if_order_async(self, contract: ib_insync.contract.Contract, order: ib_insync.order.Order) -> Awaitable[ib_insync.order.OrderState]:
        return self.ib.whatIfOrderAsync(contract, order)

    def bracket_order(self, action: str, quantity: float, limitPrice: float, takeProfitPrice: float, stopLossPrice: float, **kwargs) -> ib_insync.order.BracketOrder:
        return self.ib.bracketOrder(action, quantity, limitPrice, takeProfitPrice, stopLossPrice, **kwargs)

    def one_cancels_all(self, orders: List[ib_insync.order.Order], ocaGroup: str, ocaType: int) -> List[ib_insync.order.Order]:
        return self.ib.oneCancelsAll(orders, ocaGroup, ocaType)

    def exercise_options(self, contract: ib_insync.contract.Contract, exerciseAction: int, exerciseQuantity: int, account: str, override: int):
        return self.ib.exerciseOptions(contract, exerciseAction, exerciseQuantity, account, override)

    def open_orders(self) -> List[ib_insync.order.Order]:
        return self.ib.openOrders()

    def orders(self) -> List[ib_insync.order.Order]:
        return self.ib.orders()

    def req_executions_async(self, execFilter: Optional[ib_insync.objects.ExecutionFilter] = None) -> Awaitable[List[ib_insync.objects.Fill]]:
        return self.ib.reqExecutionsAsync(execFilter)

    def req_open_orders(self) -> List[ib_insync.order.Trade]:
        return self.ib.reqOpenOrders()

    def req_open_orders_async(self) -> Awaitable[List[ib_insync.order.Trade]]:
        return self.ib.reqOpenOrdersAsync()

    def req_all_open_orders(self) -> List[ib_insync.order.Trade]:
        return self.ib.reqAllOpenOrders()

    def req_all_open_orders_async(self) -> Awaitable[List[ib_insync.order.Trade]]:
        return self.ib.reqAllOpenOrdersAsync()

    def req_auto_open_orders(self, autoBind: bool = True):
        return self.ib.reqAutoOpenOrders(autoBind)

    def req_completed_orders(self, apiOnly: bool) -> List[ib_insync.order.Trade]:
        return self.ib.reqCompletedOrders(apiOnly)

    def req_completed_orders_async(self, apiOnly: bool) -> Awaitable[List[ib_insync.order.Trade]]:
        return self.ib.reqCompletedOrdersAsync(apiOnly)

    def req_global_cancel(self):
        return self.ib.reqGlobalCancel()

    # Real-time market data
    def cancel_mkt_data(self, contract: ib_insync.contract.Contract):
        return self.ib.cancelMktData(contract)

    def tickers(self) -> List[ib_insync.ticker.Ticker]:
        return self.ib.tickers()

    def pending_tickers(self) -> List[ib_insync.ticker.Ticker]:
        return self.ib.pendingTickers()

    def req_tickers(self, *contracts: ib_insync.contract.Contract, regulatorySnapshot: bool = False) -> List[ib_insync.ticker.Ticker]:
        return self.ib.reqTickers(*contracts, regulatorySnapshot=regulatorySnapshot)

    def req_tickers_async(self, *contracts: ib_insync.contract.Contract, regulatorySnapshot: bool = False) -> List[ib_insync.ticker.Ticker]:
        return self.ib.reqTickersAsync(*contracts, regulatorySnapshot=regulatorySnapshot)

    def req_tick_by_tick_data(self, contract: ib_insync.contract.Contract, tickType: str, numberOfTicks: int = 0, ignoreSize: bool = False) -> ib_insync.ticker.Ticker:
        return self.ib.reqTickByTickData(contract, tickType, numberOfTicks, ignoreSize)

    def cancel_tick_by_tick_data(self, contract: ib_insync.contract.Contract, tickType: str):
        return self.ib.cancelTickByTickData(contract, tickType)

    def req_mkt_depth(self, contract: ib_insync.contract.Contract, numRows: int = 5, isSmartDepth: bool = False, mktDepthOptions=None) -> ib_insync.ticker.Ticker:
        return self.ib.reqMktDepth(contract, numRows, isSmartDepth, mktDepthOptions)

    def cancel_mkt_depth(self, contract: ib_insync.contract.Contract, isSmartDepth=False):
        return self.ib.cancelMktDepth(contract, isSmartDepth)

    def req_mkt_depth_exchanges(self) -> List[ib_insync.objects.DepthMktDataDescription]:
        return self.ib.reqMktDepthExchanges()

    def req_mkt_depth_exchanges_async(self) -> Awaitable[List[ib_insync.objects.DepthMktDataDescription]]:
        return self.ib.reqMktDepthExchangesAsync()

    def req_market_data_type(self, marketDataType: int):
        return self.ib.reqMarketDataType(marketDataType)

    def req_real_time_bars(self, contract: ib_insync.contract.Contract, barSize: int, whatToShow: str, useRTH: bool, realTimeBarsOptions: List[ib_insync.contract.TagValue] = []) -> ib_insync.objects.RealTimeBarList:
        return self.ib.reqRealTimeBars(contract, barSize, whatToShow, useRTH, realTimeBarsOptions)

    def cancel_real_time_bars(self, bars: ib_insync.objects.RealTimeBarList):
        return self.ib.cancelRealTimeBars(bars)

    def realtime_bars(self) -> List[Union[ib_insync.objects.BarDataList, ib_insync.objects.RealTimeBarList]]:
        return self.ib.realtimeBars()

    # Historical and contract data
    def req_historical_data(self, contract: ib_insync.contract.Contract, endDateTime: Union[datetime.datetime, datetime.date, str, NoneType], durationStr: str, barSizeSetting: str, whatToShow: str, useRTH: bool, formatDate: int = 1, keepUpToDate: bool = False, chartOptions: List[ib_insync.contract.TagValue] = [], timeout: float = 60) -> ib_insync.objects.BarDataList:
        return self.ib.reqHistoricalData(contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH, formatDate, keepUpToDate, chartOptions, timeout)

    def req_historical_data_async(self, contract: ib_insync.contract.Contract, endDateTime: Union[datetime.datetime, datetime.date, str, NoneType], durationStr: str, barSizeSetting: str, whatToShow: str, useRTH: bool, formatDate: int = 1, keepUpToDate: bool = False, chartOptions: List[ib_insync.contract.TagValue] = [], timeout: float = 60) -> ib_insync.objects.BarDataList:
        return self.ib.reqHistoricalDataAsync(contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH, formatDate, keepUpToDate, chartOptions, timeout)

    def cancel_historical_data(self, bars: ib_insync.objects.BarDataList):
        return self.ib.cancelHistoricalData(bars)

    def req_historical_ticks(self, contract: ib_insync.contract.Contract, startDateTime: Union[str, datetime.date], endDateTime: Union[str, datetime.date], numberOfTicks: int, whatToShow: str, useRth: bool, ignoreSize: bool = False, miscOptions: List[ib_insync.contract.TagValue] = []) -> List:
        return self.ib.reqHistoricalTicks(contract, startDateTime, endDateTime, numberOfTicks, whatToShow, useRth, ignoreSize, miscOptions)

    def req_historical_ticks_async(self, contract: ib_insync.contract.Contract, startDateTime: Union[str, datetime.date], endDateTime: Union[str, datetime.date], numberOfTicks: int, whatToShow: str, useRth: bool, ignoreSize: bool = False, miscOptions: List[ib_insync.contract.TagValue] = []) -> Awaitable[List]:
        return self.ib.reqHistoricalTicksAsync(contract, startDateTime, endDateTime, numberOfTicks, whatToShow, useRth, ignoreSize, miscOptions)

    def req_historical_schedule(self, contract: ib_insync.contract.Contract, numDays: int, endDateTime: Union[datetime.datetime, datetime.date, str, NoneType] = '', useRTH: bool = True) -> ib_insync.objects.HistoricalSchedule:
        return self.ib.reqHistoricalSchedule(contract, numDays, endDateTime, useRTH)

    def req_historical_schedule_async(self, contract: ib_insync.contract.Contract, numDays: int, endDateTime: Union[datetime.datetime, datetime.date, str, NoneType] = '', useRTH: bool = True) -> Awaitable[ib_insync.objects.HistoricalSchedule]:
        return self.ib.reqHistoricalScheduleAsync(contract, numDays, endDateTime, useRTH)

    def req_head_time_stamp(self, contract: ib_insync.contract.Contract, whatToShow: str, useRTH: bool, formatDate: int = 1) -> datetime.datetime:
        return self.ib.reqHeadTimeStamp(contract, whatToShow, useRTH, formatDate)

    def req_head_time_stamp_async(self, contract: ib_insync.contract.Contract, whatToShow: str, useRTH: bool, formatDate: int) -> datetime.datetime:
        return self.ib.reqHeadTimeStampAsync(contract, whatToShow, useRTH, formatDate)

    def req_histogram_data(self, contract: ib_insync.contract.Contract, useRTH: bool, period: str) -> List[ib_insync.objects.HistogramData]:
        return self.ib.reqHistogramData(contract, useRTH, period)

    def req_histogram_data_async(self, contract: ib_insync.contract.Contract, useRTH: bool, period: str) -> Awaitable[List[ib_insync.objects.HistogramData]]:
        return self.ib.reqHistogramDataAsync(contract, useRTH, period)

    def req_contract_details(self, contract: ib_insync.contract.Contract) -> List[ib_insync.contract.ContractDetails]:
        return self.ib.reqContractDetails(contract)

    def req_contract_details_async(self, contract: ib_insync.contract.Contract) -> Awaitable[List[ib_insync.contract.ContractDetails]]:
        return self.ib.reqContractDetailsAsync(contract)

    def qualify_contracts(self, *contracts: ib_insync.contract.Contract) -> List[ib_insync.contract.Contract]:
        return self.ib.qualifyContracts(*contracts)

    def qualify_contracts_async(self, *contracts: ib_insync.contract.Contract) -> List[ib_insync.contract.Contract]:
        return self.ib.qualifyContractsAsync(*contracts)

    def req_matching_symbols(self, pattern: str) -> List[ib_insync.contract.ContractDescription]:
        return self.ib.reqMatchingSymbols(pattern)

    def req_matching_symbols_async(self, pattern: str) -> Optional[List[ib_insync.contract.ContractDescription]]:
        return self.ib.reqMatchingSymbolsAsync(pattern)

    def req_market_rule(self, marketRuleId: int) -> ib_insync.objects.PriceIncrement:
        return self.ib.reqMarketRule(marketRuleId)

    def req_market_rule_async(self, marketRuleId: int) -> Optional[List[ib_insync.objects.PriceIncrement]]:
        return self.ib.reqMarketRuleAsync(marketRuleId)

    def req_current_time_async(self) -> Awaitable[datetime.datetime]:
        return self.ib.reqCurrentTimeAsync()

    def req_fundamental_data(self, contract: ib_insync.contract.Contract, reportType: str, fundamentalDataOptions: List[ib_insync.contract.TagValue] = []) -> str:
        return self.ib.reqFundamentalData(contract, reportType, fundamentalDataOptions)

    def req_fundamental_data_async(self, contract: ib_insync.contract.Contract, reportType: str, fundamentalDataOptions: List[ib_insync.contract.TagValue] = []) -> Awaitable[str]:
        return self.ib.reqFundamentalDataAsync(contract, reportType, fundamentalDataOptions)

    def req_sec_def_opt_params(self, underlyingSymbol: str, futFopExchange: str, underlyingSecType: str, underlyingConId: int) -> List[ib_insync.objects.OptionChain]:
        return self.ib.reqSecDefOptParams(underlyingSymbol, futFopExchange, underlyingSecType, underlyingConId)

    def req_sec_def_opt_params_async(self, underlyingSymbol: str, futFopExchange: str, underlyingSecType: str, underlyingConId: int) -> Awaitable[List[ib_insync.objects.OptionChain]]:
        return self.ib.reqSecDefOptParamsAsync(underlyingSymbol, futFopExchange, underlyingSecType, underlyingConId)

    def req_smart_components(self, bboExchange: str) -> List[ib_insync.objects.SmartComponent]:
        return self.ib.reqSmartComponents(bboExchange)

    def req_smart_components_async(self, bboExchange):
        return self.ib.reqSmartComponentsAsync(bboExchange)

    # News, scanner, and WSH
    def req_news_providers(self) -> List[ib_insync.objects.NewsProvider]:
        return self.ib.reqNewsProviders()

    def req_news_providers_async(self) -> Awaitable[List[ib_insync.objects.NewsProvider]]:
        return self.ib.reqNewsProvidersAsync()

    def req_news_article(self, providerCode: str, articleId: str, newsArticleOptions: List[ib_insync.contract.TagValue] = []) -> ib_insync.objects.NewsArticle:
        return self.ib.reqNewsArticle(providerCode, articleId, newsArticleOptions)

    def req_news_article_async(self, providerCode: str, articleId: str, newsArticleOptions: List[ib_insync.contract.TagValue] = []) -> Awaitable[ib_insync.objects.NewsArticle]:
        return self.ib.reqNewsArticleAsync(providerCode, articleId, newsArticleOptions)

    def req_historical_news(self, conId: int, providerCodes: str, startDateTime: Union[str, datetime.date], endDateTime: Union[str, datetime.date], totalResults: int, historicalNewsOptions: List[ib_insync.contract.TagValue] = []) -> ib_insync.objects.HistoricalNews:
        return self.ib.reqHistoricalNews(conId, providerCodes, startDateTime, endDateTime, totalResults, historicalNewsOptions)

    def req_historical_news_async(self, conId: int, providerCodes: str, startDateTime: Union[str, datetime.date], endDateTime: Union[str, datetime.date], totalResults: int, historicalNewsOptions: List[ib_insync.contract.TagValue] = []) -> Optional[ib_insync.objects.HistoricalNews]:
        return self.ib.reqHistoricalNewsAsync(conId, providerCodes, startDateTime, endDateTime, totalResults, historicalNewsOptions)

    def req_news_bulletins(self, allMessages: bool):
        return self.ib.reqNewsBulletins(allMessages)

    def cancel_news_bulletins(self):
        return self.ib.cancelNewsBulletins()

    def news_bulletins(self) -> List[ib_insync.objects.NewsBulletin]:
        return self.ib.newsBulletins()

    def news_ticks(self) -> List[ib_insync.objects.NewsTick]:
        return self.ib.newsTicks()

    def req_scanner_subscription(self, subscription: ib_insync.objects.ScannerSubscription, scannerSubscriptionOptions: List[ib_insync.contract.TagValue] = [], scannerSubscriptionFilterOptions: List[ib_insync.contract.TagValue] = []) -> ib_insync.objects.ScanDataList:
        return self.ib.reqScannerSubscription(subscription, scannerSubscriptionOptions, scannerSubscriptionFilterOptions)

    def cancel_scanner_subscription(self, dataList: ib_insync.objects.ScanDataList):
        return self.ib.cancelScannerSubscription(dataList)

    def req_scanner_data(self, subscription: ib_insync.objects.ScannerSubscription, scannerSubscriptionOptions: List[ib_insync.contract.TagValue] = [], scannerSubscriptionFilterOptions: List[ib_insync.contract.TagValue] = []) -> ib_insync.objects.ScanDataList:
        return self.ib.reqScannerData(subscription, scannerSubscriptionOptions, scannerSubscriptionFilterOptions)

    def req_scanner_data_async(self, subscription: ib_insync.objects.ScannerSubscription, scannerSubscriptionOptions: List[ib_insync.contract.TagValue] = [], scannerSubscriptionFilterOptions: List[ib_insync.contract.TagValue] = []) -> ib_insync.objects.ScanDataList:
        return self.ib.reqScannerDataAsync(subscription, scannerSubscriptionOptions, scannerSubscriptionFilterOptions)

    def req_scanner_parameters(self) -> str:
        return self.ib.reqScannerParameters()

    def req_scanner_parameters_async(self) -> Awaitable[str]:
        return self.ib.reqScannerParametersAsync()

    def req_wsh_meta_data(self):
        return self.ib.reqWshMetaData()

    def cancel_wsh_meta_data(self):
        return self.ib.cancelWshMetaData()

    def get_wsh_meta_data(self) -> str:
        return self.ib.getWshMetaData()

    def get_wsh_meta_data_async(self) -> str:
        return self.ib.getWshMetaDataAsync()

    def req_wsh_event_data(self, data: ib_insync.objects.WshEventData):
        return self.ib.reqWshEventData(data)

    def cancel_wsh_event_data(self):
        return self.ib.cancelWshEventData()

    def get_wsh_event_data(self, data: ib_insync.objects.WshEventData) -> str:
        return self.ib.getWshEventData(data)

    def get_wsh_event_data_async(self, data: ib_insync.objects.WshEventData) -> str:
        return self.ib.getWshEventDataAsync(data)

    # Options and pricing tools
    def calculate_option_price(self, contract: ib_insync.contract.Contract, volatility: float, underPrice: float, optPrcOptions: List[ib_insync.contract.TagValue] = []) -> ib_insync.objects.OptionComputation:
        return self.ib.calculateOptionPrice(contract, volatility, underPrice, optPrcOptions)

    def calculate_option_price_async(self, contract: ib_insync.contract.Contract, volatility: float, underPrice: float, optPrcOptions: List[ib_insync.contract.TagValue] = []) -> Optional[ib_insync.objects.OptionComputation]:
        return self.ib.calculateOptionPriceAsync(contract, volatility, underPrice, optPrcOptions)

    def calculate_implied_volatility(self, contract: ib_insync.contract.Contract, optionPrice: float, underPrice: float, implVolOptions: List[ib_insync.contract.TagValue] = []) -> ib_insync.objects.OptionComputation:
        return self.ib.calculateImpliedVolatility(contract, optionPrice, underPrice, implVolOptions)

    def calculate_implied_volatility_async(self, contract: ib_insync.contract.Contract, optionPrice: float, underPrice: float, implVolOptions: List[ib_insync.contract.TagValue] = []) -> Optional[ib_insync.objects.OptionComputation]:
        return self.ib.calculateImpliedVolatilityAsync(contract, optionPrice, underPrice, implVolOptions)

    # FA and user profile
    def request_fa(self, faDataType: int):
        return self.ib.requestFA(faDataType)

    def request_fa_async(self, faDataType: int):
        return self.ib.requestFAAsync(faDataType)

    def replace_fa(self, faDataType: int, xml: str):
        return self.ib.replaceFA(faDataType, xml)

    def req_user_info(self) -> str:
        return self.ib.reqUserInfo()

    def req_user_info_async(self):
        return self.ib.reqUserInfoAsync()
