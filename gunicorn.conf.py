"""
Gunicorn configuration for production deployment.
"""
import multiprocessing
import os

# Server socket
# Render uses PORT environment variable (defaults to 10000)
# Use PORT if set, otherwise default to 5000 for local development
PORT = int(os.environ.get("PORT", 5000))
bind = f"0.0.0.0:{PORT}"
backlog = 2048

# Worker processes
# For Render free tier (2GB RAM), use minimal workers to avoid memory/timeout issues
# Start with 1 worker - can increase if needed
workers = 1  # Single worker for free tier stability
worker_class = "eventlet"
worker_connections = 1000
timeout = 120  # Increased timeout for slow initialization (database connections, etc.)
keepalive = 2

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Process naming
proc_name = "code-server"

# Worker lifecycle hooks
def on_starting(server):
    """Called just before the master process is started."""
    import logging
    logging.info("=" * 60)
    logging.info("Gunicorn master process starting")
    logging.info(f"Workers: {workers}, Worker class: {worker_class}")
    logging.info(f"Binding to: {bind}")
    logging.info("=" * 60)

def when_ready(server):
    """Called just after the server is started."""
    import logging
    logging.info("=" * 60)
    logging.info("Gunicorn server is ready to accept connections")
    logging.info("=" * 60)

def worker_int(worker):
    """Called when a worker receives INT or QUIT signal."""
    import logging
    logging.warning(f"Worker {worker.pid} received INT/QUIT signal")

def pre_fork(server, worker):
    """Called just before a worker is forked."""
    pass

def post_fork(server, worker):
    """Called just after a worker has been forked."""
    import logging
    logging.info(f"Worker {worker.pid} forked and ready")



