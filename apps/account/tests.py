from unittest.mock import patch
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

class AuthenticationTests(APITestCase):
    def setUp(self):
        self.google_login_url = reverse('google-login')
        self.me_url = reverse('me')
        self.logout_url = reverse('logout')
        self.refresh_url = reverse('token-refresh')

        # Create a test user
        self.user = User.objects.create(
            email='testuser@example.com',
            first_name='Test',
            last_name='User',
            auth_provider='google'
        )

    def get_tokens_for_user(self, user):
        refresh = RefreshToken.for_user(user)
        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }

    @patch('apps.account.views.id_token.verify_oauth2_token')
    def test_google_login_success(self, mock_verify):
        # Mock the Google token verification response
        mock_verify.return_value = {
            'email': 'newuser@example.com',
            'given_name': 'New',
            'family_name': 'User',
            'picture': 'http://example.com/pic.jpg'
        }

        data = {'token': 'dummy_google_token'}
        response = self.client.post(self.google_login_url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['status'])
        
        # Check if user was created
        self.assertTrue(User.objects.filter(email='newuser@example.com').exists())

        # Check if cookies are set
        self.assertIn('access_token', response.cookies)
        self.assertIn('refresh_token', response.cookies)

    def test_google_login_missing_token(self):
        response = self.client.post(self.google_login_url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('error'), 'Token not provided')

    @patch('apps.account.views.id_token.verify_oauth2_token')
    def test_google_login_invalid_token(self, mock_verify):
        mock_verify.side_effect = ValueError("Invalid token")

        data = {'token': 'invalid_token'}
        response = self.client.post(self.google_login_url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('error'), 'Invalid token')

    def test_get_me_success(self):
        # Authenticate via cookie
        tokens = self.get_tokens_for_user(self.user)
        self.client.cookies['access_token'] = tokens['access']

        response = self.client.get(self.me_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['email'], self.user.email)

    def test_get_me_unauthorized(self):
        # No cookies
        response = self.client.get(self.me_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_refresh_success(self):
        tokens = self.get_tokens_for_user(self.user)
        self.client.cookies['refresh_token'] = tokens['refresh']

        response = self.client.post(self.refresh_url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Should return new cookies
        self.assertIn('access_token', response.cookies)
        self.assertIn('refresh_token', response.cookies)

    def test_token_refresh_no_cookie(self):
        response = self.client.post(self.refresh_url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout(self):
        # Even without tokens, logout clears cookies
        self.client.cookies['access_token'] = 'some_token'
        self.client.cookies['refresh_token'] = 'some_refresh_token'

        response = self.client.post(self.logout_url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Cookies should be deleted (max-age=0 or expires in past)
        self.assertEqual(response.cookies['access_token'].value, '')
        self.assertEqual(response.cookies['refresh_token'].value, '')
