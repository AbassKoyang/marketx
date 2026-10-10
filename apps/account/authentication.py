from rest_framework_simplejwt.authentication import JWTAuthentication


class CookieJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        print(f"COOKIES RECEIVED: {request.COOKIES}")
        header = self.get_header(request)
        if header is not None:
            raw_token = self.get_raw_token(header)
        else:
            raw_token = request.COOKIES.get("access_token")

        if raw_token is None:
            print("raw_token is None in CookieJWTAuthentication")
            return None

        try:
            validated_token = self.get_validated_token(raw_token)
            user = self.get_user(validated_token)
            return user, validated_token
        except Exception as e:
            print("Token validation failed:", e)
            return None