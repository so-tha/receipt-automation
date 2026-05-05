"""
Main application module.
Entry point for initializing the application.
"""

import sys
from PyQt6.QtWidgets import QApplication
from src.core import config_obj, init_database
from src.ui.main_window import MainWindow
from src.ui.dialogs.azure_login_simple_dialog import AzureLoginDialog
from src.utils.logger import get_logger

logger = get_logger(__name__)


class LocalLifeApplication:
    """Main application class."""
    
    def __init__(self):
        """Initialize the application."""
        self.app = None
        self.main_window = None
        logger.info(f"Initializing {config_obj.APP_NAME} v{config_obj.APP_VERSION}")
    
    def initialize(self) -> bool:
        """
        Initialize application components.
        
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            # Initialize database
            logger.info("Initializing database...")
            init_database()
            logger.info("Database initialized successfully")
            
            # Create PyQt6 application
            self.app = QApplication.instance() or QApplication(sys.argv)
            
            logger.info(f"{config_obj.APP_NAME} initialized successfully")
            return True
        
        except Exception as e:
            logger.error(f"Application initialization failed: {str(e)}")
            return False
    
    def run(self) -> int:
        """
        Run the application.
        
        Returns:
            Application exit code
        """
        if not self.app:
            logger.error("Application not properly initialized")
            return 1
        
        try:
            # Se authentication ativada, pedir login
            current_user = None
            if config_obj.AUTHENTICATION_ENABLED:
                logger.info("Modo autenticação: Azure AD (AUTH_ENABLED ligado).")
                logger.info("Authentication ativada - solicitando login...")
                
                # Abrir diálogo de login
                from PyQt6.QtWidgets import QDialog
                login_dialog = AzureLoginDialog()
                result = login_dialog.exec()
                
                if result == QDialog.DialogCode.Accepted:
                    current_user = login_dialog.get_user_info()
                    logger.info(f"[OK] Usuario autenticado: {current_user['email']}")
                else:
                    logger.warning("Usuário cancelou o login")
                    return 1
            else:
                logger.info(
                    "Modo autenticação: desligado (sem tela de login). "
                    "Para exigir Azure AD, defina AUTH_ENABLED=true no .env na mesma pasta do .exe."
                )
            
            # Abrir janela principal
            logger.info("Abrindo MainWindow...")
            self.main_window = MainWindow(current_user=current_user)
            self.main_window.show()
            
            logger.info(f"{config_obj.APP_NAME} iniciada com sucesso")
            return self.app.exec()
        
        except Exception as e:
            logger.error(f"Application runtime error: {str(e)}")
            import traceback
            traceback.print_exc()
            return 1


def main() -> int:
    """
    Main entry point for the application.
    
    Returns:
        Application exit code
    """
    app = LocalLifeApplication()
    
    if not app.initialize():
        return 1
    
    return app.run()


if __name__ == '__main__':
    sys.exit(main())
