"""Recover interrupted operations through their ordinary exception cleanup.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Final

RECOVERABLE_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
SIGNAL_EXIT_BASE: Final = 128


@contextmanager
def recoverable_signals() -> Iterator[None]:
    """Translate signals to an exception while the caller can clean up.

    Only the main thread can own process handlers. Existing handlers are
    restored at scope exit, including when an interrupt or cleanup fails.
    Further signals after an interrupt cannot interrupt its cleanup.

    :yields: Control while the operation and its cleanup own the handlers.
    :ytype: None
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {signum: signal.getsignal(signum) for signum in RECOVERABLE_SIGNALS}

    def interrupt(signum: int, _frame: FrameType | None) -> None:
        """Raise the usual signal exit code after protecting cleanup.

        :param signum: Signal received by the active operation.
        :param _frame: Interrupted frame supplied by Python.
        :raises SystemExit: Signal-derived exit status after caller cleanup.
        """
        for recoverable in previous:
            signal.signal(recoverable, signal.SIG_IGN)
        raise SystemExit(SIGNAL_EXIT_BASE + signum)

    try:
        for signum in previous:
            signal.signal(signum, interrupt)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
