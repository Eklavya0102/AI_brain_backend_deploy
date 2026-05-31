"""
TeamPulse — Flask Application
Fix: Use gevent for proper WebSocket support on Render/production
Fix: Eliminates 'write() before start_response' Werkzeug error
"""

import os
import sys

# CRITICAL: gevent monkey patch must happen FIRST before any other imports
try:
    from gevent import monkey
    monkey.patch_all()
    ASYNC_MODE = "gevent"
except ImportError:
    ASYNC_MODE = "threading"

from dotenv import load_dotenv
# Try multiple paths
for env_path in [".env", "backend/.env", os.path.join(os.path.dirname(__file__), ".env")]:
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
        break

from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_socketio import SocketIO
from loguru import logger

# Logging
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>",
    colorize=True,
    level="DEBUG",
)
os.makedirs("logs", exist_ok=True)
logger.add("logs/app.log", rotation="10 MB", retention="30 days", level="INFO")

# Key check
groq_key = os.getenv("GROQ_API_KEY", "")
if groq_key:
    logger.info(f"✅ GROQ_API_KEY loaded (starts: {groq_key[:8]}...)")
else:
    logger.warning("⚠️  GROQ_API_KEY not set — AI features disabled")

logger.info(f"✅ SocketIO async mode: {ASYNC_MODE}")

socketio = SocketIO(
    cors_allowed_origins="*",
    async_mode=ASYNC_MODE,
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25,
)
jwt = JWTManager()


def create_app():
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY                     = os.getenv("SECRET_KEY",    "teampulse-dev-secret"),
        JWT_SECRET_KEY                 = os.getenv("JWT_SECRET_KEY","teampulse-jwt-secret"),
        SQLALCHEMY_DATABASE_URI        = os.getenv("DATABASE_URL",  "sqlite:///teampulse.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS = False,
        UPLOAD_FOLDER                  = os.getenv("UPLOAD_FOLDER", "./uploads"),
        MAX_CONTENT_LENGTH             = int(os.getenv("MAX_CONTENT_LENGTH", 16_777_216)),
        JWT_ACCESS_TOKEN_EXPIRES       = False,
    )

    for d in [app.config["UPLOAD_FOLDER"], "logs", "vector_store"]:
        os.makedirs(d, exist_ok=True)

    from models.database import db
    db.init_app(app)

    origins = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000"
    ).split(",")
    CORS(app, origins=origins, supports_credentials=True)
    jwt.init_app(app)
    socketio.init_app(app)

    from api.auth.routes      import auth_bp
    from api.tasks.routes     import tasks_bp
    from api.chat.routes      import chat_bp
    from api.knowledge.routes import knowledge_bp
    from api.analytics.routes import analytics_bp
    from api.ai.routes        import ai_bp

    app.register_blueprint(auth_bp,      url_prefix="/api/auth")
    app.register_blueprint(tasks_bp,     url_prefix="/api/tasks")
    app.register_blueprint(chat_bp,      url_prefix="/api/chat")
    app.register_blueprint(knowledge_bp, url_prefix="/api/knowledge")
    app.register_blueprint(analytics_bp, url_prefix="/api/analytics")
    app.register_blueprint(ai_bp,        url_prefix="/api/ai")

    from api.chat.socket_events import register_socket_events
    register_socket_events(socketio)

    with app.app_context():
        db.create_all()
        logger.info("✅ Database ready")

    try:
        from utils.auth_middleware import init_firebase
        init_firebase()
    except Exception as e:
        logger.warning(f"Firebase init skipped: {e}")

    @app.route("/api/health")
    def health():
        from services.ai.vector_service import FAISS_AVAILABLE, ST_AVAILABLE
        return {
            "status":     "healthy",
            "service":    "TeamPulse",
            "version":    "1.0.0",
            "async_mode": ASYNC_MODE,
            "groq_key":   bool(os.getenv("GROQ_API_KEY")),
            "features":   {"faiss": FAISS_AVAILABLE, "embeddings": ST_AVAILABLE},
        }

    @app.route("/")
    def root():
        return {"message": "TeamPulse API — visit /api/health"}

    logger.info("🚀 TeamPulse backend ready")
    return app


if __name__ == "__main__":
    app   = create_app()
    port  = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_ENV", "development") == "development"
    logger.info(f"🌐 http://localhost:{port} (mode: {ASYNC_MODE})")
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=debug,
        allow_unsafe_werkzeug=True,
        use_reloader=False,
    )
