"""
AI Team Brain — Socket.IO Events
Fix: join_team_room so task broadcasts reach all team members.
Fix: join_user_room for personal notifications.

SECURITY FIX: every handler now trusts only the identity established by a
verified JWT at connect time, and checks real team/room membership before
letting a client join a room, read a room's live traffic, or post a message.
Previously every handler here trusted whatever roomId/teamId/userId the
client happened to send with zero verification, meaning any socket client
could read or spoof any team's chat, task broadcasts, and personal
notifications regardless of the REST-API's own auth checks. This mirrors the
same require_auth pattern already used on the REST side
(utils/auth_middleware.py).
"""

from flask import request
from flask_socketio import join_room, leave_room, emit, ConnectionRefusedError
from flask_jwt_extended import decode_token
from loguru import logger
from models.database import db, ChatMessage, ChatRoom, TeamMember, User

# Maps this process's live Socket.IO connection id (request.sid) -> the
# user_id that connection proved ownership of via a valid JWT at connect
# time. Populated once on a verified `connect`, cleared on `disconnect`.
# In-process state — consistent with this app's current single-process
# Socket.IO deployment (see production-readiness audit §8 for the separate
# follow-up needed before running more than one worker/process).
_socket_users = {}


def _authenticated_user_id():
    """The verified user_id for the CURRENT socket connection, or None."""
    return _socket_users.get(request.sid)


def _is_team_member(team_id, user_id):
    if not team_id or not user_id:
        return False
    return TeamMember.query.filter_by(team_id=team_id, user_id=user_id).first() is not None


def register_socket_events(socketio):

    @socketio.on("connect")
    def on_connect(auth):
        """Verify the JWT the client sends at connect time. Reject the
        connection outright if it's missing, invalid/expired, or belongs to
        a user that no longer exists or is inactive."""
        token = (auth or {}).get("token") if isinstance(auth, dict) else None
        if not token:
            logger.warning("Socket connect rejected: no auth token provided")
            raise ConnectionRefusedError("Authentication required")

        try:
            decoded = decode_token(token)
            user_id = decoded["sub"]
        except Exception as e:
            logger.warning(f"Socket connect rejected: invalid token ({e})")
            raise ConnectionRefusedError("Invalid or expired token")

        user = User.query.get(user_id)
        if not user or not user.is_active:
            logger.warning(f"Socket connect rejected: unknown/inactive user {user_id}")
            raise ConnectionRefusedError("Account not found or inactive")

        _socket_users[request.sid] = user_id
        logger.debug(f"Socket connected & authenticated: user {user_id}")

    @socketio.on("disconnect")
    def on_disconnect():
        _socket_users.pop(request.sid, None)
        logger.debug("Client disconnected")

    @socketio.on("join_room")
    def on_join(data):
        """Join a chat room's live broadcast group — only if the caller is
        an authenticated member of that room's team."""
        user_id = _authenticated_user_id()
        room_id = (data or {}).get("roomId")
        if not user_id or not room_id:
            return

        room = ChatRoom.query.get(room_id)
        if not room or not _is_team_member(room.team_id, user_id):
            logger.warning(f"Blocked join_room: user {user_id} -> room {room_id}")
            return

        join_room(room_id)
        logger.debug(f"User {user_id} joined chat room {room_id}")

    @socketio.on("leave_room")
    def on_leave(data):
        room_id = (data or {}).get("roomId")
        if room_id:
            leave_room(room_id)

    @socketio.on("join_user_room")
    def on_join_user_room(data):
        """Personal room for push notifications. A connection may only ever
        join ITS OWN personal room — the requested userId in `data` is
        ignored on purpose, the authenticated identity always wins."""
        user_id = _authenticated_user_id()
        if not user_id:
            return
        join_room(f"user_{user_id}")
        logger.debug(f"User {user_id} joined personal room")

    @socketio.on("join_team_room")
    def on_join_team_room(data):
        """Team room for task/notification broadcasts — only if the caller
        is an authenticated member of that team."""
        user_id = _authenticated_user_id()
        team_id = (data or {}).get("teamId")
        if not user_id or not team_id:
            return
        if not _is_team_member(team_id, user_id):
            logger.warning(f"Blocked join_team_room: user {user_id} -> team {team_id}")
            return
        join_room(f"team_{team_id}")
        logger.debug(f"User {user_id} joined team room {team_id}")

    @socketio.on("leave_team_room")
    def on_leave_team_room(data):
        team_id = (data or {}).get("teamId")
        if team_id:
            leave_room(f"team_{team_id}")

    @socketio.on("send_message")
    def on_message(data):
        """Post a chat message. The sender is always the authenticated
        socket identity — never the client-supplied userId — and the caller
        must be a member of the target room's team."""
        user_id = _authenticated_user_id()
        room_id = (data or {}).get("roomId")
        content = (data or {}).get("content", "").strip()
        temp_id = (data or {}).get("tempId")

        if not user_id or not room_id or not content:
            return

        room = ChatRoom.query.get(room_id)
        if not room or not _is_team_member(room.team_id, user_id):
            logger.warning(f"Blocked send_message: user {user_id} -> room {room_id}")
            emit("message_error", {"error": "Access denied", "tempId": temp_id})
            return

        try:
            msg = ChatMessage(room_id=room_id, user_id=user_id, content=content, message_type="text")
            db.session.add(msg)
            db.session.commit()
            msg_dict = msg.to_dict()
            if temp_id:
                msg_dict["tempId"] = temp_id
            emit("new_message", msg_dict, to=room_id)
        except Exception as e:
            db.session.rollback()
            logger.error(f"Socket message error: {e}")
            emit("message_error", {"error": "Failed to send message", "tempId": temp_id})

    @socketio.on("typing")
    def on_typing(data):
        """Typing indicator — verified membership required, and the display
        name comes from the authenticated user's own DB record rather than
        whatever userName the client claims."""
        user_id   = _authenticated_user_id()
        room_id   = (data or {}).get("roomId")
        is_typing = (data or {}).get("isTyping", False)
        if not user_id or not room_id:
            return

        room = ChatRoom.query.get(room_id)
        if not room or not _is_team_member(room.team_id, user_id):
            return

        user = User.query.get(user_id)
        user_name = user.display_name if user else "Someone"
        emit("user_typing", {"userId": user_id, "userName": user_name, "isTyping": is_typing},
             to=room_id, include_self=False)
