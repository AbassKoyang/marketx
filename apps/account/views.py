from django.shortcuts import render
from django.conf import settings
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User



@api_view(["POST"])
@permission_classes([AllowAny])
def google_login(request):
    token = request.data.get("token")
    if not token:
        print("user_login_failed", "missing_google_token")
        return Response({"error": "Token not provided", "status": False}, status=status.HTTP_400_BAD_REQUEST)
    try:
        id_info = id_token.verify_oauth2_token(
            token, 
            google_requests.Request(), 
            settings.GOOGLE_OAUTH_CLIENT_ID
        )   
        email = id_info['email']
        first_name = id_info.get('given_name', '')
        last_name = id_info.get('family_name', '')
        profile_pic_url = id_info.get('picture', '')
        user, created = User.objects.get_or_create(email=email)
        if created:
            user.set_unusable_password()
            user.first_name = first_name
            user.last_name = last_name
            user.auth_provider = 'google'
            user.avatar = profile_pic_url
            user.save()
            print("user_registered", user.id, email, "google")
        else:
            if user.auth_provider != 'google':
                print("user_login_failed", email, "provider_mismatch")
                return Response({
                    "error": "User needs to sign in through email",
                    "status": False
                }, status=status.HTTP_403_FORBIDDEN)

        print("user_login_success", user.id, user.email, "google")
        refresh = RefreshToken.for_user(user)

        response = Response({
            "status": True,
            "message": "Google login successful"
        })

        response.set_cookie(
            key="access_token",
            value=str(refresh.access_token),
            httponly=True,
            secure=True,
            samesite="None",
            max_age=60 * 30,
            path='/'
        )

        response.set_cookie(
            key="refresh_token",
            value=str(refresh),
            httponly=True,
            secure=True,
            samesite="None",
            max_age=(60 * 60 * 24) * 7,
            path='/'
        )

        return response
    except ValueError:
        print("user_login_failed", "invalid_google_token")
        return Response({"error": "Invalid token", "status": False}, status=status.HTTP_400_BAD_REQUEST)