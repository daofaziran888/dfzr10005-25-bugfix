"""并发控制原语：同一份业务记录的争抢操作要在同一把锁内串行判定。

规则归档、两个管理员同时操作同一规则这类场景，本质上是“先判定状态、再落结论”
的读改写过程，必须加锁并在锁内复检，否则两个请求都会通过校验并各自写出结论。
每个业务模块一把可重入锁，按规则 id 串行化即可覆盖同规则争抢。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

_module_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()


@contextmanager
def module_lock(module: str) -> Iterator[None]:
    with _guard:
        lock = _module_locks.setdefault(module, threading.RLock())
    with lock:
        yield
