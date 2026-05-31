"""
TeamPulse — Tasks Routes
Fix 1: Only assignee can mark task status (pending→in_progress→completed)
Fix 2: Emit socket event for real-time task updates (new_task, task_updated)
Fix 3: Completed tasks deletable only by team owner
Fix 4: Instant notification push via socket
"""

from datetime import datetime
from flask import Blueprint, request, jsonify, g
from models.database import db, Task, ActivityLog, TeamMember, Notification
from utils.auth_middleware import require_auth
from loguru import logger

tasks_bp = Blueprint("tasks", __name__)


def _check_member(team_id, user_id):
    return TeamMember.query.filter_by(team_id=team_id, user_id=user_id).first()


def _is_owner(team_id, user_id):
    return TeamMember.query.filter_by(team_id=team_id, user_id=user_id, role="owner").first()


def _emit_socket(event: str, data: dict, room: str):
    """Emit a socket event to a room."""
    try:
        from app import socketio
        socketio.emit(event, data, room=room)
    except Exception as e:
        logger.warning(f"Socket emit failed ({event}): {e}")


def _create_notification(user_id, team_id, title, message, notif_type, entity_id=None):
    notif = Notification(
        user_id=user_id, team_id=team_id,
        title=title, message=message,
        notification_type=notif_type, entity_id=entity_id,
    )
    db.session.add(notif)
    db.session.flush()
    _emit_socket("new_notification", notif.to_dict(), f"user_{user_id}")
    return notif


@tasks_bp.route("/teams/<team_id>/tasks", methods=["GET"])
@require_auth
def get_tasks(team_id):
    if not _check_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    status      = request.args.get("status")
    priority    = request.args.get("priority")
    assignee_id = request.args.get("assigneeId")
    limit       = int(request.args.get("limit", 100))

    query = Task.query.filter_by(team_id=team_id)
    if status:      query = query.filter_by(status=status)
    if priority:    query = query.filter_by(priority=priority)
    if assignee_id: query = query.filter_by(assignee_id=assignee_id)

    tasks = query.order_by(Task.created_at.desc()).limit(limit).all()
    return jsonify({"tasks": [t.to_dict() for t in tasks]})


@tasks_bp.route("/teams/<team_id>/tasks", methods=["POST"])
@require_auth
def create_task(team_id):
    if not _check_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    data     = request.get_json() or {}
    deadline = None
    if data.get("deadline"):
        try:
            deadline = datetime.fromisoformat(data["deadline"].replace("Z", "+00:00"))
        except Exception:
            pass

    task = Task(
        team_id=team_id,
        creator_id=g.current_user.id,
        assignee_id=data.get("assigneeId"),
        title=data.get("title", "Untitled task"),
        description=data.get("description", ""),
        status=data.get("status", "pending"),
        priority=data.get("priority", "medium"),
        deadline=deadline,
        deadline_text=data.get("deadlineText"),
        source=data.get("source", "manual"),
    )
    db.session.add(task)

    db.session.add(ActivityLog(
        team_id=team_id, user_id=g.current_user.id,
        action="task_created", entity_type="task", entity_id=task.id,
        description=f"{g.current_user.display_name} created: {task.title}",
    ))

    # Notify assignee
    if task.assignee_id and task.assignee_id != g.current_user.id:
        _create_notification(
            user_id=task.assignee_id, team_id=team_id,
            title="📋 New task assigned to you",
            message=f"{g.current_user.display_name} assigned: {task.title}",
            notif_type="task", entity_id=task.id,
        )

    db.session.commit()

    # FIX 4: Broadcast new task to entire team room so all members see it instantly
    task_dict = task.to_dict()
    _emit_socket("new_task", task_dict, f"team_{team_id}")

    return jsonify({"task": task_dict}), 201


