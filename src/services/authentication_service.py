"""
Authentication service module.
Handles user authentication, session management, and password operations.
"""

from datetime import datetime, timedelta
from typing import Optional
from src.core import get_session
from src.core.models import User, AuditLog
from src.utils.logger import get_logger
from src.utils.constants import AuditAction

logger = get_logger(__name__)


class AuthenticationService:
    """Service for handling user authentication and authorization."""
    
    @staticmethod
    def authenticate_user(email: str, password: str) -> Optional[User]:
        """
        Authenticate user with email and password.
        
        Args:
            email: User email address
            password: User password (plain text)
        
        Returns:
            User object if authentication successful, None otherwise
        """
        try:
            session = get_session()
            user = session.query(User).filter_by(email=email, is_active=True).first()
            session.close()
            
            if user and user.check_password(password):
                logger.info(f"User authenticated: {email}")
                return user
            
            logger.warning(f"Failed authentication attempt for: {email}")
            return None
        
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return None
    
    @staticmethod
    def create_user(email: str, name: str, password: str, role: str = 'user') -> Optional[User]:
        """
        Create a new user account.
        
        Args:
            email: User email address
            name: User full name
            password: User password
            role: User role (default: 'user')
        
        Returns:
            Created User object or None if creation failed
        """
        try:
            session = get_session()
            
            # Check if user already exists
            existing_user = session.query(User).filter_by(email=email).first()
            if existing_user:
                logger.warning(f"User creation failed: {email} already exists")
                session.close()
                return None
            
            # Create new user
            new_user = User(email=email, name=name, role=role)
            new_user.set_password(password)
            
            session.add(new_user)
            session.commit()
            session.close()
            
            logger.info(f"User created: {email} with role {role}")
            return new_user
        
        except Exception as e:
            logger.error(f"User creation error: {str(e)}")
            return None
    
    @staticmethod
    def get_user_by_id(user_id: str) -> Optional[User]:
        """
        Get user by ID.
        
        Args:
            user_id: User ID
        
        Returns:
            User object or None if not found
        """
        try:
            session = get_session()
            user = session.query(User).filter_by(id=user_id).first()
            session.close()
            return user
        except Exception as e:
            logger.error(f"Get user error: {str(e)}")
            return None
    
    @staticmethod
    def get_user_by_email(email: str) -> Optional[User]:
        """
        Get user by email.
        
        Args:
            email: User email
        
        Returns:
            User object or None if not found
        """
        try:
            session = get_session()
            user = session.query(User).filter_by(email=email).first()
            session.close()
            return user
        except Exception as e:
            logger.error(f"Get user by email error: {str(e)}")
            return None
    
    @staticmethod
    def update_password(user_id: str, new_password: str) -> bool:
        """
        Update user password.
        
        Args:
            user_id: User ID
            new_password: New password
        
        Returns:
            True if update successful, False otherwise
        """
        try:
            session = get_session()
            user = session.query(User).filter_by(id=user_id).first()
            
            if user:
                user.set_password(new_password)
                session.commit()
                session.close()
                logger.info(f"Password updated for user: {user_id}")
                return True
            
            session.close()
            return False
        
        except Exception as e:
            logger.error(f"Password update error: {str(e)}")
            return False
    
    @staticmethod
    def deactivate_user(user_id: str) -> bool:
        """
        Deactivate a user account.
        
        Args:
            user_id: User ID
        
        Returns:
            True if deactivation successful, False otherwise
        """
        try:
            session = get_session()
            user = session.query(User).filter_by(id=user_id).first()
            
            if user:
                user.is_active = False
                session.commit()
                session.close()
                logger.info(f"User deactivated: {user_id}")
                return True
            
            session.close()
            return False
        
        except Exception as e:
            logger.error(f"User deactivation error: {str(e)}")
            return False
    
    @staticmethod
    def ensure_user_record_for_audit(current_user) -> Optional[str]:
        """
        Garante que existe um usuário na tabela local (SQLite) para gravar Auditoria (FK audit_logs.user_id).
        Para login Azure usa o OID como id do User quando válido como UUID.
        """
        if not current_user:
            return None
        import uuid as uuid_lib
        from src.core.models import User as LocalUser
        
        try:
            oid = getattr(current_user, 'id', None)
            email = getattr(current_user, 'email', None)
            name = getattr(current_user, 'name', None)
            if isinstance(current_user, dict):
                oid = current_user.get('oid') or oid
                email = current_user.get('email') or email
                name = current_user.get('name') or name
            
            uid = None
            if oid and isinstance(oid, str) and len(oid) == 36:
                uid = oid
            if email:
                existing = AuthenticationService.get_user_by_email(email)
                if existing:
                    return existing.id
            
            if not uid:
                uid = str(uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, email or str(oid) or 'local'))
            
            session = get_session()
            existing = session.query(LocalUser).filter_by(id=uid).first()
            if existing:
                session.close()
                return existing.id
            
            nu = LocalUser(
                id=uid,
                email=email or f"azure-{uid[:8]}@local.audit",
                name=name or (email.split('@')[0] if email else 'Usuario Azure'),
                password_hash=None,
                azure_id=str(oid or '')[:255],
            )
            session.add(nu)
            session.commit()
            session.close()
            logger.info(f"Usuario local criado/atualizado para auditoria: {uid}")
            return uid
        except Exception as e:
            logger.error(f"ensure_user_record_for_audit error: {str(e)}")
            try:
                session.close()
            except Exception:
                pass
            return None