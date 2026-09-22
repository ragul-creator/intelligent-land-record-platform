"""Celery application configured only for Phase B.2 infrastructure jobs."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "land_record_platform",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    task_acks_late=True,
    broker_connection_retry_on_startup=True,
    task_routes={
        "app.workers.tasks.process_imagery_registration": {"queue": "geoai"},
        "app.workers.tasks.process_geoai_buildings": {"queue": "geoai"},
        "app.workers.tasks.process_geoai_roads": {"queue": "geoai"},
    },
)
