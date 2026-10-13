from django.urls import path
from . import views

urlpatterns = [
    path('auth/login/', views.CustomTokenObtainPairView.as_view(), name='token-obtain-pair'),
    path('auth/google/', views.google_login, name='google-login'),
    path('auth/refresh/', views.CustomTokenRefreshView.as_view(), name='token-refresh'),
    path('auth/logout/', views.logout, name='logout'),
    path('auth/me/', views.MeView.as_view(), name='me'),
    path('auth/update/<int:id>/', views.UpdateUser.as_view(), name='update-user'),
    path('auth/onboard/', views.OnboardingView.as_view(), name='onboard'),
]
