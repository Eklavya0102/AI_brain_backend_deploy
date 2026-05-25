"""
AI Team Brain — AI API Routes
Fix 1: Proper error messages when AI providers fail.
"""

from flask import Blueprint, request, jsonify, g
from utils.auth_middleware import require_auth
from models.database import TeamMember
from services.ai.ai_service import (
    extract_tasks_from_text, summarize_content,
    recommend_task_priority, check_ai_providers,
)
from loguru import logger

ai_bp = Blueprint("ai", __name__)


def _check_member(team_id, user_id):
    return TeamMember.query.filter_by(team_id=team_id, user_id=user_id).first()


@ai_bp.route("/teams/<team_id>/extract-tasks", methods=["POST"])
@require_auth
def extract_tasks(team_id):
    if not _check_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "Text is required"}), 400
    if len(text) < 10:
        return jsonify({"error": "Text is too short to extract tasks from"}), 400

    members = [
        m.user.to_dict()
        for m in TeamMember.query.filter_by(team_id=team_id).all()
        if m.user
    ]

    try:
        result = extract_tasks_from_text(text, members)
        return jsonify(result)
    except RuntimeError as e:
        logger.error(f"AI task extraction failed: {e}")
        return jsonify({
            "error": str(e),
            "hint": "Check that GROQ_API_KEY is set in backend/.env and restart the server."
        }), 503
    except Exception as e:
        logger.error(f"Unexpected AI error: {e}")
        return jsonify({"error": f"AI processing failed: {str(e)}"}), 500


@ai_bp.route("/teams/<team_id>/recommend-priorities", methods=["GET"])
@require_auth
def recommend_priorities(team_id):
    if not _check_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    from models.database import Task
    tasks = Task.query.filter_by(team_id=team_id).filter(
        Task.status.notin_(["completed", "cancelled"])
    ).limit(20).all()

    try:
        result = recommend_task_priority([t.to_dict() for t in tasks])
        return jsonify(result)
    except RuntimeError as e:
        return jsonify({"error": str(e), "recommendations": [], "insight": "AI unavailable"}), 503
    except Exception as e:
        return jsonify({"error": str(e), "recommendations": [], "insight": ""}), 500


@ai_bp.route("/teams/<team_id>/summarize", methods=["POST"])
@require_auth
def summarize_text(team_id):
    if not _check_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    data         = request.get_json() or {}
    text         = data.get("text", "").strip()
    content_type = data.get("type", "document")

    if not text:
        return jsonify({"error": "Text is required"}), 400

    try:
        result = summarize_content(text, content_type)
        return jsonify(result)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_bp.route("/health", methods=["GET"])
def ai_health():
    """Check which AI providers are available and configured."""
    providers = check_ai_providers()
    available = [k for k, v in providers.items() if v]

    return jsonify({
        "providers":   providers,
        "primary":     available[0] if available else None,
        "status":      "ok" if available else "no_providers_configured",
        "message":     (
            f"Using {available[0]}" if available
            else "No AI providers configured. Add GROQ_API_KEY to backend/.env"
        ),
    })
