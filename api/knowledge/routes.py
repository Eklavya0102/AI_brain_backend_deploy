"""
AI Team Brain - Knowledge Base API Routes
RAG pipeline: Upload → Extract → Embed → Store → Search
"""

import os
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, g
from werkzeug.utils import secure_filename
from models.database import db, KnowledgeItem, Task, TeamMember, ActivityLog
from utils.auth_middleware import require_auth
from services.storage.file_service import extract_text_from_file, clean_text, allowed_file, get_file_type
from services.ai.ai_service import extract_tasks_from_text, summarize_content, answer_question_with_context
from services.ai.vector_service import vector_service
from loguru import logger

knowledge_bp = Blueprint("knowledge", __name__)


@knowledge_bp.route("/teams/<team_id>/knowledge", methods=["GET"])
@require_auth
def list_knowledge(team_id):
    if not TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first():
        return jsonify({"error": "Access denied"}), 403

    items = KnowledgeItem.query.filter_by(team_id=team_id)\
        .order_by(KnowledgeItem.created_at.desc()).all()
    return jsonify({"items": [i.to_dict() for i in items]})


@knowledge_bp.route("/teams/<team_id>/knowledge/upload", methods=["POST"])
@require_auth
def upload_file(team_id):
    """Upload file → extract text → summarize → embed → extract tasks."""
    if not TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first():
        return jsonify({"error": "Access denied"}), 403

    if "file" not in request.files and "content" not in request.form:
        return jsonify({"error": "No file or content provided"}), 400

    upload_folder = os.getenv("UPLOAD_FOLDER", "./uploads")
    os.makedirs(upload_folder, exist_ok=True)

    # ── Paste / text input ──────────────────────
    if "content" in request.form:
        raw_content = request.form["content"]
        title = request.form.get("title", "Pasted Content")
        file_type = "paste"

        item = KnowledgeItem(
            team_id=team_id,
            uploaded_by=g.current_user.id,
            title=title,
            content=raw_content,
            file_type="paste",
            processing_status="processing"
        )
        db.session.add(item)
        db.session.commit()

    # ── File upload ──────────────────────────────
    else:
        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "Empty filename"}), 400
        if not allowed_file(file.filename):
            return jsonify({"error": "File type not supported. Use PDF, DOCX, or TXT."}), 400

        filename = secure_filename(file.filename)
        unique_name = f"{uuid.uuid4()}_{filename}"
        file_path = os.path.join(upload_folder, unique_name)
        file.save(file_path)

        file_type = get_file_type(filename)
        file_size = os.path.getsize(file_path)
        raw_content = extract_text_from_file(file_path, file_type)

        if not raw_content:
            return jsonify({"error": "Could not extract text from file"}), 422

        raw_content = clean_text(raw_content)
        title = request.form.get("title", filename)

        item = KnowledgeItem(
            team_id=team_id,
            uploaded_by=g.current_user.id,
            title=title,
            content=raw_content,
            original_filename=filename,
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            processing_status="processing"
        )
        db.session.add(item)
        db.session.commit()

    # ── AI Processing ────────────────────────────
    try:
        # 1. Summarize
        summary_result = summarize_content(raw_content, file_type)
        import json
        item.summary = summary_result.get("summary", "")
        item.key_points = json.dumps(summary_result.get("key_points", []))

        # 2. Embed into vector store
        success = vector_service.add_document(
            team_id=team_id,
            doc_id=item.id,
            title=item.title,
            content=raw_content,
            doc_type=file_type
        )
        if success:
            item.vector_id = item.id

        item.processing_status = "done"
        db.session.commit()

        # 3. Extract tasks — returned to frontend for user to manually add
        # Tasks are NOT auto-saved to DB; user decides which ones to add
        members = [m.user.to_dict() for m in TeamMember.query.filter_by(team_id=team_id).all() if m.user]
        extraction = extract_tasks_from_text(raw_content, members)
        extracted_tasks = []

        for task_data in extraction.get("tasks", []):
            # Try to match assignee name to team member
            assignee_id   = None
            assignee_name = task_data.get("assignee")
            if assignee_name:
                for m in members:
                    name = m.get("displayName", "")
                    if assignee_name.lower() in name.lower():
                        assignee_id = m["id"]
                        break

            extracted_tasks.append({
                "title":        task_data.get("title", ""),
                "description":  task_data.get("description", ""),
                "priority":     task_data.get("priority", "medium"),
                "assigneeId":   assignee_id,
                "assigneeName": task_data.get("assignee"),
                "deadline":     task_data.get("deadline"),
                "deadlineText": task_data.get("deadline_text"),
                "confidence":   task_data.get("confidence", 0.8),
                "follow_up":    task_data.get("follow_up", []),
            })

        # Activity log
        log = ActivityLog(
            team_id=team_id, user_id=g.current_user.id,
            action="file_uploaded", entity_type="knowledge", entity_id=item.id,
            description=f"{g.current_user.display_name} uploaded {item.title}"
        )
        db.session.add(log)
        db.session.commit()

        logger.info(f"✅ Processed '{item.title}': {len(extracted_tasks)} tasks suggested (not auto-saved)")
        return jsonify({
            "item": item.to_dict(),
            "tasksExtracted": len(extracted_tasks),
            "tasks": extracted_tasks,   # suggestions only, not saved
            "summary": summary_result
        }), 201

    except RuntimeError as ai_err:
        # AI provider unavailable — item is still saved and searchable
        item.processing_status = "done"
        db.session.commit()
        logger.warning(f"AI unavailable during upload: {ai_err}")
        return jsonify({
            "item": item.to_dict(),
            "tasksExtracted": 0,
            "aiError": str(ai_err),
            "warning": "File saved but AI processing unavailable. Check GROQ_API_KEY in .env"
        }), 201
    except Exception as e:
        item.processing_status = "failed"
        db.session.commit()
        logger.error(f"Processing failed: {e}")
        return jsonify({
            "item": item.to_dict(),
            "error": f"Processing failed: {str(e)}",
            "tasksExtracted": 0
        }), 201


