"""组合管理：现金、持仓、盈亏（T+1、整手）"""
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Position:
    code: str
    quantity: int
    available_qty: int  # T+1：当日买入冻结，次一交易日解冻
    cost_price: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def profit(self) -> float:
        return self.quantity * (self.current_price - self.cost_price)


@dataclass
class Portfolio:
    initial_cash: float = 100000.0
    cash: float = field(default_factory=lambda: 100000.0)
    positions: Dict[str, Position] = field(default_factory=dict)
    trades: list = field(default_factory=list)
    last_date: str = ""  # 用于 T+1 解冻判断
    buy_amount: float = field(default_factory=float)  # 累计买入成交金额（turnover 分子）
    total_cost: float = field(default_factory=float)  # 累计佣金+印花税+滑点（cost 分子）

    @property
    def total_value(self) -> float:
        mv = sum(p.market_value for p in self.positions.values())
        return self.cash + mv

    @property
    def total_return(self) -> float:
        return (self.total_value - self.initial_cash) / self.initial_cash

    def buy(self, code: str, price: float, qty: int, commission: float,
            trade_date: str = "", slippage: float = 0.0):
        if qty % 100 != 0:
            raise ValueError("买入数量必须为100股整数倍")
        cost = price * qty + commission
        if cost > self.cash:
            raise ValueError("资金不足")
        self.cash -= cost
        # 风险指标累计（§3.5）：buy_amount=买入成交金额，total_cost=佣金+滑点
        self.buy_amount += price * qty
        self.total_cost += commission + slippage
        if code in self.positions:
            pos = self.positions[code]
            total_cost = pos.cost_price * pos.quantity + price * qty
            pos.quantity += qty
            pos.cost_price = total_cost / pos.quantity
            # T+1：当日买入部分冻结，由引擎在次一交易日解冻
            pos.available_qty -= 0  # 冻结体现在 available_qty 不动，解冻逻辑见 engine._unfreeze_t1
        else:
            # T+1：新建仓当日 available_qty = 0
            self.positions[code] = Position(code, qty, 0, price, price)
        self.last_date = trade_date

    def sell(self, code: str, price: float, qty: int, commission: float,
             tax: float, slippage: float = 0.0):
        if code not in self.positions:
            raise ValueError("无持仓")
        pos = self.positions[code]
        if qty > pos.available_qty:
            raise ValueError("可用持仓不足（T+1 限制）")
        proceeds = price * qty - commission - tax
        self.cash += proceeds
        # 风险指标累计（§3.5）：total_cost=佣金+印花税+滑点
        self.total_cost += commission + tax + slippage
        pos.quantity -= qty
        pos.available_qty -= qty
        if pos.quantity == 0:
            del self.positions[code]

    def mark_to_market(self, prices: Dict[str, float]):
        """每日收盘 mark-to-market：停牌（无当日价）保留旧价。

        Iteration 4 风控镜像：Go ExecuteDay 第 2 步先按当日收盘更新持仓现价，
        止损 profit_rate 与回撤熔断 total_value 均以此口径计算。
        """
        for code, pos in self.positions.items():
            price = prices.get(code)
            if price is not None:
                pos.current_price = price
