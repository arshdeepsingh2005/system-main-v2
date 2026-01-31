"""
Main Flask application factory.
"""
from pathlib import Path

from flask import Flask
from flask_socketio import SocketIO

from app.config import Config
from app.database import init_db
from app.services import user_service, init_cache_service, init_user_auth_service, user_sync_worker

# Auto-detect async mode:
# - Use 'eventlet' for production (Gunicorn with eventlet workers)
# - Use 'threading' for local development
# - Allow override via SOCKETIO_ASYNC_MODE env var
import os

# Check if we're running in production (Gunicorn or Render)
# Render sets PORT env var, Gunicorn sets GUNICORN_CMD_ARGS
is_production = (
    os.environ.get('GUNICORN_CMD_ARGS') is not None or 
    os.environ.get('SERVER_SOFTWARE', '').startswith('gunicorn') or
    os.environ.get('PORT') is not None  # Render sets this
)

# Default async mode based on environment
if os.environ.get('SOCKETIO_ASYNC_MODE'):
    # Explicit override
    async_mode = os.environ.get('SOCKETIO_ASYNC_MODE')
elif is_production:
    # Production: use eventlet (required for Gunicorn eventlet workers)
    async_mode = 'eventlet'
else:
    # Local development: use threading (works without eventlet)
    async_mode = 'threading'

socketio = SocketIO(cors_allowed_origins="*", async_mode=async_mode)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR.parent / "templates"


def create_app(config_class=Config):
    """Create and configure the Flask application."""
    app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
    app.config.from_object(config_class)
    
    # Initialize SocketIO
    socketio.init_app(app)
    
    # Register blueprints
    from app.routes.main import main_bp
    from app.routes.api import api_bp
    from app.routes.internal import internal_bp
    from app.routes.websocket_routes import ws_bp
    from app.routes.sse_routes import sse_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(internal_bp)
    app.register_blueprint(ws_bp)
    app.register_blueprint(sse_bp)

    # Initialize persistence + service layer
    # Do this synchronously but quickly - database init is fast, user service uses background thread
    import logging
    
    try:
        with app.app_context():
            init_db()
            user_service.start()
            init_cache_service(config_class)
            init_user_auth_service(config_class)
            user_sync_worker.start()
    except Exception as e:
        logging.error(f"Error during database/service initialization: {e}", exc_info=True)
    
    _start_sse_cleanup_thread()
    
    return app


def _start_sse_cleanup_thread():
    """Start background thread to clean up stale SSE connections."""
    import threading
    import time
    import logging
    
    def cleanup_loop():
        """Background loop to clean up stale SSE connections."""
        from app.sse_manager import sse_manager
        
        while True:
            try:
                time.sleep(60)  # Run every minute
                sse_manager.cleanup_stale_connections(timeout_seconds=120)
            except Exception as e:
                logging.error(f"Error in SSE cleanup loop: {e}")
    
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True, name="SSE-Cleanup")
    cleanup_thread.start()


