from taskiq_redis import ListQueueBroker

from glowbug.config import get_settings

settings = get_settings()
broker = ListQueueBroker(url=settings.redis_url)
