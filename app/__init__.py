"""
Main Flask application factory.
"""
from pathlib import Path

from flask import Flask
from flask_socketio import SocketIO

from app.config import Config
from app.database import init_db
from app.services import user_service

# Use eventlet async mode when running with Gunicorn eventlet workers
# This is detected automatically, but we can be explicit
import os
async_mode = os.environ.get('SOCKETIO_ASYNC_MODE', 'eventlet')
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
    # Do this in a non-blocking way to avoid worker timeout
    import threading
    import logging
    
    def init_background():
        """Initialize database and services in background to avoid blocking worker startup."""
        try:
            with app.app_context():
                logging.info("Initializing database...")
                init_db()
                logging.info("Database initialized")
                logging.info("Starting user service...")
                user_service.start()
                logging.info("User service started")
        except Exception as e:
            logging.error(f"Error during database/service initialization: {e}", exc_info=True)
            # Don't crash the app - let it start and handle errors at request time
    
    # Start initialization in background thread
    init_thread = threading.Thread(target=init_background, daemon=True, name="App-Init")
    init_thread.start()
    
    # Start background cleanup thread for SSE connections
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
                cleaned = sse_manager.cleanup_stale_connections(timeout_seconds=120)
                if cleaned > 0:
                    logging.info(f"Cleaned up {cleaned} stale SSE connections")
            except Exception as e:
                logging.error(f"Error in SSE cleanup loop: {e}")
    
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True, name="SSE-Cleanup")
    cleanup_thread.start()


