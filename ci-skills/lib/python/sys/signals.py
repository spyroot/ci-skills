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
from typing import Final, Optional

RECOVERABLE_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
SIGNAL_EXIT_BASE: Final = 128


@contextmanager
def recoverable_signals() -> Iterator[None]:
    """Turn recoverable process signals into cleanup-safe exits.

    Only the main thread installs handlers. Existing handlers are restored after
    this scope, including when the operation fails.

    :yields: Control while the operation owns temporary signal handlers.
    :raises SystemExit: The main thread receives SIGINT, SIGTERM, or SIGHUP;
        the exit status is 128 plus the signal number.
    :ytype: None
    """  # noqa: DOC502 - Installed signal handler raises SystemExit asynchronously.
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {signum: signal.getsignal(signum) for signum in RECOVERABLE_SIGNALS}

    def interrupt(signum: int, _frame: Optional[FrameType]) -> None:
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
