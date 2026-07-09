"""
AI Team Brain - Analytics Routes
"""

from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, g
from models.database import db, Task, ActivityLog, ChatMessage, ChatRoom, KnowledgeItem, TeamMember, DailySummary
from utils.auth_middleware import require_auth
from loguru import logger

analytics_bp = Blueprint("analytics", __name__)


@analytics_bp.route("/teams/<team_id>/dashboard", methods=["GET"])
@require_auth
def get_dashboard(team_id):
    """Master dashboard endpoint — all data in one call."""
    if not TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first():
        return jsonify({"error": "Access denied"}), 403

    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Tasks
    all_tasks = Task.query.filter_by(team_id=team_id).all()
    pending = [t for t in all_tasks if t.status == "pending"]
    in_progress = [t for t in all_tasks if t.status == "in_progress"]
    overdue = [t for t in all_tasks if t.deadline and t.deadline < now and t.status not in ("completed", "cancelled")]
    upcoming = [t for t in all_tasks if t.deadline and now <= t.deadline <= now + timedelta(days=7) and t.status != "completed"]
    completed_today = [t for t in all_tasks if t.completed_at and t.completed_at >= today_start]

    # Recent activity
    activities = ActivityLog.query.filter_by(team_id=team_id)\
        .filter(ActivityLog.created_at >= week_ago)\
        .order_by(ActivityLog.created_at.desc()).limit(20).all()

    # Knowledge items
    knowledge_count = KnowledgeItem.query.filter_by(team_id=team_id).count()

    # Members
    members = TeamMember.query.filter_by(team_id=team_id).all()

    # Daily summary
    today_summary = DailySummary.query.filter_by(
        team_id=team_id, summary_date=now.date()
    ).first()

    # Task completion trend (last 7 days)
    trend = []
    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        completed = sum(1 for t in all_tasks if t.completed_at and day_start <= t.completed_at < day_end)
        created = sum(1 for t in all_tasks if day_start <= t.created_at < day_end)
        trend.append({
            "date": day.strftime("%m/%d"),
            "completed": completed,
            "created": created
        })

    return jsonify({
        "taskStats": {
            "total": len(all_tasks),
            "pending": len(pending),
            "inProgress": len(in_progress),
            "overdue": len(overdue),
            "completedToday": len(completed_today),
        },
        "pendingTasks": [t.to_dict() for t in sorted(pending + in_progress, key=lambda x: x.priority)[:10]],
        "upcomingDeadlines": sorted([t.to_dict() for t in upcoming], key=lambda x: x.get("deadline") or "")[:5],
        "recentActivity": [a.to_dict() for a in activities],
        "memberCount": len(members),
        "knowledgeCount": knowledge_count,
        "completionTrend": trend,
        "dailySummary": today_summary.to_dict() if today_summary else None
    })


@analytics_bp.route("/teams/<team_id>/analytics", methods=["GET"])
@require_auth
def get_analytics(team_id):
    """Detailed analytics for analytics dashboard."""
    if not TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first():
        return jsonify({"error": "Access denied"}), 403

    now = datetime.utcnow()
    all_tasks = Task.query.filter_by(team_id=team_id).all()
    members = TeamMember.query.filter_by(team_id=team_id).all()

    # Per-member stats
    member_stats = []
    for m in members:
        if not m.user:
            continue
        user_tasks = [t for t in all_tasks if t.assignee_id == m.user_id]
        member_stats.append({
            "user": m.user.to_dict(),
            "assigned": len(user_tasks),
            "completed": sum(1 for t in user_tasks if t.status == "completed"),
            "overdue": sum(1 for t in user_tasks if t.deadline and t.deadline < now and t.status not in ("completed", "cancelled")),
            "completionRate": round(sum(1 for t in user_tasks if t.status == "completed") / len(user_tasks) * 100, 1) if user_tasks else 0
        })

    # Priority distribution
    priority_dist = {
        "critical": sum(1 for t in all_tasks if t.priority == "critical"),
        "high": sum(1 for t in all_tasks if t.priority == "high"),
        "medium": sum(1 for t in all_tasks if t.priority == "medium"),
        "low": sum(1 for t in all_tasks if t.priority == "low"),
    }

    # Source distribution
    source_dist = {
        "manual": sum(1 for t in all_tasks if t.source == "manual"),
        "ai_extracted": sum(1 for t in all_tasks if t.source == "ai_extracted"),
        "chat": sum(1 for t in all_tasks if t.source == "chat"),
    }

    completed = [t for t in all_tasks if t.status == "completed"]
    avg_completion_days = 0
    if completed:
        days_list = [(t.completed_at - t.created_at).days for t in completed if t.completed_at]
        avg_completion_days = round(sum(days_list) / len(days_list), 1) if days_list else 0

    return jsonify({
        "overview": {
            "totalTasks": len(all_tasks),
            "completionRate": round(len(completed) / len(all_tasks) * 100, 1) if all_tasks else 0,
            "avgCompletionDays": avg_completion_days,
            "activeMembers": len([m for m in members if m.user and m.user.is_active])
        },
        "memberStats": sorted(member_stats, key=lambda x: x["completed"], reverse=True),
        "priorityDistribution": priority_dist,
        "sourceDistribution": source_dist
    })


@analytics_bp.route("/teams/<team_id>/daily-summary", methods=["POST"])
@require_auth
def generate_daily_summary(team_id):
    """Generate or refresh AI daily summary."""
    if not TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first():
        return jsonify({"error": "Access denied"}), 403

    from models.database import Team
    team = Team.query.get(team_id)
    now = datetime.utcnow()
    today = now.date()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    all_tasks = Task.query.filter_by(team_id=team_id).all()
    completed_today = [t for t in all_tasks if t.completed_at and t.completed_at >= today_start]
    created_today = [t for t in all_tasks if t.created_at >= today_start]
    overdue = [t for t in all_tasks if t.deadline and t.deadline < now and t.status not in ("completed", "cancelled")]
    upcoming = [t for t in all_tasks if t.deadline and now <= t.deadline <= now + timedelta(days=3)]

    msgs_today = ChatMessage.query.join(
        ChatMessage.room
    ).filter(
        ChatRoom.team_id == team_id,
        ChatMessage.created_at >= today_start,
    ).count()

    files_today = KnowledgeItem.query.filter(
        KnowledgeItem.team_id == team_id,
        KnowledgeItem.created_at >= today_start
    ).count()

    stats = {
        "tasks_completed": len(completed_today),
        "tasks_created": len(created_today),
        "overdue_tasks": len(overdue),
        "messages": msgs_today,
        "files_uploaded": files_today,
        "upcoming_deadlines": [t.title for t in upcoming[:3]]
    }

    from services.ai.ai_service import generate_daily_digest
    import json

    content = generate_daily_digest(team.name, stats)

    summary = DailySummary.query.filter_by(team_id=team_id, summary_date=today).first()
    if summary:
        summary.content = content
        summary.stats = json.dumps(stats)
    else:
        summary = DailySummary(
            team_id=team_id,
            summary_date=today,
            content=content,
            stats=json.dumps(stats)
        )
        db.session.add(summary)

    db.session.commit()
    return jsonify({"summary": summary.to_dict()})


@analytics_bp.route("/teams/<team_id>/activity", methods=["GET"])
@require_auth
def get_activity(team_id):
    if not TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first():
        return jsonify({"error": "Access denied"}), 403

    limit = int(request.args.get("limit", 30))
    activities = ActivityLog.query.filter_by(team_id=team_id)\
        .order_by(ActivityLog.created_at.desc()).limit(limit).all()
    return jsonify({"activities": [a.to_dict() for a in activities]})
