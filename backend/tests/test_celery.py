from app.workers.celery_app import celery_app


def test_celery_uses_configured_redis_for_broker_and_result_backend() -> None:
    assert celery_app.conf.broker_url == "redis://redis:6379/0"
    assert celery_app.conf.result_backend == "redis://redis:6379/0"
    assert celery_app.conf.task_acks_late is True
