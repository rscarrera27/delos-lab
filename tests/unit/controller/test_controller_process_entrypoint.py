from collections.abc import Coroutine
from typing import Any

import pytest

from delos_lab.controller import process


def test_main_treats_keyboard_interrupt_as_clean_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted(coroutine: Coroutine[Any, Any, None]) -> None:
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(process.asyncio, "run", interrupted)
    monkeypatch.setattr("sys.argv", ["delos-lab"])

    process.main()
