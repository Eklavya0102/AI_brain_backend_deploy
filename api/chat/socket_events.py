"""
AI Team Brain — Socket.IO Events
Fix: join_team_room so task broadcasts reach all team members.
Fix: join_user_room for personal notifications.
"""

from loguru import logger
from models.database import db, ChatMessage


def register_socket_events(socketio):

    @socketio.on("connect")
    def on_connect():
        logger.debug("Client connected")

    @socketio.on("disconnect")
    def on_disconnect():
        logger.debug("Client disconnected")

    @socketio.on("join_room")
    def on_join(data):
        from flask_socketio import join_room
        room_id = data.get("roomId")
        user_id = data.get("userId")
        if room_id:
            join_room(room_id)
            logger.debug(f"User {user_id} joined chat room {room_id}")

    @socketio.on("leave_room")
    def on_leave(data):
        from flask_socketio import leave_room
        room_id = data.get("roomId")
        if room_id:
            leave_room(room_id)

    @socketio.on("join_user_room")
    def on_join_user_room(data):
        """Personal room for push notifications."""
        from flask_socketio import join_room
        user_id = data.get("userId")
        if user_id:
            join_room(f"user_{user_id}")
            logger.debug(f"User {user_id} joined personal room")

    @socketio.on("join_team_room")
    def on_join_team_room(data):
        """Team room for task broadcasts (new_task, task_updated, task_deleted)."""
        from flask_socketio import join_room
        team_id = data.get("teamId")
        user_id = data.get("userId")
        if team_id:
            join_room(f"team_{team_id}")
            logger.debug(f"User {user_id} joined team room {team_id}")

    @socketio.on("leave_team_room")
    def on_leave_team_room(data):
        from flask_socketio import leave_room
        team_id = data.get("teamId")
        if team_id:
            leave_room(f"team_{team_id}")

    @socketio.on("send_message")
    def on_message(data):
        from flask_socketio import emit
        room_id = data.get("roomId")
        user_id = data.get("userId")
        content = data.get("content", "").strip()
        temp_id = data.get("tempId")

        if not room_id or not user_id or not content:
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
            logger.error(f"Socket message error: {e}")
            emit("message_error", {"error": str(e), "tempId": temp_id})

    @socketio.on("typing")
    def on_typing(data):
        from flask_socketio import emit
        room_id   = data.get("roomId")
        user_id   = data.get("userId")
        user_name = data.get("userName", "Someone")
        is_typing = data.get("isTyping", False)
        if room_id:
            emit("user_typing", {"userId": user_id, "userName": user_name, "isTyping": is_typing},
                 to=room_id, include_self=False)
