import logging

from django.db import transaction
from firebase_admin import messaging

from config.firebase_config import initialize_firebase
from .models import Notification, UserDevice

logger = logging.getLogger(__name__)

# Tokens that will never succeed again should be retired so we stop paying for
# failed multicast attempts on every subsequent notification.
INVALID_TOKEN_ERRORS = (
    messaging.UnregisteredError,
    messaging.SenderIdMismatchError,
)


def register_device(*, user, token, platform):
    device, _ = UserDevice.objects.update_or_create(
        token=token,
        defaults={
            "user": user,
            "platform": platform,
            "is_active": True,
        },
    )
    return device


def _stringify_data(data):
    return {
        key: str(value)
        for key, value in (data or {}).items()
        if value is not None
    }


def send_user_notification(*, user_id, title, body, data=None):
    """Send a Firebase push to every active device owned by ``user_id``.

    Returns a small result dict instead of raising so notification delivery can
    never break the business operation that triggered it.
    """
    try:
        app = initialize_firebase()
    except Exception:
        logger.exception("firebase_initialization_failed", extra={"user_id": user_id})
        return {"sent": 0, "failed": 0, "error": "firebase_initialization_failed"}

    if app is None:
        return {"sent": 0, "failed": 0, "skipped": "firebase_not_configured"}

    tokens = list(
        UserDevice.objects.filter(user_id=user_id, is_active=True)
        .values_list("token", flat=True)
    )
    if not tokens:
        return {"sent": 0, "failed": 0, "skipped": "no_devices"}

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=_stringify_data(data),
        tokens=tokens,
    )
    try:
        response = messaging.send_each_for_multicast(message)
    except Exception:
        logger.exception(
            "push_notification_failed",
            extra={"user_id": user_id, "token_count": len(tokens)},
        )
        return {"sent": 0, "failed": len(tokens), "error": "send_failed"}

    invalid_tokens = []
    for token, result in zip(tokens, response.responses):
        if result.success:
            continue
        if isinstance(result.exception, INVALID_TOKEN_ERRORS):
            invalid_tokens.append(token)
        else:
            logger.warning(
                "push_notification_rejected",
                extra={
                    "user_id": user_id,
                    "error": str(result.exception),
                },
            )

    if invalid_tokens:
        UserDevice.objects.filter(token__in=invalid_tokens).update(is_active=False)
        logger.info(
            "push_tokens_deactivated",
            extra={"user_id": user_id, "token_count": len(invalid_tokens)},
        )

    return {"sent": response.success_count, "failed": response.failure_count}


def _dispatch_push(*, notification_id, user_id, event_type, title, body, data):
    payload = _stringify_data(data)
    payload["event_type"] = event_type
    if notification_id is not None:
        payload["notification_id"] = str(notification_id)
    try:
        send_user_notification(
            user_id=user_id,
            title=title,
            body=body,
            data=payload,
        )
    except Exception:
        # on_commit callbacks run after the database commit; an exception here
        # must not surface as a failure of the committed business operation.
        logger.exception(
            "notification_push_dispatch_failed",
            extra={"user_id": user_id, "event_type": event_type},
        )


def _schedule_push(notification):
    transaction.on_commit(
        lambda: _dispatch_push(
            notification_id=notification.id,
            user_id=notification.user_id,
            event_type=notification.event_type,
            title=notification.title,
            body=notification.body,
            data=notification.data,
        )
    )


def notify_user(*, user, event_type, title, body, data=None):
    """Persist an in-app notification and schedule its push delivery."""
    notification = Notification.objects.create(
        user=user,
        event_type=event_type,
        title=title,
        body=body,
        data=data or {},
    )
    _schedule_push(notification)
    return notification


def notify_users(*, user_ids, event_type, title, body, data=None):
    """Persist one in-app notification per user and schedule push delivery."""
    unique_user_ids = list(dict.fromkeys(user_ids))
    if not unique_user_ids:
        return []

    notifications = Notification.objects.bulk_create(
        [
            Notification(
                user_id=user_id,
                event_type=event_type,
                title=title,
                body=body,
                data=data or {},
            )
            for user_id in unique_user_ids
        ]
    )
    for notification in notifications:
        _schedule_push(notification)
    return notifications
