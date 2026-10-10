from celery import Celery

from app.config import get_settings

settings = get_settings()
celery_app = Celery("olx_hunter", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={
        "schedule-due-searches": {
            "task": "app.jobs.tasks.schedule_due_searches",
            "schedule": 60.0,
        },
        "deliver-notifications": {
            "task": "app.jobs.tasks.deliver_notifications",
            "schedule": 10.0,
        },
    },
    imports=("app.jobs.tasks",),
)
