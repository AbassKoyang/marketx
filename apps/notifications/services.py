from firebase_admin import messaging

from config.firebase_config import initialize_firebase
from .models import UserDevice


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


def send_user_notification(*, user_id, title, body, data=None):
    app = initialize_firebase()
    if app is None:
        return {"sent": 0, "skipped": "firebase_not_configured"}

    tokens = list(
        UserDevice.objects.filter(user_id=user_id, is_active=True)
        .values_list("token", flat=True)
    )
    if not tokens:
        return {"sent": 0}

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data={key: str(value) for key, value in (data or {}).items()},
        tokens=tokens,
    )
    response = messaging.send_each_for_multicast(message)
    return {"sent": response.success_count, "failed": response.failure_count}
