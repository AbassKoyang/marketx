from rest_framework import serializers

from .models import User


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'username', 'last_name', 'avatar', 'is_onboarded', 'is_verified', 'is_staff', 'auth_provider']
        read_only_fields = ('id', 'email', 'is_onboarded', 'is_verified', 'is_staff', 'auth_provider')