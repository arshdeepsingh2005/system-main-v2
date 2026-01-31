from typing import Optional
import redis
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError
from app.config import Config


class UserAuthService:
    USER_KEY_PREFIX = "user:"
    RATE_LIMIT_PREFIX = "rate_limit:"
    RATE_LIMIT_TTL = 10
    
    def __init__(self, config: Config):
        self.config = config
        self.redis_client: Optional[redis.Redis] = None
        self._connect()
    
    def _connect(self) -> None:
        try:
            redis_url = self.config.REDIS_URL
            if not redis_url:
                return
            
            connection_params = {
                'decode_responses': True,
                'socket_connect_timeout': self.config.REDIS_SOCKET_CONNECT_TIMEOUT,
                'socket_timeout': self.config.REDIS_SOCKET_TIMEOUT,
                'retry_on_timeout': True,
                'health_check_interval': 30
            }
            
            if redis_url.startswith('rediss://'):
                import ssl
                connection_params['ssl_cert_reqs'] = ssl.CERT_NONE
                connection_params['ssl_check_hostname'] = False
            
            self.redis_client = redis.from_url(redis_url, **connection_params)
            self.redis_client.ping()
        except (RedisConnectionError, Exception):
            self.redis_client = None
    
    def _get_user_key(self, username: str) -> str:
        return f"{self.USER_KEY_PREFIX}{username.lower()}"
    
    def _get_rate_limit_key(self, username: str) -> str:
        return f"{self.RATE_LIMIT_PREFIX}{username.lower()}"
    
    def _is_rate_limited(self, username: str) -> bool:
        if not self.redis_client:
            return False
        try:
            rate_limit_key = self._get_rate_limit_key(username)
            return self.redis_client.exists(rate_limit_key) > 0
        except RedisError:
            return False
    
    def _set_rate_limit(self, username: str) -> None:
        if not self.redis_client:
            return
        try:
            rate_limit_key = self._get_rate_limit_key(username)
            self.redis_client.setex(rate_limit_key, self.RATE_LIMIT_TTL, "1")
        except RedisError:
            pass
    
    def verify_username(self, username: str) -> Optional[dict]:
        if not self.redis_client or not username:
            return None
        
        try:
            user_key = self._get_user_key(username)
            user_data = self.redis_client.hgetall(user_key)
            
            if user_data and user_data.get('user_id'):
                return {
                    'user_id': int(user_data['user_id']),
                    'username': user_data.get('username', username)
                }
            
            if self._is_rate_limited(username):
                return None
            
            self._lookup_and_cache_user(username)
            self._set_rate_limit(username)
            return None
        except (RedisError, ValueError):
            return None
    
    def _lookup_and_cache_user(self, username: str) -> None:
        try:
            import eventlet
            if hasattr(eventlet, 'spawn'):
                eventlet.spawn(self._db_lookup_user, username)
                return
        except (ImportError, AttributeError):
            pass
        
        import threading
        thread = threading.Thread(target=self._db_lookup_user, args=(username,), daemon=True)
        thread.start()
    
    def _db_lookup_user(self, username: str) -> None:
        try:
            from sqlalchemy import select, func
            from app.database import SessionLocal
            from app.models import User
            
            with SessionLocal() as session:
                row = session.execute(
                    select(User).where(func.lower(User.username) == username.lower())
                ).scalar_one_or_none()
                
                if row:
                    self.set_user(row.username, row.id)
        except Exception:
            pass
    
    def set_user(self, username: str, user_id: int) -> bool:
        if not self.redis_client:
            return False
        
        try:
            user_key = self._get_user_key(username)
            return bool(self.redis_client.hset(
                user_key,
                mapping={
                    'user_id': str(user_id),
                    'username': username
                }
            ))
        except RedisError:
            return False
    
    def delete_user(self, username: str) -> bool:
        if not self.redis_client:
            return False
        
        try:
            user_key = self._get_user_key(username)
            return self.redis_client.delete(user_key) > 0
        except RedisError:
            return False
    
    def remove_stale_users(self, valid_usernames: set) -> int:
        if not self.redis_client:
            return 0
        
        try:
            pattern = f"{self.USER_KEY_PREFIX}*"
            deleted_count = 0
            pipeline_batch_size = 100
            keys_to_delete = []
            
            cursor = 0
            while True:
                cursor, keys = self.redis_client.scan(cursor, match=pattern, count=100)
                
                for key in keys:
                    username_from_key = key.replace(self.USER_KEY_PREFIX, "").lower()
                    if username_from_key not in valid_usernames:
                        keys_to_delete.append(key)
                
                if cursor == 0:
                    break
            
            if keys_to_delete:
                pipe = self.redis_client.pipeline()
                for i in range(0, len(keys_to_delete), pipeline_batch_size):
                    batch = keys_to_delete[i:i + pipeline_batch_size]
                    for key in batch:
                        pipe.delete(key)
                    pipe.execute()
                    deleted_count += len(batch)
            
            return deleted_count
        except RedisError:
            return 0
    
    def sync_users(self, users: list) -> int:
        if not self.redis_client:
            return 0
        
        try:
            count = 0
            pipe = self.redis_client.pipeline()
            
            for user in users:
                user_key = self._get_user_key(user['username'])
                pipe.hset(
                    user_key,
                    mapping={
                        'user_id': str(user['user_id']),
                        'username': user['username']
                    }
                )
                count += 1
            
            pipe.execute()
            return count
        except RedisError:
            return 0
    
    def is_available(self) -> bool:
        if not self.redis_client:
            return False
        try:
            self.redis_client.ping()
            return True
        except RedisError:
            self.redis_client = None
            return False


user_auth_service: Optional[UserAuthService] = None


def init_user_auth_service(config: Config) -> UserAuthService:
    global user_auth_service
    if user_auth_service is None:
        user_auth_service = UserAuthService(config)
    return user_auth_service


def get_user_auth_service() -> Optional[UserAuthService]:
    return user_auth_service

