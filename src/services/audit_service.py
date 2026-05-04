"""
Audit service module.
Handles audit log recording and retrieval.
"""

from datetime import datetime
from typing import List, Dict, Any, Optional
from src.core import get_session
from src.core.models import AuditLog
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Envios para planilha (processar recebimento)
RECEIPT_SEND_ACTIONS = (
    "receipt_send_ok",
    "receipt_send_failed",
    "receipt_send_partial",
)


class AuditService:
    """Service for managing audit logs."""
    
    @staticmethod
    def log_action(
        user_id: str,
        action: str,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None
    ) -> Optional[AuditLog]:
        """
        Log a user action to the audit trail.
        
        Args:
            user_id: ID of the user performing the action
            action: Action type (login, upload, approve, etc.)
            details: Additional details about the action
            ip_address: IP address of the request
        
        Returns:
            Created AuditLog object or None if creation failed
        """
        try:
            session = get_session()
            
            audit_log = AuditLog(
                user_id=user_id,
                action=action,
                details=details,
                ip_address=ip_address
            )
            
            session.add(audit_log)
            session.commit()
            session.close()
            
            logger.info(f"Audit action logged: {action} by user {user_id}")
            return audit_log
        
        except Exception as e:
            logger.error(f"Audit log creation error: {str(e)}")
            return None
    
    @staticmethod
    def get_user_audit_logs(
        user_id: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[AuditLog]:
        """
        Get audit logs for a specific user.
        
        Args:
            user_id: User ID
            limit: Maximum number of logs to return
            offset: Number of logs to skip
        
        Returns:
            List of AuditLog objects
        """
        try:
            session = get_session()
            logs = session.query(AuditLog).filter_by(user_id=user_id).order_by(
                AuditLog.created_at.desc()
            ).limit(limit).offset(offset).all()
            session.close()
            return logs
        except Exception as e:
            logger.error(f"Get user audit logs error: {str(e)}")
            return []
    
    @staticmethod
    def get_action_audit_logs(
        action: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[AuditLog]:
        """
        Get audit logs for a specific action.
        
        Args:
            action: Action type to filter by
            limit: Maximum number of logs to return
            offset: Number of logs to skip
        
        Returns:
            List of AuditLog objects
        """
        try:
            session = get_session()
            logs = session.query(AuditLog).filter_by(action=action).order_by(
                AuditLog.created_at.desc()
            ).limit(limit).offset(offset).all()
            session.close()
            return logs
        except Exception as e:
            logger.error(f"Get action audit logs error: {str(e)}")
            return []
    
    @staticmethod
    def get_all_audit_logs(
        limit: int = 100,
        offset: int = 0
    ) -> List[AuditLog]:
        """
        Get all audit logs.
        
        Args:
            limit: Maximum number of logs to return
            offset: Number of logs to skip
        
        Returns:
            List of AuditLog objects
        """
        try:
            session = get_session()
            logs = session.query(AuditLog).order_by(
                AuditLog.created_at.desc()
            ).limit(limit).offset(offset).all()
            session.close()
            return logs
        except Exception as e:
            logger.error(f"Get all audit logs error: {str(e)}")
            return []
    
    @staticmethod
    def count_logs_for_user(user_id: str) -> int:
        """
        Count total audit logs for a user.
        
        Args:
            user_id: User ID
        
        Returns:
            Total count of logs
        """
        try:
            session = get_session()
            count = session.query(AuditLog).filter_by(user_id=user_id).count()
            session.close()
            return count
        except Exception as e:
            logger.error(f"Count user logs error: {str(e)}")
            return 0
    
    @staticmethod
    def get_all_logs(limit: int = 500, offset: int = 0) -> List[AuditLog]:
        """
        Alias para get_all_audit_logs - Retorna todos os logs de auditoria.
        
        Args:
            limit: Máximo de logs a retornar
            offset: Número de logs a pular
        
        Returns:
            Lista de objetos AuditLog
        """
        return AuditService.get_all_audit_logs(limit, offset)
    
    @staticmethod
    def get_receipt_send_dashboard_stats() -> Dict[str, int]:
        """
        Contagens de envios de recebimento para planilha (auditoria).
        """
        empty = {"total": 0, "ok": 0, "partial": 0, "failed": 0}
        try:
            session = get_session()
            q = session.query(AuditLog).filter(AuditLog.action.in_(RECEIPT_SEND_ACTIONS))
            empty["total"] = q.count()
            empty["ok"] = session.query(AuditLog).filter(
                AuditLog.action == "receipt_send_ok"
            ).count()
            empty["partial"] = session.query(AuditLog).filter(
                AuditLog.action == "receipt_send_partial"
            ).count()
            empty["failed"] = session.query(AuditLog).filter(
                AuditLog.action == "receipt_send_failed"
            ).count()
            session.close()
            return empty
        except Exception as e:
            logger.error(f"Receipt dashboard stats error: {str(e)}")
            try:
                session.close()
            except Exception:
                pass
            return empty

    @staticmethod
    def get_recent_receipt_audit_logs(limit: int = 50) -> List[AuditLog]:
        """Últimos registros de envio ao OneDrive (processar recebimento)."""
        try:
            session = get_session()
            logs = (
                session.query(AuditLog)
                .filter(AuditLog.action.in_(RECEIPT_SEND_ACTIONS))
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
                .all()
            )
            session.close()
            return logs
        except Exception as e:
            logger.error(f"Get recent receipt logs error: {str(e)}")
            try:
                session.close()
            except Exception:
                pass
            return []

    @staticmethod
    def format_log(log: AuditLog) -> Dict[str, Any]:
        """
        Formata um log de auditoria para exibição.
        
        Args:
            log: Objeto AuditLog a formatar
        
        Returns:
            Dicionário formatado com informações do log
        """
        try:
            # Obter usuário
            from src.core.models import User
            session = get_session()
            user = session.query(User).filter_by(id=log.user_id).first()
            session.close()
            
            user_name = user.name if user else f"Usuário {log.user_id[:8]}"
            user_email = user.email if user else "N/A"
            
            # Extrair informações do arquivo dos detalhes
            filename = 'N/A'
            file_size = None
            if log.details and isinstance(log.details, dict):
                filename = log.details.get('filename') or log.details.get('planilha', 'N/A')
                file_size = log.details.get('file_size')
            
            # Formatar data/hora
            try:
                created_at_formatted = log.created_at.strftime('%d/%m/%Y %H:%M:%S')
            except:
                created_at_formatted = str(log.created_at)
            
            return {
                'id': log.id,
                'user_name': user_name,
                'user_email': user_email,
                'action': log.action,
                'details': log.details or {},
                'filename': filename,
                'file_size': file_size,
                'created_at': log.created_at.isoformat() if log.created_at else None,
                'created_at_formatted': created_at_formatted,
                'ip_address': getattr(log, 'ip_address', 'N/A'),
                'entity_type': getattr(log, 'entity_type', None),
                'entity_id': getattr(log, 'entity_id', None),
            }
        except Exception as e:
            logger.error(f"Erro ao formatar log: {str(e)}")
            return {
                'id': log.id,
                'user_name': 'Erro',
                'user_email': 'N/A',
                'action': log.action,
                'details': {},
                'filename': 'N/A',
                'file_size': None,
                'created_at': None,
                'created_at_formatted': 'N/A',
                'ip_address': 'N/A',
                'entity_type': None,
                'entity_id': None,
            }
