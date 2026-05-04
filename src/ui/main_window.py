"""
Main application window for the desktop application.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QTabWidget, QGridLayout, QGroupBox, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from src.core.config import config_obj
from src.services import AuditService
from src.ui.dialogs.receipt_processing_dialog import ReceiptProcessingDialog
from src.ui.widgets.receipt_activity_table import ReceiptActivityTable
from src.ui.widgets.audit_log_view import AuditLogView
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, current_user=None):
        """
        Initialize the main window.

        Args:
            current_user: Currently logged-in user
        """
        super().__init__()
        self.current_user = current_user
        self.init_ui()

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_dashboard)
        self.refresh_timer.start(30000)

    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle(f"{config_obj.APP_NAME} v{config_obj.APP_VERSION}")
        self.setGeometry(100, 100, config_obj.WINDOW_WIDTH, config_obj.WINDOW_HEIGHT)
        self.setMinimumSize(1000, 700)

        self.apply_stylesheet()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(20)

        title_label = QLabel("Sistema de Recebimento")
        title_label.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        if self.current_user:
            email = self.current_user.email if hasattr(self.current_user, 'email') else self.current_user.get('email', 'Usuário')
            user_label = QLabel(f"👤 {email}")
            user_label.setStyleSheet("color: #0078D4; font-weight: bold; font-size: 12px;")
        else:
            user_label = QLabel("🔓 Não logado")
            user_label.setStyleSheet("color: #FF9800; font-weight: bold; font-size: 12px;")

        header_layout.addWidget(user_label)

        layout.addLayout(header_layout)

        self.tabs = QTabWidget()

        self.dashboard_tab = QWidget()
        self.tabs.addTab(self.dashboard_tab, "📊 Dashboard")
        self.setup_dashboard_tab()

        self.audit_tab = QWidget()
        self.tabs.addTab(self.audit_tab, "📝 Logs")
        self.setup_audit_tab()

        layout.addWidget(self.tabs)

        central_widget.setLayout(layout)

        self.create_menu_bar()

        logger.info("Main window initialized successfully")

    def apply_stylesheet(self):
        """Apply global stylesheet to the application."""
        stylesheet = """
        QWidget {
            background-color: #F5F5F5;
            font-family: "Segoe UI", Arial, sans-serif;
            font-size: 10pt;
        }

        QMainWindow {
            background-color: #FFFFFF;
        }

        QPushButton {
            background-color: #0078D4;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 8px 16px;
            font-weight: 500;
            min-height: 32px;
        }

        QPushButton:hover {
            background-color: #1084D7;
        }

        QPushButton:pressed {
            background-color: #005A9E;
        }

        QPushButton:disabled {
            background-color: #CCCCCC;
            color: #666666;
        }

        QGroupBox {
            border: 1px solid #E0E0E0;
            border-radius: 4px;
            margin-top: 10px;
            padding-top: 10px;
            font-weight: bold;
            color: #333333;
        }

        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 3px;
        }

        QTabWidget::pane {
            border: 1px solid #E0E0E0;
        }

        QTabBar::tab {
            background-color: #F0F0F0;
            color: #333333;
            padding: 8px 20px;
            border: 1px solid #E0E0E0;
            border-bottom: none;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
        }

        QTabBar::tab:selected {
            background-color: #FFFFFF;
            color: #0078D4;
            border: 1px solid #E0E0E0;
            border-bottom: 2px solid #0078D4;
        }

        QTabBar::tab:hover:!selected {
            background-color: #EBEBEB;
        }

        QLabel {
            color: #333333;
        }

        QTableWidget {
            background-color: #FFFFFF;
            gridline-color: #E0E0E0;
            border: 1px solid #E0E0E0;
        }

        QTableWidget::item {
            padding: 5px;
        }

        QTableWidget::item:selected {
            background-color: #DCE8F7;
            color: #333333;
        }

        QHeaderView::section {
            background-color: #F0F0F0;
            color: #333333;
            padding: 5px;
            border: 1px solid #E0E0E0;
            font-weight: bold;
        }
        """
        self.setStyleSheet(stylesheet)

    def setup_dashboard_tab(self):
        """Painel focalizado em processamento de recebimento (planilha)."""
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(10, 10, 10, 10)

        stats_group = QGroupBox("📊 Envios para planilha (histórico)")
        stats_group.setStyleSheet("QGroupBox { font-size: 11pt; }")
        stats_layout = QGridLayout()
        stats_layout.setSpacing(15)

        self.receipt_total_label = self.create_stat_label("Total de envios", "0")
        self.receipt_ok_label = self.create_stat_label("Com sucesso", "0", color="green")
        self.receipt_partial_label = self.create_stat_label("Parcial", "0", color="orange")
        self.receipt_failed_label = self.create_stat_label("Falhou", "0", color="red")

        stats_layout.addWidget(self.receipt_total_label, 0, 0)
        stats_layout.addWidget(self.receipt_ok_label, 0, 1)
        stats_layout.addWidget(self.receipt_partial_label, 0, 2)
        stats_layout.addWidget(self.receipt_failed_label, 0, 3)

        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        actions_group = QGroupBox("🔧 Ações")
        actions_group.setStyleSheet("QGroupBox { font-size: 11pt; }")
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(10)

        receipt_button = QPushButton("📋 Processar recebimento")
        receipt_button.setMinimumWidth(200)
        receipt_button.setStyleSheet("QPushButton { background-color: #28a745; }")
        receipt_button.clicked.connect(self.on_process_receipt)
        actions_layout.addWidget(receipt_button)

        refresh_button = QPushButton("🔄 Atualizar")
        refresh_button.setMinimumWidth(120)
        refresh_button.clicked.connect(self.refresh_dashboard)
        actions_layout.addWidget(refresh_button)

        actions_layout.addStretch()

        actions_group.setLayout(actions_layout)
        layout.addWidget(actions_group)

        recent_group = QGroupBox("📋 Envios recentes")
        recent_layout = QVBoxLayout()

        self.receipt_activity_table = ReceiptActivityTable()
        recent_layout.addWidget(self.receipt_activity_table)

        recent_group.setLayout(recent_layout)
        layout.addWidget(recent_group)

        self.dashboard_tab.setLayout(layout)

        self.refresh_dashboard()

    def setup_audit_tab(self):
        """Configurar a aba de logs de auditoria."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.audit_log_view = AuditLogView()
        layout.addWidget(self.audit_log_view)

        self.audit_tab.setLayout(layout)

    def create_stat_label(self, title: str, value: str, color: str = "black") -> QGroupBox:
        group = QGroupBox(title)
        v_layout = QVBoxLayout()

        label = QLabel(value)
        label.setFont(QFont("Arial", 32, QFont.Weight.Bold))
        label.setStyleSheet(f"color: {color};")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        v_layout.addWidget(label)
        group.setLayout(v_layout)

        return group

    def refresh_dashboard(self):
        """Atualiza estatísticas e tabela de envios de recebimento."""
        try:
            stats = AuditService.get_receipt_send_dashboard_stats()

            self.receipt_total_label.findChild(QLabel).setText(str(stats["total"]))
            self.receipt_ok_label.findChild(QLabel).setText(str(stats["ok"]))
            self.receipt_partial_label.findChild(QLabel).setText(str(stats["partial"]))
            self.receipt_failed_label.findChild(QLabel).setText(str(stats["failed"]))

            self.receipt_activity_table.refresh(limit=50)

            logger.info("Dashboard refreshed")

        except Exception as e:
            logger.error(f"Failed to refresh dashboard: {str(e)}")

    def on_process_receipt(self):
        """Abre o diálogo de processamento de recebimento."""
        dialog = ReceiptProcessingDialog(self, current_user=self.current_user)
        dialog.exec()
        if getattr(self, "audit_log_view", None):
            self.audit_log_view.load_logs()
        self.refresh_dashboard()

    def create_menu_bar(self):
        """Criar barra de menu da aplicação."""
        menubar = self.menuBar()

        file_menu = menubar.addMenu("📁 Arquivo")

        receipt_action = file_menu.addAction("Processar recebimento")
        receipt_action.triggered.connect(self.on_process_receipt)

        file_menu.addSeparator()

        exit_action = file_menu.addAction("Sair")
        exit_action.triggered.connect(self.close)

        view_menu = menubar.addMenu("👁️ Visualizar")

        refresh_action = view_menu.addAction("Atualizar")
        refresh_action.triggered.connect(self.refresh_dashboard)

        help_menu = menubar.addMenu("❓ Ajuda")

        about_action = help_menu.addAction("Sobre")
        about_action.triggered.connect(self.on_about)

        if self.current_user:
            user_email = self.current_user.email if hasattr(self.current_user, 'email') else self.current_user.get('email', 'Usuário')
            help_menu.addSeparator()
            logout_action = help_menu.addAction(f"Sair da conta ({user_email})")
            logout_action.triggered.connect(self.on_logout)

    def on_about(self):
        """Exibir diálogo sobre."""
        QMessageBox.information(
            self,
            "Sobre",
            f"{config_obj.APP_NAME} v{config_obj.APP_VERSION}\n\n"
            "Automatização de recebimento: leitura de contas a receber "
            "e gravação na planilha no OneDrive, com auditoria dos envios.\n\n"
            "PyQt6 e SQLAlchemy"
        )

    def on_logout(self):
        """Tratar logout."""
        if self.current_user:
            user_id = self.current_user.id if hasattr(self.current_user, 'id') else self.current_user.get('oid', 'unknown')
            user_email = self.current_user.email if hasattr(self.current_user, 'email') else self.current_user.get('email', 'Usuário')
            AuditService.log_action(user_id, "logout")
            logger.info(f"Usuário saiu: {user_email}")
            QMessageBox.information(self, "Sucesso", "Desconectado com sucesso!")
            self.close()

    def closeEvent(self, event):
        """Handle window close event."""
        self.refresh_timer.stop()
        event.accept()
