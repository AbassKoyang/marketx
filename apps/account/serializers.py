from rest_framework import generics, serializers
from .models import User


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'username', 'last_name', 'avatar', 'is_onboarded', 'is_verified', 'is_staff']
        read_only_fields = ('id', 'email', 'is_onboarded', 'is_verified', 'is_staff')