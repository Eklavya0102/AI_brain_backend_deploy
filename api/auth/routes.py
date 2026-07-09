"""
AI Team Brain — Auth Routes
Fix 2: Added delete team endpoint for team creator.
"""

import os
import random
import string
from datetime import datetime
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity
from loguru import logger
from models.database import db, User, Team, TeamMember, ActivityLog, ChatRoom, Task, KnowledgeItem, Notification, ChatMessage, DailySummary
from utils.auth_middleware import require_auth, verify_firebase_token

auth_bp = Blueprint("auth", __name__)


def _generate_invite_code(length=8):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


# ── Firebase token exchange ────────────────────────────────────

@auth_bp.route("/firebase-login", methods=["POST"])
def firebase_login():
    data         = request.get_json() or {}
    id_token     = data.get("idToken", "")
    email        = data.get("email", "")
    display_name = data.get("displayName", "")
    firebase_uid = None
    avatar_url   = ""

    # Try real Firebase verification first
    try:
        decoded      = verify_firebase_token(id_token)
        firebase_uid = decoded["uid"]
        email        = decoded.get("email", email)
        display_name = decoded.get("name", display_name) or email.split("@")[0]
        avatar_url   = decoded.get("picture", "")
        logger.info(f"Firebase auth OK: {email}")
    except Exception as firebase_err:
        # Dev mode fallback — reachable ONLY when the server itself is running
        # in development mode (FLASK_ENV=development, the local default).
        #
        # SECURITY FIX: this used to also accept any client-supplied idToken
        # starting with "mock_", regardless of FLASK_ENV. That meant anyone,
        # in any environment including production, could authenticate as an
        # arbitrary email address just by sending {"idToken": "mock_x", ...}
        # with no real credentials. The client can no longer opt itself into
        # the dev-mode fallback — only the server's own environment can.
        is_dev = os.getenv("FLASK_ENV", "development") == "development"
        if is_dev:
            if not email:
                return jsonify({"error": "Email required"}), 400
            firebase_uid = f"dev_{email.replace('@','_').replace('.','_')}"
            display_name = display_name or email.split("@")[0]
            logger.warning(f"Dev-mode login: {email}")
        else:
            logger.warning(
                f"Rejected unverifiable Firebase token outside dev mode: {firebase_err}"
            )
            return jsonify({"error": "Invalid Firebase token"}), 401

    # Upsert user
    user = User.query.filter_by(firebase_uid=firebase_uid).first()
    if not user:
        user = User(
            firebase_uid=firebase_uid,
            email=email,
            display_name=display_name or email.split("@")[0],
            avatar_url=avatar_url,
        )
        db.session.add(user)
        logger.info(f"New user: {email}")
    else:
        user.last_seen    = datetime.utcnow()
        user.display_name = display_name or user.display_name
        if avatar_url:
            user.avatar_url = avatar_url

    db.session.commit()
    access_token  = create_access_token(identity=user.id)
    refresh_token = create_refresh_token(identity=user.id)
    return jsonify({"accessToken": access_token, "refreshToken": refresh_token, "user": user.to_dict()})


# ── Token refresh ───────────────────────────────────────────────
# Access tokens are short-lived (see app.py JWT_ACCESS_TOKEN_EXPIRES). This
# endpoint lets the frontend silently exchange a still-valid refresh token
# for a new access token instead of forcing the user to log in again every
# time the access token expires. Requires a REFRESH token specifically
# (jwt_required(refresh=True)) — an access token cannot be used here, and
# a refresh token cannot be used on any other endpoint (require_auth calls
# verify_jwt_in_request() with its default refresh=False).

@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user or not user.is_active:
        return jsonify({"error": "User not found or inactive"}), 401
    new_access_token = create_access_token(identity=user_id)
    return jsonify({"accessToken": new_access_token})


# ── Profile ───────────────────────────────────────────────────

@auth_bp.route("/me", methods=["GET"])
@require_auth
def get_profile():
    return jsonify({"user": g.current_user.to_dict()})


@auth_bp.route("/me", methods=["PUT"])
@require_auth
def update_profile():
    data = request.get_json() or {}
    user = g.current_user
    if "displayName" in data:
        user.display_name = data["displayName"]
    if "avatarUrl" in data:
        user.avatar_url = data["avatarUrl"]
    db.session.commit()
    return jsonify({"user": user.to_dict()})


# ── Teams ─────────────────────────────────────────────────────

@auth_bp.route("/teams", methods=["GET"])
@require_auth
def get_my_teams():
    memberships = TeamMember.query.filter_by(user_id=g.current_user.id).all()
    return jsonify({"teams": [m.team.to_dict() for m in memberships if m.team]})


