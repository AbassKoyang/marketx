from django.shortcuts import render
from django.conf import settings
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .models import User
from .permissions import IsProfileOwner
from .serializers import UserProfileSerializer
from apps.wallet.services import get_or_create_wallet



from rest_framework.decorators import api_view, permission_classes, authentication_classes

@api_view(["POST"])
@authentication_classes([])
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
            get_or_create_wallet(user, 10000)
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

@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def logout(request):
    response = Response({
        "message": "Logout successful",
        "status": True
    })

    response.delete_cookie(key="access_token",
                           samesite="None",
                           path='/')
    response.delete_cookie(key="refresh_token",
                           samesite="None",
                           path='/')
    return response

class ListUsersView(generics.ListAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return User.objects.all()

class UpdateUser(generics.UpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated, IsProfileOwner]
    lookup_field = 'pk'
    lookup_url_kwarg = 'id'

    def perform_update(self, serializer):
        serializer.save()

class RetrieveUser(generics.RetrieveAPIView):
    queryset = User.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [AllowAny]

    lookup_field = 'pk'
    lookup_url_kwarg='id'

class DeleteUser(generics.DestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated, IsProfileOwner]

    lookup_field = 'pk'
    lookup_url_kwarg='id'


class MeView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    def get(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)

class OnboardingView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    def post(self, request):
        serializer = self.get_serializer(request.user, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

class CustomTokenObtainPairView(TokenObtainPairView):
    authentication_classes = ()
    permission_classes = [AllowAny]
    throttle_scope = "auth_actions"

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            access = response.data["access"]
            refresh = response.data["refresh"]

            response.set_cookie(
                key="access_token",
                value=access,
                httponly=True,
                secure=True,
                samesite="None",
                max_age=60 * 30,
                path='/'
            )

            response.set_cookie(
                key="refresh_token",
                value=refresh,
                httponly=True,
                secure=True,
                samesite="None",
                max_age=(60 * 60 * 24) * 7,
                path='/'
            )

            response.data = {
                "status": True,
                "message": "Token obtained successfully"
            }

            print("Token obtain success", response.data)

            return response
        else:
            print("Token obtain failed", response.data)
            return response

class CustomTokenRefreshView(TokenRefreshView):
    authentication_classes = ()
    permission_classes = [AllowAny]
    throttle_scope = "auth_actions"

    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get("refresh_token")

        if not refresh_token:
            print("Token refresh failed")
            return Response({"detail": "No refresh token"}, status=401)

        request.data["refresh"] = refresh_token
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            access = response.data["access"]
            refresh = response.data["refresh"]

            response.set_cookie(
                key="access_token",
                value=access,
                httponly=True,
                secure=True,
                samesite="None",
                max_age=60 * 30,
                path='/'
            )

            response.set_cookie(
                key="refresh_token",
                value=refresh,
                httponly=True,
                secure=True,
                samesite="None",
                max_age=(60 * 60 * 24) * 7,
                path='/'
            )

            response.data = {
                "status": True,
                "message": "Token refreshed successfully"
            }

            print("Token refresh success", response.data)

            return response
        else:
            print("Token refresh failed", response.data)
            return response
        
    
