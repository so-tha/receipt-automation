"""
Configuration module for the desktop application.
Manages environment variables and application settings.
"""

import os
from pathlib import Path
from datetime import timedelta

from src.bootstrap_env import app_runtime_root, load_dotenv_from_app_dir


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


APP_ROOT = app_runtime_root()
load_dotenv_from_app_dir()


class Config:
    """Base configuration class for the application."""
    
    # Application
    APP_NAME = "Loglife"
    APP_VERSION = "1.0.0"
    
    # Database - Use same database as Flask backend
    DATABASE_URL = os.getenv(
        'DATABASE_URL',
        f'sqlite:///{APP_ROOT}/instance/loglife.db'
    )
    SQLALCHEMY_ECHO = os.getenv('SQLALCHEMY_ECHO', 'False').lower() == 'true'
    
    # Upload
    UPLOAD_FOLDER = Path(os.getenv('UPLOAD_FOLDER', f'{APP_ROOT}/uploads'))
    MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', 52428800))  # 50MB in bytes
    ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv', 'pdf'}
    
    # SharePoint Configuration
    SHAREPOINT_SITE_URL = os.getenv('SHAREPOINT_SITE_URL', '')
    SHAREPOINT_LIBRARY = os.getenv('SHAREPOINT_LIBRARY', 'Shared Documents')
    
    # Azure AD Configuration
    # AUTH_ENABLED=true (ou 1/yes/on) ativa o login Azure; caso contrário modo demo (sem diálogo).
    AUTHENTICATION_ENABLED = _env_bool("AUTH_ENABLED", "false")
    
    AZURE_TENANT_ID = os.getenv('AZURE_TENANT_ID', '')
    AZURE_CLIENT_ID = os.getenv('AZURE_CLIENT_ID', '')
    AZURE_CLIENT_SECRET = os.getenv('AZURE_CLIENT_SECRET', '')
    AZURE_REDIRECT_URI = os.getenv('AZURE_REDIRECT_URI', 'http://localhost:8080/auth/callback')
    AZURE_AUTHORITY = os.getenv(
        'AZURE_AUTHORITY', 
        f'https://login.microsoftonline.com/{AZURE_TENANT_ID}'
    )
    AZURE_SCOPES = ['User.Read']  # API permissions needed
    
    # Demo User Configuration (Used when AUTHENTICATION_ENABLED = False)
    DEMO_USER_EMAIL = 'demo@loglife.local'
    DEMO_USER_NAME = 'Demo User'
    DEMO_USER_ID = 'demo-user-fixed-id-001'  # Fixed ID for demo user
    
    # Session
    SESSION_TIMEOUT = timedelta(hours=24)
    
    # UI Settings
    WINDOW_WIDTH = 1200
    WINDOW_HEIGHT = 800
    THEME = 'light'  # 'light' or 'dark'
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = APP_ROOT / 'logs' / 'loglife.log'
    
    @staticmethod
    def get_demo_user():
        """
        Returns or creates a demo user object for use when authentication is disabled.
        Demo user is persisted to database and can be used like a normal user.
        
        Returns:
            User object configured as demo user, or None if creation fails
        """
        from src.core.models import User
        from src.core.database import get_session
        from src.utils.logger import get_logger
        
        logger = get_logger(__name__)
        
        session = None
        try:
            # Get database session
            session = get_session()
            
            # Try to find existing demo user by email
            user = session.query(User).filter_by(email=Config.DEMO_USER_EMAIL).first()
            
            if user:
                logger.info(f"✅ Demo user found in database: {user.email} (ID: {user.id})")
                return user
            
            # Demo user doesn't exist - create it with fixed ID
            logger.info(f"📝 Creating new demo user: {Config.DEMO_USER_EMAIL}")
            user = User(
                id=Config.DEMO_USER_ID,  # Fixed ID
                email=Config.DEMO_USER_EMAIL,
                name=Config.DEMO_USER_NAME,
                role='admin'  # Demo user has admin privileges
            )
            user.is_active = True
            # No password for demo user
            
            session.add(user)
            session.commit()
            
            logger.info(f"✅ Demo user created and saved: {user.email} (ID: {user.id})")
            return user
        
        except Exception as e:
            logger.error(f"❌ Error getting/creating demo user: {e}")
            if session:
                try:
                    session.rollback()
                except:
                    pass
            return None
        finally:
            if session:
                try:
                    session.close()
                except:
                    pass


class DevelopmentConfig(Config):
    """Development environment configuration."""
    DEBUG = True
    SQLALCHEMY_ECHO = True


class ProductionConfig(Config):
    """Production environment configuration."""
    DEBUG = False
    SQLALCHEMY_ECHO = False


class TestingConfig(Config):
    """Testing environment configuration."""
    TESTING = True
    DATABASE_URL = 'sqlite:///:memory:'
    SQLALCHEMY_ECHO = False


# Select configuration based on environment
_ENV = os.getenv('APP_ENV', 'development').lower()

if _ENV == 'production':
    config_obj = ProductionConfig()
elif _ENV == 'testing':
    config_obj = TestingConfig()
else:
    config_obj = DevelopmentConfig()
