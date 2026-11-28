from glowbug.jobs.broker import broker


def test_broker_disables_redis_socket_timeout() -> None:
    # Taskiq blocks on BRPOP indefinitely. redis-py 8 defaults socket_timeout
    # to five seconds, which would kill idle workers.
    assert broker.connection_pool.connection_kwargs.get("socket_timeout") is None
