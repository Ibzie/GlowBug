import sys


def test_gil_is_disabled() -> None:
    assert sys._is_gil_enabled() is False, "GlowBug must run on the free-threaded CPython 3.14t build."
