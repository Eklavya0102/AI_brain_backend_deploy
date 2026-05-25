"""
AI Team Brain — Chat Routes
Fix: Delete channel — only team owner/creator can delete.
Fix: Notifications — emit socket event on creation so frontend gets instant update.
"""

from flask import Blueprint, request, jsonify, g
from models.database import db, ChatRoom, ChatMessage, TeamMember, ActivityLog
from utils.auth_middleware import require_auth
from loguru import logger

chat_bp = Blueprint("chat", __name__)


def _is_team_member(team_id, user_id):
    return TeamMember.query.filter_by(team_id=team_id, user_id=user_id).first()


def _is_team_owner(team_id, user_id):
    return TeamMember.query.filter_by(team_id=team_id, user_id=user_id, role="owner").first()


@chat_bp.route("/teams/<team_id>/rooms", methods=["GET"])
@require_auth
def get_rooms(team_id):
    if not _is_team_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403
    rooms = ChatRoom.query.filter_by(team_id=team_id).all()
    return jsonify({"rooms": [r.to_dict() for r in rooms]})


@chat_bp.route("/teams/<team_id>/rooms", methods=["POST"])
@require_auth
def create_room(team_id):
    if not _is_team_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Channel name required"}), 400

    room = ChatRoom(
        team_id=team_id,
        name=name.lower().replace(" ", "-"),
        description=data.get("description", ""),
        room_type=data.get("roomType", "general"),
        created_by=g.current_user.id,
    )
    db.session.add(room)
    db.session.commit()
    return jsonify({"room": room.to_dict()}), 201


@chat_bp.route("/rooms/<room_id>", methods=["DELETE"])
@require_auth
def delete_room(room_id):
    """Delete a channel — only team owner can do this."""
    room = ChatRoom.query.get_or_404(room_id)

    # Only team owner can delete channels
    if not _is_team_owner(room.team_id, g.current_user.id):
        return jsonify({"error": "Only the team owner can delete channels"}), 403

    # Cannot delete the last channel
    room_count = ChatRoom.query.filter_by(team_id=room.team_id).count()
    if room_count <= 1:
        return jsonify({"error": "Cannot delete the last channel"}), 400

    try:
        ChatMessage.query.filter_by(room_id=room_id).delete()
        db.session.delete(room)
        db.session.commit()
        logger.info(f"Channel #{room.name} deleted by {g.current_user.email}")
        return jsonify({"message": "Channel deleted"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/rooms/<room_id>/messages", methods=["GET"])
@require_auth
def get_messages(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    if not _is_team_member(room.team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    limit  = int(request.args.get("limit", 60))
    before = request.args.get("before")
    query  = ChatMessage.query.filter_by(room_id=room_id)
    if before:
        from datetime import datetime
        try:
            query = query.filter(ChatMessage.created_at < datetime.fromisoformat(before))
        except Exception:
            pass
    messages = query.order_by(ChatMessage.created_at.desc()).limit(limit).all()
    messages.reverse()
    return jsonify({"messages": [m.to_dict() for m in messages]})


@chat_bp.route("/rooms/<room_id>/messages", methods=["POST"])
@require_auth
def send_message(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    if not _is_team_member(room.team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "Message content required"}), 400

    msg = ChatMessage(
        room_id=room_id,
        user_id=g.current_user.id,
        content=content,
        message_type="text",
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"message": msg.to_dict()}), 201


@chat_bp.route("/rooms/<room_id>/catchup", methods=["GET"])
@require_auth
def catch_me_up(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    if not _is_team_member(room.team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    messages = ChatMessage.query.filter_by(room_id=room_id)\
        .order_by(ChatMessage.created_at.desc()).limit(50).all()
    messages.reverse()

    if not messages:
        return jsonify({"summary": "No messages yet in this channel."})

    from services.ai.ai_service import generate_catchup_summary
    try:
        summary = generate_catchup_summary(
            [m.to_dict() for m in messages],
            g.current_user.display_name or "there"
        )
        return jsonify({"summary": summary, "messageCount": len(messages)})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503


@chat_bp.route("/rooms/<room_id>/summarize", methods=["POST"])
@require_auth
def summarize_chat(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    if not _is_team_member(room.team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    messages = ChatMessage.query.filter_by(room_id=room_id)\
        .order_by(ChatMessage.created_at.desc()).limit(100).all()
    messages.reverse()

    chat_text = "\n".join([
        f"{m.user.display_name if m.user else 'User'}: {m.content}"
        for m in messages if m.message_type == "text"
    ])

    from services.ai.ai_service import summarize_content
    try:
        result = summarize_content(chat_text, "team chat conversation")
        return jsonify(result)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
