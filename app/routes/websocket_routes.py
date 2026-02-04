"""
WebSocket route handlers with username verification.
"""
import hmac

from flask import Blueprint, request
from flask_socketio import disconnect, emit, join_room, leave_room

from app import socketio
from app.config import Config
from app.services import get_user_auth_service, user_service
from app.utils.validators import extract_username, validate_code_data
from app.websocket_manager import websocket_manager

ws_bp = Blueprint('ws', __name__)


def _resolve_user_from_request():
    """
    Resolve user with clean priority flow:
    1. Local cache (pinned users only) - if found, return immediately
    2. Redis cache - if found, return immediately
    3. Quick DB lookup (synchronous with timeout) - if found, cache in Redis and return
    4. If not found, return None
    """
    username = request.args.get('username') or request.headers.get('X-Username')
    if not username:
        return None
    normalized = extract_username({'username': username})
    if not normalized:
        return None
    
    # Priority 1: Check local cache (pinned users only)
    cached = user_service._get_from_cache(normalized)
    if cached and cached.get('user_id'):
        return cached
    
    # Priority 2: Check Redis
    auth_service = get_user_auth_service()
    if auth_service and auth_service.is_available():
        try:
            user_record = auth_service.verify_username(normalized)
            if user_record:
                return user_record
        except Exception:
            pass
    
    # Priority 3: Quick synchronous DB lookup (with timeout)
    if auth_service:
        try:
            user_record = auth_service.lookup_user_sync(normalized, timeout=0.2)
            if user_record:
                return user_record
        except Exception:
            pass
    
    # Not found in any cache or DB
    return None


def _authorize_connection(namespace):
    """
    Authorize WebSocket connection.
    Returns user_record if authorized, None otherwise.
    """
    user_record = _resolve_user_from_request()
    if not user_record or not user_record.get('user_id'):
        emit('error', {'message': 'Unauthorized or unknown username'})
        disconnect(request.sid)
        return None

    websocket_manager.add_client(request.sid, namespace, user_record)
    return user_record


def _client_context():
    client = websocket_manager.get_client(request.sid)
    if not client or not client.get('username'):
        emit('error', {'message': 'Client context missing; please reconnect with username'})
        disconnect(request.sid)
        return None
    return client


def _validate_ingest_token(payload: dict) -> bool:
    server_token = Config.INGEST_SHARED_TOKEN
    if not server_token:
        emit('error', {'message': 'Ingest token not configured on server'})
        return False
    
    provided_token = None
    if isinstance(payload, dict):
        provided_token = payload.get('token')
    
    if not provided_token:
        emit('error', {'message': 'Ingest token is required'})
        return False
    
    if not hmac.compare_digest(str(provided_token), str(server_token)):
        emit('error', {'message': 'Invalid ingest token'})
        return False
    
    return True


def _handle_code_event(data):
    client = _client_context()
    if not client:
        return
    
    if not isinstance(data, dict):
        emit('error', {'message': 'Invalid payload'})
        return

    code_data = validate_code_data(data)
    if not code_data:
        emit('error', {'message': 'Invalid code data'})
        return

    code_data['username'] = client['username']
    code_data['user_id'] = client.get('user_id')
    if 'metadata' not in code_data:
        code_data['metadata'] = {}
    code_data['metadata']['ingested_via'] = client.get('namespace', 'unknown')

    # Broadcast code (with deduplication check) - silent operation, no ack
    websocket_manager.broadcast_code(code_data, socketio)


@socketio.on('connect', namespace='/internal/newcodes')
def handle_internal_newcodes_connect():
    """Handle connection to /internal/newcodes WebSocket endpoint."""
    if not _authorize_connection('internal/newcodes'):
        return False
    emit('connected', {'message': 'Connected to internal newcodes endpoint'})


@socketio.on('disconnect', namespace='/internal/newcodes')
def handle_internal_newcodes_disconnect():
    """Handle disconnection from /internal/newcodes."""
    websocket_manager.remove_client(request.sid)


@socketio.on('code', namespace='/internal/newcodes')
def handle_internal_code(data):
    """Handle code received via /internal/newcodes."""
    _handle_code_event(data)


@socketio.on('connect', namespace='/ws/ingest')
def handle_ws_ingest_connect():
    """Handle connection to /ws/ingest WebSocket endpoint."""
    if not _authorize_connection('ws/ingest'):
        return False
    emit('connected', {'message': 'Connected to ingest endpoint'})


@socketio.on('disconnect', namespace='/ws/ingest')
def handle_ws_ingest_disconnect():
    """Handle disconnection from /ws/ingest."""
    websocket_manager.remove_client(request.sid)


@socketio.on('code', namespace='/ws/ingest')
def handle_ws_ingest_code(data):
    """Handle code received via /ws/ingest."""
    if not isinstance(data, dict):
        emit('error', {'message': 'Invalid payload'})
        return
    
    if not _validate_ingest_token(data):
        return
    
    _handle_code_event(data)


@socketio.on('connect', namespace='/embed')
def handle_embed_connect():
    """Handle connection to /embed WebSocket endpoint (replacement for /ws)."""
    if not _authorize_connection('embed'):
        return False
    emit('connected', {'message': 'Connected to embed endpoint'})


@socketio.on('disconnect', namespace='/embed')
def handle_embed_disconnect():
    """Handle disconnection from /embed."""
    websocket_manager.remove_client(request.sid)


@socketio.on('code', namespace='/embed')
def handle_embed_code(data):
    """Handle code received via /embed."""
    _handle_code_event(data)


@socketio.on('connect', namespace='/events')
def handle_events_connect():
    """Handle connection to /events WebSocket endpoint."""
    if not _authorize_connection('events'):
        return False
    join_room('code_listeners')
    emit('connected', {'message': 'Connected to events endpoint'})


@socketio.on('disconnect', namespace='/events')
def handle_events_disconnect():
    """Handle disconnection from /events."""
    leave_room('code_listeners')
    websocket_manager.remove_client(request.sid)


@socketio.on('subscribe', namespace='/events')
def handle_events_subscribe():
    """Handle subscription to code events."""
    if not _client_context():
        return
    join_room('code_listeners')
    emit('subscribed', {'message': 'Subscribed to code events'})


@socketio.on('connect')
def handle_default_connect():
    """Handle default WebSocket connection."""
    if not _authorize_connection('default'):
        return False
    emit('connected', {'message': 'Connected'})


@socketio.on('disconnect')
def handle_default_disconnect():
    """Handle default WebSocket disconnection."""
    websocket_manager.remove_client(request.sid)

