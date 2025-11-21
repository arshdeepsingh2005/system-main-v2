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
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "eventlet"
worker_connections = 1000
timeout = 30
keepalive = 2

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Process naming
proc_name = "code-server"



