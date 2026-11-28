from taskiq_redis import ListQueueBroker

from glowbug.config import get_settings

settings = get_settings()
# redis-py 8 defaults to a 5s socket timeout, but Taskiq blocks on BRPOP with
# no timeout. Without disabling it, every worker dies after five idle seconds.
broker = ListQueueBroker(url=settings.redis_url, socket_timeout=None)
