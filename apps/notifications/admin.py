from django.contrib import admin

from .models import UserDevice


@admin.register(UserDevice)
class UserDeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "is_active", "last_seen_at")
    list_filter = ("platform", "is_active")
    search_fields = ("user__email", "token")