@auth_bp.route("/teams", methods=["POST"])
@require_auth
def create_team():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Team name required"}), 400

    team = Team(
        name=name,
        description=data.get("description", ""),
        invite_code=_generate_invite_code(),
        created_by=g.current_user.id,
    )
    db.session.add(team)
    db.session.flush()

    db.session.add(TeamMember(team_id=team.id, user_id=g.current_user.id, role="owner"))
    db.session.add(ChatRoom(
        team_id=team.id, name="general",
        description="General discussion", room_type="general",
        created_by=g.current_user.id,
    ))
    db.session.add(ActivityLog(
        team_id=team.id, user_id=g.current_user.id,
        action="team_created", entity_type="team", entity_id=team.id,
        description=f"{g.current_user.display_name} created the team",
    ))
    db.session.commit()
    logger.info(f"Team '{name}' created by {g.current_user.email}")
    return jsonify({"team": team.to_dict(include_members=True)}), 201


@auth_bp.route("/teams/join", methods=["POST"])
@require_auth
def join_team():
    data = request.get_json() or {}
    code = data.get("inviteCode", "").strip().upper()
    team = Team.query.filter_by(invite_code=code).first()
    if not team:
        return jsonify({"error": "Invalid invite code"}), 404
    if TeamMember.query.filter_by(team_id=team.id, user_id=g.current_user.id).first():
        return jsonify({"error": "Already a member"}), 409

    db.session.add(TeamMember(team_id=team.id, user_id=g.current_user.id, role="member"))
    db.session.add(ActivityLog(
        team_id=team.id, user_id=g.current_user.id,
        action="member_joined", entity_type="team", entity_id=team.id,
        description=f"{g.current_user.display_name} joined the team",
    ))
    db.session.commit()
    return jsonify({"team": team.to_dict(include_members=True)})


@auth_bp.route("/teams/<team_id>", methods=["GET"])
@require_auth
def get_team(team_id):
    member = TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first()
    if not member:
        return jsonify({"error": "Access denied"}), 403
    return jsonify({"team": member.team.to_dict(include_members=True)})


@auth_bp.route("/teams/<team_id>/members", methods=["GET"])
@require_auth
def get_team_members(team_id):
    if not TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first():
        return jsonify({"error": "Access denied"}), 403
    members = TeamMember.query.filter_by(team_id=team_id).all()
    return jsonify({"members": [m.to_dict() for m in members]})


@auth_bp.route("/teams/<team_id>/invite-code", methods=["POST"])
@require_auth
def regenerate_invite(team_id):
    member = TeamMember.query.filter_by(
        team_id=team_id, user_id=g.current_user.id, role="owner"
    ).first()
    if not member:
        return jsonify({"error": "Only owner can regenerate invite code"}), 403
    team = Team.query.get(team_id)
    team.invite_code = _generate_invite_code()
    db.session.commit()
    return jsonify({"inviteCode": team.invite_code})


# ── FIX 2: Delete team (owner only) ──────────────────────────

@auth_bp.route("/teams/<team_id>", methods=["DELETE"])
@require_auth
def delete_team(team_id):
    """Delete a team and all its data. Only the team owner can do this."""
    member = TeamMember.query.filter_by(
        team_id=team_id, user_id=g.current_user.id, role="owner"
    ).first()
    if not member:
        return jsonify({"error": "Only the team owner can delete this team"}), 403

    team = Team.query.get(team_id)
    if not team:
        return jsonify({"error": "Team not found"}), 404

    try:
        # Delete all related data in correct order (foreign key constraints)
        # 1. Notifications
        Notification.query.filter_by(team_id=team_id).delete()

        # 2. Daily summaries
        DailySummary.query.filter_by(team_id=team_id).delete()

        # 3. Activity logs
        ActivityLog.query.filter_by(team_id=team_id).delete()

        # 4. Tasks
        Task.query.filter_by(team_id=team_id).delete()

        # 5. Knowledge items
        KnowledgeItem.query.filter_by(team_id=team_id).delete()

        # 6. Chat messages (via rooms)
        rooms = ChatRoom.query.filter_by(team_id=team_id).all()
        for room in rooms:
            ChatMessage.query.filter_by(room_id=room.id).delete()
        ChatRoom.query.filter_by(team_id=team_id).delete()

        # 7. Team members
        TeamMember.query.filter_by(team_id=team_id).delete()

        # 8. Team itself
        db.session.delete(team)
        db.session.commit()

        logger.info(f"Team '{team.name}' deleted by {g.current_user.email}")
        return jsonify({"message": "Team deleted successfully"})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Team deletion failed: {e}")
        return jsonify({"error": "Failed to delete team"}), 500
