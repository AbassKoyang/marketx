web: python -m gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker --timeout 90
worker: celery -A config worker -l info --concurrency=1