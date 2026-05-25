"""
AI Team Brain - Database Models
================================
Complete SQLAlchemy ORM models with relationships.
"""

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import uuid

db = SQLAlchemy()


def generate_uuid():
    return str(uuid.uuid4())


# ─────────────────────────────────────────────
# USER & TEAM MODELS
# ─────────────────────────────────────────────

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    firebase_uid = db.Column(db.String(128), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    display_name = db.Column(db.String(100))
    avatar_url = db.Column(db.String(500))
    role = db.Column(db.String(20), default="member")
    is_active = db.Column(db.Boolean, default=True)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team_memberships = db.relationship("TeamMember", back_populates="user", lazy="dynamic")
    tasks_assigned = db.relationship("Task", foreign_keys="Task.assignee_id", back_populates="assignee", lazy="dynamic")
    tasks_created = db.relationship("Task", foreign_keys="Task.creator_id", back_populates="creator", lazy="dynamic")
    messages = db.relationship("ChatMessage", back_populates="user", lazy="dynamic")
    activities = db.relationship("ActivityLog", back_populates="user", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "displayName": self.display_name,
            "avatarUrl": self.avatar_url,
            "role": self.role,
            "isActive": self.is_active,
            "lastSeen": self.last_seen.isoformat() if self.last_seen else None,
            "createdAt": self.created_at.isoformat()
        }


class Team(db.Model):
    __tablename__ = "teams"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    avatar_url = db.Column(db.String(500))
    invite_code = db.Column(db.String(8), unique=True)
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    members = db.relationship("TeamMember", back_populates="team", lazy="dynamic")
    tasks = db.relationship("Task", back_populates="team", lazy="dynamic")
    chat_rooms = db.relationship("ChatRoom", back_populates="team", lazy="dynamic")
    knowledge_items = db.relationship("KnowledgeItem", back_populates="team", lazy="dynamic")

    def to_dict(self, include_members=False):
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "avatarUrl": self.avatar_url,
            "inviteCode": self.invite_code,
            "createdBy": self.created_by,
            "memberCount": self.members.count(),
            "createdAt": self.created_at.isoformat()
        }
        if include_members:
            data["members"] = [m.to_dict() for m in self.members.all()]
        return data


class TeamMember(db.Model):
    __tablename__ = "team_members"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"), nullable=False)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    role = db.Column(db.String(20), default="member")
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    team = db.relationship("Team", back_populates="members")
    user = db.relationship("User", back_populates="team_memberships")

    def to_dict(self):
        return {
            "id": self.id,
            "teamId": self.team_id,
            "userId": self.user_id,
            "user": self.user.to_dict() if self.user else None,
            "role": self.role,
            "joinedAt": self.joined_at.isoformat()
        }


# ─────────────────────────────────────────────
# TASK MODELS
# ─────────────────────────────────────────────

class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"), nullable=False)
    creator_id = db.Column(db.String(36), db.ForeignKey("users.id"))
    assignee_id = db.Column(db.String(36), db.ForeignKey("users.id"))
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default="pending")
    priority = db.Column(db.String(10), default="medium")
    deadline = db.Column(db.DateTime)
    deadline_text = db.Column(db.String(100))
    source = db.Column(db.String(50), default="manual")
    source_id = db.Column(db.String(36))
    ai_confidence = db.Column(db.Float, default=1.0)
    follow_up_suggestions = db.Column(db.Text)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team = db.relationship("Team", back_populates="tasks")
    assignee = db.relationship("User", foreign_keys=[assignee_id], back_populates="tasks_assigned")
    creator = db.relationship("User", foreign_keys=[creator_id], back_populates="tasks_created")

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "teamId": self.team_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "deadlineText": self.deadline_text,
            "assignee": self.assignee.to_dict() if self.assignee else None,
            "assigneeId": self.assignee_id,
            "creator": self.creator.to_dict() if self.creator else None,
            "source": self.source,
            "aiConfidence": self.ai_confidence,
            "followUpSuggestions": json.loads(self.follow_up_suggestions) if self.follow_up_suggestions else [],
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat()
        }


