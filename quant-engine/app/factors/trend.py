"""趋势因子（纯 pandas 实现，不依赖 ta-lib）

注意：因子计算统一使用前复权价（adj_factor 折算），
与模拟交易/回测成交使用的真实价区分。
"""
import numpy as np
import pandas as pd


def ma_trend(close: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
    """均线趋势因子：MA5 > MA20 为 1，否则 0；窗口不足为 NaN（不参与横截面）"""
    ma_short = close.rolling(short).mean()
    ma_long = close.rolling(long).mean()
    diff = ma_short - ma_long
    res = (diff > 0).astype(int)
    res[diff.isna()] = np.nan
    return res


def macd_signal(close: pd.Series, fast: int = 12, slow: int = 26,
                signal: int = 9) -> pd.Series:
    """MACD 信号因子：DIF > DEA 为 1，否则 0

    fast/slow/signal 为 G10 参数化重算的可调参数（默认经典 12/26/9）。
    """
    ema_fast = close.ewm(span=fast).mean()
    ema_slow = close.ewm(span=slow).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal).mean()
    return (dif > dea).astype(int)


def winsorize(s: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """去极值：截断到分位数区间，防止极端值主导横截面归一化"""
    lo, hi = s.quantile(lower), s.quantile(upper)
    return s.clip(lo, hi)


def rank_normalize(s: pd.Series) -> pd.Series:
    """横截面百分位归一化（0-1）：避免固定阈值随市场整体漂移"""
    return s.rank(pct=True)
