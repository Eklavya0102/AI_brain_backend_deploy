# wsgi.py — Vercel entry point for Flask
from app import create_app, socketio

app = create_app()

# Vercel uses this
application = app

if __name__ == "__main__":
    socketio.run(app)
