"""小工具：限频器、标准输出静音、时间戳。"""

from __future__ import annotations

import contextlib
import io
import time
from datetime import datetime


class RateLimiter:
    """定周期节拍器。

    和 loop_rate_limiters 的区别：如果某一周期超时了，**不追赶**，
    直接以当前时刻为基准继续，避免电机收到一串"补课"指令。
    """

    def __init__(self, frequency: float) -> None:
        self.frequency = float(frequency)
        self.period = 1.0 / self.frequency if self.frequency > 0 else 0.0
        self._next = time.perf_counter()

    def reset(self) -> None:
        self._next = time.perf_counter()

    def sleep(self) -> None:
        if self.period <= 0:
            return
        self._next += self.period
        delay = self._next - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        else:
            self._next = time.perf_counter()


@contextlib.contextmanager
def quiet():
    """临时屏蔽标准输出。

    robstride_dynamics 的构造/连接会打印大段标定字典和每个电机的 ping 结果，
    在 GUI 里没用，统一静音，由我们自己的日志代替。
    """

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


def stamp() -> str:
    """文件名用的时间戳，例如 20251020_143012。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def pretty(moment: datetime | float) -> str:
    """给人看的时间，例如 2025-10-20 14:30:12。"""
    if isinstance(moment, (int, float)):
        moment = datetime.fromtimestamp(moment)
    return moment.strftime("%Y-%m-%d %H:%M:%S")
