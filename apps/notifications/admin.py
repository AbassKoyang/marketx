from django.contrib import admin

from .models import Notification, UserDevice


@admin.register(UserDevice)
class UserDeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "is_active", "last_seen_at")
    list_filter = ("platform", "is_active")
    search_fields = ("user__email", "token")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "event_type", "title", "is_read", "created_at")
    list_filter = ("event_type", "read_at")
    search_fields = ("user__email", "title", "body")
    readonly_fields = ("created_at",)

    @admin.display(boolean=True, description="Read")
    def is_read(self, obj):
        return obj.read_at is not None