@tasks_bp.route("/teams/<team_id>/tasks/<task_id>", methods=["PUT"])
@require_auth
def update_task(team_id, task_id):
    if not _check_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    task = Task.query.filter_by(id=task_id, team_id=team_id).first_or_404()
    data = request.get_json() or {}

    is_assignee = task.assignee_id == g.current_user.id
    is_owner    = _is_owner(team_id, g.current_user.id) is not None
    is_creator  = task.creator_id == g.current_user.id

    # FIX 1: Only assignee (or owner/creator) can update status
    if "status" in data:
        new_status = data["status"]
        if not (is_assignee or is_owner or is_creator):
            return jsonify({"error": "Only the assigned person can change task status"}), 403

        # FIX 2: Enforce status progression: pending→in_progress→completed
        current = task.status
        allowed_transitions = {
            "pending":     ["in_progress"],
            "in_progress": ["completed", "pending"],  # pending = undo
            "completed":   ["in_progress"],            # undo complete → in_progress
        }
        allowed = allowed_transitions.get(current, [])
        if new_status not in allowed:
            return jsonify({
                "error": f"Cannot move task from '{current}' to '{new_status}'",
                "allowed": allowed,
            }), 400

        task.status = new_status
        if new_status == "completed":
            task.completed_at = datetime.utcnow()
        elif new_status == "in_progress":
            task.completed_at = None

    # Non-status fields — owner/creator only
    if any(k in data for k in ["title", "description", "priority", "assigneeId", "deadline"]):
        if not (is_owner or is_creator):
            return jsonify({"error": "Only team owner or task creator can edit task details"}), 403

        old_assignee = task.assignee_id
        if "title"       in data: task.title       = data["title"]
        if "description" in data: task.description = data["description"]
        if "priority"    in data: task.priority    = data["priority"]
        if "assigneeId"  in data: task.assignee_id = data["assigneeId"]
        if "deadline"    in data:
            try:
                task.deadline = datetime.fromisoformat(data["deadline"].replace("Z", "+00:00"))
            except Exception:
                pass

        # Notify new assignee
        new_assignee = task.assignee_id
        if new_assignee and new_assignee != old_assignee and new_assignee != g.current_user.id:
            _create_notification(
                user_id=new_assignee, team_id=team_id,
                title="📋 Task assigned to you",
                message=f"{g.current_user.display_name} assigned: {task.title}",
                notif_type="task", entity_id=task.id,
            )

    db.session.add(ActivityLog(
        team_id=team_id, user_id=g.current_user.id,
        action="task_updated", entity_type="task", entity_id=task.id,
        description=f"{g.current_user.display_name} updated: {task.title}",
    ))
    db.session.commit()

    task_dict = task.to_dict()
    # FIX 4: Broadcast update to team room
    _emit_socket("task_updated", task_dict, f"team_{team_id}")

    return jsonify({"task": task_dict})


@tasks_bp.route("/teams/<team_id>/tasks/<task_id>", methods=["DELETE"])
@require_auth
def delete_task(team_id, task_id):
    if not _check_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    task = Task.query.filter_by(id=task_id, team_id=team_id).first_or_404()

    # FIX 3: Completed tasks can only be deleted by team owner
    if task.status == "completed" and not _is_owner(team_id, g.current_user.id):
        return jsonify({"error": "Only the team owner can delete completed tasks"}), 403

    # Non-completed: creator or owner can delete
    is_creator = task.creator_id == g.current_user.id
    is_owner_f = _is_owner(team_id, g.current_user.id) is not None
    if not (is_creator or is_owner_f):
        return jsonify({"error": "Only the task creator or team owner can delete tasks"}), 403

    task_id_copy = task.id
    db.session.delete(task)
    db.session.commit()

    _emit_socket("task_deleted", {"taskId": task_id_copy, "teamId": team_id}, f"team_{team_id}")
    return jsonify({"message": "Task deleted"})


@tasks_bp.route("/teams/<team_id>/tasks/stats", methods=["GET"])
@require_auth
def get_task_stats(team_id):
    if not _check_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403

    all_tasks = Task.query.filter_by(team_id=team_id).all()
    now = datetime.utcnow()
    return jsonify({"stats": {
        "total":         len(all_tasks),
        "pending":       sum(1 for t in all_tasks if t.status == "pending"),
        "in_progress":   sum(1 for t in all_tasks if t.status == "in_progress"),
        "completed":     sum(1 for t in all_tasks if t.status == "completed"),
        "overdue":       sum(1 for t in all_tasks if t.deadline and t.deadline < now and t.status not in ("completed","cancelled")),
        "high_priority": sum(1 for t in all_tasks if t.priority in ("high","critical") and t.status != "completed"),
        "by_priority": {
            "critical": sum(1 for t in all_tasks if t.priority == "critical"),
            "high":     sum(1 for t in all_tasks if t.priority == "high"),
            "medium":   sum(1 for t in all_tasks if t.priority == "medium"),
            "low":      sum(1 for t in all_tasks if t.priority == "low"),
        }
    }})


@tasks_bp.route("/teams/<team_id>/notifications", methods=["GET"])
@require_auth
def get_notifications(team_id):
    if not _check_member(team_id, g.current_user.id):
        return jsonify({"error": "Access denied"}), 403
    notifs = Notification.query.filter_by(
        user_id=g.current_user.id, team_id=team_id
    ).order_by(Notification.created_at.desc()).limit(30).all()
    return jsonify({"notifications": [n.to_dict() for n in notifs]})


@tasks_bp.route("/notifications/<notif_id>/read", methods=["POST"])
@require_auth
def mark_read(notif_id):
    notif = Notification.query.filter_by(id=notif_id, user_id=g.current_user.id).first_or_404()
    notif.is_read = True
    db.session.commit()
    return jsonify({"success": True})
