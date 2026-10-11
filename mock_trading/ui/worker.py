"""네트워크·DB 작업을 백그라운드에서 돌려 화면이 멈추지 않게 한다.

작업 스레드를 하나로 고정해 DB 연결을 동시에 두 스레드가 쓰지 않게 한다.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..domain import TradingError


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except TradingError as error:
            self.signals.failed.emit(str(error))
        except Exception as error:  # 네트워크 오류 등: 사용자에게는 요약만 보여준다
            traceback.print_exc()
            self.signals.failed.emit(f"처리 중 오류가 발생했습니다: {type(error).__name__}")
        else:
            self.signals.done.emit(result)


class Worker:
    def __init__(self) -> None:
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)
        self._jobs: set[_Job] = set()

    def run(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        job = _Job(fn)
        job.setAutoDelete(False)
        self._jobs.add(job)  # 시그널이 오기 전에 객체가 사라지지 않게 잡아 둔다
        if on_done:
            job.signals.done.connect(on_done)
        if on_failed:
            job.signals.failed.connect(on_failed)
        job.signals.done.connect(lambda _: self._jobs.discard(job))
        job.signals.failed.connect(lambda _: self._jobs.discard(job))
        self._pool.start(job)

    def wait(self) -> None:
        self._pool.waitForDone()
