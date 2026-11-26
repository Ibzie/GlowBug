from concurrent.futures import ThreadPoolExecutor


def square(value: int) -> int:
    return value * value


def test_thread_pool_runs() -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(square, range(1000))) == [square(value) for value in range(1000)]
