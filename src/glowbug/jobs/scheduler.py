from taskiq.schedule_sources import LabelScheduleSource
from taskiq.scheduler.scheduler import TaskiqScheduler

from glowbug.jobs.broker import broker

scheduler = TaskiqScheduler(broker, sources=[LabelScheduleSource(broker)])
