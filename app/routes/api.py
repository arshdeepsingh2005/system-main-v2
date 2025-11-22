"""
API routes including HTTP fallback for code ingestion.
"""
from flask import Blueprint, jsonify, request

from app.services import user_service
from app.utils.validators import extract_username, validate_code_data
from app.websocket_manager import websocket_manager

api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route('/ingest', methods=['POST', 'GET'])
def ingest():
    """
    HTTP fallback endpoint for code ingestion.
    Accepts both POST and GET requests.
    """
    try:
        # Handle both POST and GET
        if request.method == 'POST':
            data = request.get_json() or request.form.to_dict()
        else:
            data = request.args.to_dict()
        
        # Enforce username presence
        username = extract_username(data)
        if not username:
            return jsonify({'error': 'username is required'}), 400

        user_record = user_service.verify_username(username)
        if not user_record or not user_record.get('user_id'):
            return jsonify({'error': 'Unknown username'}), 403

        # Validate the incoming code payload
        code_data = validate_code_data(data)
        
        if not code_data:
            return jsonify({'error': 'Invalid code data'}), 400

        code_data['username'] = user_record['username']
        code_data['user_id'] = user_record['user_id']
        if 'metadata' not in code_data or not isinstance(code_data['metadata'], dict):
            code_data['metadata'] = {}
        
        # Broadcast to all connected WebSocket clients (non-blocking)
        import threading
        from app import socketio
        
        def broadcast_async():
            try:
                websocket_manager.broadcast_code(code_data, socketio)
            except Exception as e:
                import logging
                logging.error(f"Error broadcasting code: {e}", exc_info=True)
        
        # Start broadcast in background thread to avoid blocking
        broadcast_thread = threading.Thread(target=broadcast_async, daemon=True)
        broadcast_thread.start()
        
        return jsonify({
            'status': 'success',
            'message': 'Code received and broadcasted',
            'username': user_record['username']
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Decoy API routes
@api_bp.route('/status')
def api_status():
    """Decoy route."""
    from app.utils.decoy import generate_decoy_response
    return generate_decoy_response()


@api_bp.route('/users/<string:username>/verify', methods=['GET'])
def verify_user(username):
    """Lightweight username verification endpoint using cache + DB fallback."""
    user_record = user_service.verify_username(username)
    if not user_record or not user_record.get('user_id'):
        return jsonify({'username': username, 'verified': False}), 404

    return jsonify({
        'username': user_record['username'],
        'verified': True,
        'user_id': user_record['user_id'],
        'cache_expires_at': user_record.get('expires_at')
    })


@api_bp.route('/info')
def api_info():
    """Decoy route."""
    from app.utils.decoy import generate_decoy_response
    return generate_decoy_response()

