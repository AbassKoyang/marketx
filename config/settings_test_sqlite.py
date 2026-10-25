"""Test settings: isolated in-memory SQLite instead of the configured database."""

from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Static-file serving is irrelevant to tests and whitenoise is an optional
# production dependency, so do not require it when running the test client.
MIDDLEWARE = [
    middleware for middleware in MIDDLEWARE if "whitenoise" not in middleware.lower()
]

# Keep test throttles isolated from the configured Redis cache, and reset them
# for every test process.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "marketx-tests",
    }
}
