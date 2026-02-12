"""
WSGI entry point for production.
"""
import sys
import logging

# Configure logging to stdout (Render captures this)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

try:
    from app import create_app, socketio
    from app.utils.cloudflare import get_real_client_ip, is_cloudflare_request, get_cloudflare_ray_id
    app = create_app()
    
    @app.before_request
    def before_request():
        """Execute before each request - log Cloudflare info if available."""
        # Log Cloudflare information for debugging (optional)
        if is_cloudflare_request():
            client_ip = get_real_client_ip()
            ray_id = get_cloudflare_ray_id()
            # Uncomment below if you want to log every request (can be verbose)
            # logger.debug(f"Cloudflare request - IP: {client_ip}, Ray ID: {ray_id}")
    
except Exception as e:
    logger.error("=" * 60)
    logger.error("FATAL ERROR: Failed to create application")
    logger.error("=" * 60)
    logger.error(f"Error: {e}", exc_info=True)
    logger.error("=" * 60)
    # Re-raise so Gunicorn sees the error
    raise


if __name__ == "__main__":
    import os
    PORT = int(os.environ.get("PORT", 5000))
    # Use environment variable for debug mode, default to False for production
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    socketio.run(app, host="0.0.0.0", port=PORT, debug=DEBUG)