@knowledge_bp.route("/teams/<team_id>/knowledge/search", methods=["POST"])
@require_auth
def semantic_search(team_id):
    """RAG: semantic search + AI answer generation."""
    if not TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first():
        return jsonify({"error": "Access denied"}), 403

    data = request.get_json()
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Query required"}), 400

    # 1. Vector search
    results = vector_service.search(team_id=team_id, query=query, top_k=5)

    # Enrich with DB metadata
    context_docs = []
    for r in results:
        item = KnowledgeItem.query.get(r["doc_id"])
        if item:
            context_docs.append({
                "title": item.title,
                "content": r["content"],
                "score": r["score"],
                "id": item.id,
                "fileType": item.file_type
            })

    # 2. AI answer
    answer_result = answer_question_with_context(query, context_docs)

    return jsonify({
        "query": query,
        "answer": answer_result.get("answer"),
        "sources": answer_result.get("sources", []),
        "confidence": answer_result.get("confidence", 0),
        "relevantDocs": context_docs[:3]
    })


@knowledge_bp.route("/teams/<team_id>/knowledge/<item_id>", methods=["DELETE"])
@require_auth
def delete_knowledge_item(team_id, item_id):
    if not TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first():
        return jsonify({"error": "Access denied"}), 403

    item = KnowledgeItem.query.filter_by(id=item_id, team_id=team_id).first_or_404()
    vector_service.remove_document(team_id, item_id)

    if item.file_path and os.path.exists(item.file_path):
        try:
            os.remove(item.file_path)
        except Exception:
            pass

    db.session.delete(item)
    db.session.commit()
    return jsonify({"message": "Deleted"})


@knowledge_bp.route("/teams/<team_id>/knowledge/stats", methods=["GET"])
@require_auth
def knowledge_stats(team_id):
    if not TeamMember.query.filter_by(team_id=team_id, user_id=g.current_user.id).first():
        return jsonify({"error": "Access denied"}), 403

    items = KnowledgeItem.query.filter_by(team_id=team_id).all()
    vector_stats = vector_service.get_stats(team_id)

    return jsonify({
        "totalItems": len(items),
        "byType": {
            "pdf": sum(1 for i in items if i.file_type == "pdf"),
            "docx": sum(1 for i in items if i.file_type == "docx"),
            "txt": sum(1 for i in items if i.file_type in ("txt", "text")),
            "paste": sum(1 for i in items if i.file_type == "paste"),
        },
        "vectorStats": vector_stats
    })