# ─────────────────────────────────────────────
# CHAT MODELS
# ─────────────────────────────────────────────

class ChatRoom(db.Model):
    __tablename__ = "chat_rooms"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    room_type = db.Column(db.String(20), default="general")
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    team = db.relationship("Team", back_populates="chat_rooms")
    messages = db.relationship("ChatMessage", back_populates="room", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "teamId": self.team_id,
            "name": self.name,
            "description": self.description,
            "roomType": self.room_type,
            "messageCount": self.messages.count(),
            "createdAt": self.created_at.isoformat()
        }


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    room_id = db.Column(db.String(36), db.ForeignKey("chat_rooms.id"), nullable=False)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(20), default="text")
    extra_data = db.Column(db.Text)  # renamed from 'metadata' (reserved word)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    room = db.relationship("ChatRoom", back_populates="messages")
    user = db.relationship("User", back_populates="messages")

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "roomId": self.room_id,
            "userId": self.user_id,
            "user": self.user.to_dict() if self.user else None,
            "content": self.content,
            "messageType": self.message_type,
            "metadata": json.loads(self.extra_data) if self.extra_data else None,
            "createdAt": self.created_at.isoformat()
        }


# ─────────────────────────────────────────────
# KNOWLEDGE BASE MODELS
# ─────────────────────────────────────────────

class KnowledgeItem(db.Model):
    __tablename__ = "knowledge_items"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"), nullable=False)
    uploaded_by = db.Column(db.String(36), db.ForeignKey("users.id"))
    title = db.Column(db.String(500), nullable=False)
    content = db.Column(db.Text)
    original_filename = db.Column(db.String(500))
    file_path = db.Column(db.String(1000))
    file_type = db.Column(db.String(20))
    file_size = db.Column(db.Integer)
    summary = db.Column(db.Text)
    key_points = db.Column(db.Text)
    vector_id = db.Column(db.String(100))
    processing_status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team = db.relationship("Team", back_populates="knowledge_items")

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "teamId": self.team_id,
            "title": self.title,
            "originalFilename": self.original_filename,
            "fileType": self.file_type,
            "fileSize": self.file_size,
            "summary": self.summary,
            "keyPoints": json.loads(self.key_points) if self.key_points else [],
            "processingStatus": self.processing_status,
            "uploadedBy": self.uploaded_by,
            "createdAt": self.created_at.isoformat()
        }


# ─────────────────────────────────────────────
# ACTIVITY & NOTIFICATION MODELS
# ─────────────────────────────────────────────

class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"))
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"))
    action = db.Column(db.String(100), nullable=False)
    entity_type = db.Column(db.String(50))
    entity_id = db.Column(db.String(36))
    description = db.Column(db.Text)
    extra_data = db.Column(db.Text)  # renamed from 'metadata' (reserved word)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="activities")

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "teamId": self.team_id,
            "userId": self.user_id,
            "user": self.user.to_dict() if self.user else None,
            "action": self.action,
            "entityType": self.entity_type,
            "entityId": self.entity_id,
            "description": self.description,
            "metadata": json.loads(self.extra_data) if self.extra_data else None,
            "createdAt": self.created_at.isoformat()
        }


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"))
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text)
    notification_type = db.Column(db.String(50))
    entity_id = db.Column(db.String(36))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "userId": self.user_id,
            "teamId": self.team_id,
            "title": self.title,
            "message": self.message,
            "type": self.notification_type,
            "entityId": self.entity_id,
            "isRead": self.is_read,
            "createdAt": self.created_at.isoformat()
        }


class DailySummary(db.Model):
    __tablename__ = "daily_summaries"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"), nullable=False)
    summary_date = db.Column(db.Date, nullable=False)
    content = db.Column(db.Text)
    stats = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "teamId": self.team_id,
            "summaryDate": self.summary_date.isoformat(),
            "content": self.content,
            "stats": json.loads(self.stats) if self.stats else {},
            "createdAt": self.created_at.isoformat()
        }
