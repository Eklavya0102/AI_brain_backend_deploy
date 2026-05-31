"""
TeamPulse — Auth Middleware
=================================
Firebase token verification + JWT decorators.
Falls back to dev-mode mock auth when Firebase is not configured.
"""

import os
from functools import wraps
from flask import request, jsonify, g
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from loguru import logger

_firebase_ready = False


def init_firebase():
    global _firebase_ready
    try:
        import firebase_admin
        from firebase_admin import credentials

        if firebase_admin._apps:
            _firebase_ready = True
            return True

        service_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "./firebase-service-account.json")

        if os.path.exists(service_path):
            cred = credentials.Certificate(service_path)
            firebase_admin.initialize_app(cred)
            _firebase_ready = True
            logger.info("✅ Firebase Admin initialized from service account file")
            return True

        # Build from env vars
        project_id = os.getenv("FIREBASE_PROJECT_ID")
        private_key = os.getenv("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n")
        client_email = os.getenv("FIREBASE_CLIENT_EMAIL")

        if project_id and private_key and client_email:
            sa = {
                "type": "service_account",
                "project_id": project_id,
                "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID", ""),
                "private_key": private_key,
                "client_email": client_email,
                "client_id": os.getenv("FIREBASE_CLIENT_ID", ""),
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            cred = credentials.Certificate(sa)
            firebase_admin.initialize_app(cred)
            _firebase_ready = True
            logger.info("✅ Firebase Admin initialized from env vars")
            return True

        logger.warning("⚠️  Firebase Admin not configured — running in dev mode (mock auth enabled)")
        return False

    except Exception as e:
        logger.warning(f"⚠️  Firebase Admin init failed: {e} — dev mode active")
        return False


def verify_firebase_token(id_token: str) -> dict:
    """Verify Firebase ID token. Returns decoded claims dict."""
    if not _firebase_ready:
        raise ValueError("Firebase not configured")
    try:
        from firebase_admin import auth
        return auth.verify_id_token(id_token)
    except Exception as e:
        raise ValueError(f"Invalid Firebase token: {e}")


def require_auth(f):
    """Decorator: require valid JWT."""
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
            user_id = get_jwt_identity()
            from models.database import User
            user = User.query.get(user_id)
            if not user or not user.is_active:
                return jsonify({"error": "User not found or inactive"}), 401
            g.current_user = user
            return f(*args, **kwargs)
        except Exception as e:
            return jsonify({"error": "Unauthorized", "message": str(e)}), 401
    return decorated


def require_team_member(f):
    """Decorator: require valid JWT + team membership."""
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
            user_id = get_jwt_identity()
            from models.database import User, TeamMember
            user = User.query.get(user_id)
            if not user:
                return jsonify({"error": "Unauthorized"}), 401
            g.current_user = user

            team_id = kwargs.get("team_id")
            if team_id:
                member = TeamMember.query.filter_by(team_id=team_id, user_id=user_id).first()
                if not member:
                    return jsonify({"error": "Not a team member"}), 403
                g.team_member = member

            return f(*args, **kwargs)
        except Exception as e:
            return jsonify({"error": "Unauthorized"}), 401
    return decorated
