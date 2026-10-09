import firebase_admin
from firebase_admin import credentials
from django.conf import settings
import os

_firebase_app = None


def initialize_firebase():
    """
    Initialize Firebase Admin SDK.
    Call this in your Django app's ready() method or in settings.
    """
    global _firebase_app
    
    if _firebase_app is None:
        try:
            if hasattr(settings, 'FIREBASE_CREDENTIALS_PATH'):
                cred_path = settings.FIREBASE_CREDENTIALS_PATH
                if os.path.exists(cred_path):
                    cred = credentials.Certificate(cred_path)
                    _firebase_app = firebase_admin.initialize_app(cred)
                else:
                    print("firebase_credentials_file_missing", cred_path=cred_path)
            
            elif hasattr(settings, 'FIREBASE_CREDENTIALS_JSON'):
                import json
                cred_info = json.loads(settings.FIREBASE_CREDENTIALS_JSON)
                cred = credentials.Certificate(cred_info)
                _firebase_app = firebase_admin.initialize_app(cred)
            
            else:
                print("firebase_credentials_not_configured")
                
        except Exception as e:
            print("firebase_initialization_failed")
    
    return _firebase_app