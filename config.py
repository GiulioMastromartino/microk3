import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    """Base configuration"""
    
    # Security - MUST be set via environment variable in production
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        if os.environ.get('FLASK_ENV') == 'production':
            raise RuntimeError(
                "SECRET_KEY environment variable must be set in production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        else:
            # Generate temporary key for development
            SECRET_KEY = 'dev-key-' + secrets.token_hex(16)
            print("⚠️  WARNING: Using auto-generated SECRET_KEY for development. "
                  "Set SECRET_KEY in .env for persistence.")
    
    # Flask settings
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max request size
    
    # Session security
    SESSION_COOKIE_SECURE = False  # Set True in production with HTTPS
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour
    TRUSTED_PROXY_HOPS = int(os.environ.get('TRUSTED_PROXY_HOPS', '0'))
    
    # Application settings
    DATA_FILE = os.environ.get('DATA_FILE', str(BASE_DIR / 'data' / 'system_data.json'))
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FILE = os.environ.get('LOG_FILE', str(BASE_DIR / 'logs' / 'app.log'))
    
    # Authentication
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changeme')
    
    # Rate limiting
    RATELIMIT_STORAGE_URL = "memory://"
    RATELIMIT_DEFAULT = "200 per day;50 per hour"

    # Publisher Studio / Teleop
    PUBLISH_RATE_MAX_HZ = int(os.environ.get("PUBLISH_RATE_MAX_HZ", "30"))
    TELEOP_DEFAULT_TOPIC = os.environ.get("TELEOP_DEFAULT_TOPIC", "/cmd_vel")
    TELEOP_ALLOWED_TOPICS = [s.strip() for s in os.environ.get("TELEOP_ALLOWED_TOPICS", "/cmd_vel,/rover/cmd_vel,/diff_drive_controller/cmd_vel").split(",") if s.strip()]
    TELEOP_MAX_LINEAR = float(os.environ.get("TELEOP_MAX_LINEAR", "1.0"))
    TELEOP_MAX_ANGULAR = float(os.environ.get("TELEOP_MAX_ANGULAR", "2.0"))
    PUBLISH_ALLOWED_TOPICS = os.environ.get("PUBLISH_ALLOWED_TOPICS", "*")  # "*" or comma list


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    
    def __init__(self):
        if Config.SECRET_KEY.startswith('dev-key-'):
            raise RuntimeError("Cannot use development SECRET_KEY in production")


class TestingConfig(Config):
    """Testing configuration"""
    DEBUG = True
    TESTING = True
    WTF_CSRF_ENABLED = False


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}


def get_config():
    """Get configuration based on FLASK_ENV"""
    env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])
