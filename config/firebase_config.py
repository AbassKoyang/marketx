import json
import logging
import os

import firebase_admin
from django.conf import settings
from firebase_admin import credentials

logger = logging.getLogger(__name__)

_firebase_app = None


def _load_credentials():
    candidate_paths = []
    cred_path = getattr(settings, "FIREBASE_CREDENTIALS_PATH", None)
    if cred_path:
        candidate_paths.append(cred_path)

    # Support both the documented root location and the bundled ``config/``
    # location used by local development. Both paths are gitignored.
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir:
        candidate_paths.extend(
            [
                os.path.join(base_dir, "firebase-credentials.json"),
                os.path.join(base_dir, "config", "firebase-credentials.json"),
            ]
        )

    for path in dict.fromkeys(candidate_paths):
        if not os.path.exists(path):
            continue
        try:
            return credentials.Certificate(path)
        except Exception:
            logger.warning(
                "firebase_credentials_file_invalid",
                extra={"path": path},
            )

    cred_json = getattr(settings, "FIREBASE_CREDENTIALS_JSON", None)
    if cred_json:
        cred_info = json.loads(cred_json) if isinstance(cred_json, str) else cred_json
        return credentials.Certificate(cred_info)

    return None


def initialize_firebase():
    """Initialize the Firebase Admin SDK once, or return ``None``.

    Credentials may be supplied either as a file path or as a JSON object in
    ``FIREBASE_CREDENTIALS_JSON``. Missing credentials are not an error: push
    delivery is optional, and callers degrade gracefully.
    """
    global _firebase_app

    if _firebase_app is not None:
        return _firebase_app

    try:
        cred = _load_credentials()
        if cred is None:
            logger.info("firebase_credentials_not_configured")
            return None

        try:
            _firebase_app = firebase_admin.initialize_app(cred)
        except ValueError:
            # The default app was already initialized elsewhere in the process.
            _firebase_app = firebase_admin.get_app()
    except Exception:
        logger.exception("firebase_initialization_failed")

    return _firebase_app
