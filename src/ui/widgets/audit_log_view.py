"""
Audit Log View Widget - Exibe logs de auditoria da aplicação.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, 
    QTableWidgetItem, QLabel, QComboBox, QLineEdit, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from src.services import AuditService
from src.ui.widgets.receipt_activity_table import format_insercoes_resumo
from src.utils.logger import get_logger
from datetime import datetime

logger = get_logger(__name__)


class AuditLogView(QWidget):
    """Widget para visualizar logs de auditoria."""
    
    def __init__(self, parent=None):
        """
        Inicializar o widget.
        
        Args:
            parent: Widget pai (opcional)
        """
        super().__init__(parent)
        self.audit_logs = []
        self.init_ui()
        self.load_logs()
        
        # Auto-refresh a cada 30 segundos
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.load_logs)
        self.refresh_timer.start(30000)
    
    def init_ui(self):
        """Inicializar a interface do usuário."""
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # ===== FILTROS =====
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(10)
        
        # Filtro por ação
        filter_layout.addWidget(QLabel("Filtrar por ação:"))
        self.action_filter = QComboBox()
        self.action_filter.addItem("Todas as ações")
        self.action_filter.addItem("Upload")
        self.action_filter.addItem("Recebimento → planilha")
        self.action_filter.addItem("Aprovação")
        self.action_filter.addItem("Rejeição")
        self.action_filter.addItem("Login")
        self.action_filter.addItem("Logout")
        self.action_filter.currentTextChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.action_filter)
        
        # Filtro por usuário
        filter_layout.addWidget(QLabel("Filtrar por usuário:"))
        self.user_filter = QLineEdit()
        self.user_filter.setPlaceholderText("Digite email ou nome do usuário...")
        self.user_filter.setMaximumWidth(250)
        self.user_filter.textChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.user_filter)
        
        filter_layout.addStretch()
        
        # Botão de atualização
        refresh_btn = QPushButton("🔄 Atualizar")
        refresh_btn.clicked.connect(self.load_logs)
        filter_layout.addWidget(refresh_btn)
        
        layout.addLayout(filter_layout)
        
        # ===== TABELA DE LOGS =====
        table_layout = QVBoxLayout()
        
        # Info label
        info_label = QLabel("Todos os eventos do sistema são registrados aqui para fins de auditoria e conformidade.")
        info_label.setStyleSheet("color: #666666; font-size: 9pt;")
        table_layout.addWidget(info_label)
        
        # Criar tabela
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Horário",
            "Usuário",
            "Ação",
            "Arquivo/Entidade",
            "Detalhes",
            "IP"
        ])
        
        # Configurar colunas
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 180)  # Horário
        self.table.setColumnWidth(1, 200)  # Usuário
        self.table.setColumnWidth(2, 120)  # Ação
        self.table.setColumnWidth(3, 200)  # Arquivo/Entidade
        self.table.setColumnWidth(4, 300)  # Detalhes
        self.table.setColumnWidth(5, 120)  # IP
        
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: white;
                alternate-background-color: #F9F9F9;
                gridline-color: #E0E0E0;
            }
            QHeaderView::section {
                background-color: #F0F0F0;
                padding: 5px;
                border: none;
                border-right: 1px solid #E0E0E0;
                font-weight: bold;
            }
        """)
        
        table_layout.addWidget(self.table)
        layout.addLayout(table_layout)
        
        # ===== RODAPÉ COM CONTADOR =====
        footer_layout = QHBoxLayout()
        footer_layout.addStretch()
        
        self.count_label = QLabel("Total: 0 logs")
        self.count_label.setStyleSheet("color: #666666; font-size: 9pt;")
        footer_layout.addWidget(self.count_label)
        
        layout.addLayout(footer_layout)
        
        self.setLayout(layout)
    
    def load_logs(self):
        """Carregar logs de auditoria do banco de dados."""
        try:
            # Buscar todos os logs (limite 500) e formatar imediatamente
            raw_logs = AuditService.get_all_logs(limit=500)
            
            # Armazenar dados formatados (dicts) em memória para evitar DetachedInstanceError
            self.audit_logs = [AuditService.format_log(log) for log in raw_logs]
            
            # Atualizar tabela
            self.update_table(self.audit_logs)
            
            logger.info(f"Carregados {len(self.audit_logs)} logs de auditoria")
        
        except Exception as e:
            logger.error(f"Erro ao carregar logs de auditoria: {str(e)}")
            QMessageBox.critical(
                self,
                "Erro",
                f"Erro ao carregar logs: {str(e)}"
            )
    
    def update_table(self, logs):
        """
        Atualizar tabela com logs de auditoria.
        
        Args:
            logs: Lista de logs formatados (dicts) a exibir
        """
        self.table.setRowCount(0)
        
        for log in logs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            # Log já está formatado (dict), não precisa chamar format_log novamente
            formatted_log = log
            
            # Horário
            timestamp = formatted_log.get('created_at_formatted', formatted_log.get('created_at', 'N/A'))
            time_item = QTableWidgetItem(str(timestamp))
            time_item.setFlags(time_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, time_item)
            
            # Usuário
            user_text = f"{formatted_log['user_name']}\n{formatted_log['user_email']}"
            user_item = QTableWidgetItem(user_text)
            user_item.setFlags(user_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            user_item.setFont(QFont("Arial", 9))
            self.table.setItem(row, 1, user_item)
            
            # Ação (com cor)
            action = formatted_log['action']
            action_item = QTableWidgetItem(action.upper())
            action_item.setFlags(action_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            action_item.setFont(QFont("Arial", 9, QFont.Weight.Bold))
            
            # Cor baseada na ação
            color_map = {
                'upload': QColor(39, 174, 96),      # Verde
                'upload_duplicate': QColor(241, 196, 15),  # Amarelo
                'approve': QColor(41, 128, 185),    # Azul
                'reject': QColor(231, 76, 60),      # Vermelho
                'login': QColor(243, 156, 18),      # Laranja
                'logout': QColor(127, 140, 141),    # Cinza
                'receipt_send_ok': QColor(46, 204, 113),
                'receipt_send_failed': QColor(192, 57, 43),
                'receipt_send_partial': QColor(230, 126, 34),
            }
            
            bg_color = color_map.get(action, QColor(200, 200, 200))
            action_item.setBackground(bg_color)
            action_item.setForeground(QColor(255, 255, 255))
            
            self.table.setItem(row, 2, action_item)
            
            # Arquivo/Entidade
            filename = formatted_log.get('filename', 'N/A')
            entity_text = f"{filename}" if filename != 'N/A' else f"{formatted_log.get('entity_type', 'N/A')} #{formatted_log.get('entity_id', '')[:8]}"
            entity_item = QTableWidgetItem(entity_text)
            entity_item.setFlags(entity_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, entity_item)
            
            # Detalhes (JSON)
            details = formatted_log.get('details', {})
            details_text = self._format_details(details, formatted_log)
            details_item = QTableWidgetItem(details_text)
            details_item.setFlags(details_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            details_item.setFont(QFont("Courier", 8))
            self.table.setItem(row, 4, details_item)
            
            # IP
            ip_address = formatted_log.get('ip_address', 'N/A')
            ip_item = QTableWidgetItem(ip_address)
            ip_item.setFlags(ip_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 5, ip_item)
        
        # Atualizar contador
        self.count_label.setText(f"Total: {len(logs)} logs")
    
    def _format_details(self, details, formatted_log):
        """
        Formatar detalhes para exibição.
        
        Args:
            details: Dicionário de detalhes
            formatted_log: Log formatado
        
        Returns:
            String formatada com detalhes
        """
        if formatted_log['action'] == 'upload':
            return f"Tamanho: {formatted_log.get('file_size', 'N/A')} bytes"
        
        elif formatted_log['action'] == 'upload_duplicate':
            return f"Duplicata detectada"
        
        elif formatted_log['action'] in ('receipt_send_ok',):
            pc = details.get('por_secao', {})
            tb = ','.join(f"{k}:{v}" for k, v in sorted(pc.items())) if pc else '—'
            ent = details.get('arquivo_entrada_contas') or '—'
            cte = details.get('arquivo_entrada_ctes')
            nf_li = details.get('arquivo_listas_nf')
            extra = f" | Entrada: {ent}"
            if cte:
                extra += f" | Listas CT-e: {cte}"
            if nf_li:
                extra += f" | Listas NF: {nf_li}"
            tot = details.get('total_inseridos', '')
            base = (
                f"Baixa {details.get('data_baixa', '')} | Total: {tot} | Por secao: {tb}{extra}"
            )
            ins_txt = format_insercoes_resumo(details)
            return f"{base} | {ins_txt}" if ins_txt else base

        elif formatted_log['action'] in ('receipt_send_partial',):
            pc = details.get('por_secao', {})
            tb = ','.join(f"{k}:{v}" for k, v in sorted(pc.items())) if pc else '—'
            msg = details.get('mensagem_erro') or ''
            ins_txt = format_insercoes_resumo(details)
            frag = (
                f"Baixa {details.get('data_baixa', '')} | Secoes: {tb} | "
                f"{str(msg)[:140]}"
            )
            return f"{frag} | {ins_txt}" if ins_txt else frag

        elif formatted_log['action'] in ('receipt_send_failed',):
            et = details.get('etapa') or ''
            pref = f"[{et}] " if et else ""
            msg = details.get('mensagem_erro') or details.get('erro') or formatted_log.get('mensagem') or ''
            base = f"{pref}Baixa {details.get('data_baixa', '')}: {str(msg)[:180]}"
            ins_txt = format_insercoes_resumo(details)
            if ins_txt:
                return f"Inserido antes da falha: {ins_txt} | {base}"
            return base

        elif formatted_log['action'] in ['approve', 'reject']:
            comment = details.get('comment', details.get('reason', 'Sem comentário'))
            return f"Motivo: {comment[:100]}..." if len(str(comment)) > 100 else f"Motivo: {comment}"
        
        else:
            # Genérico: mostrar primeiro valor do details
            if details:
                first_key = next(iter(details))
                first_val = details[first_key]
                return f"{first_key}: {str(first_val)[:50]}"
            return "Sem detalhes"
    
    def apply_filters(self):
        """Aplicar filtros aos logs."""
        filtered_logs = self.audit_logs.copy()
        
        # Filtro por ação
        action_text = self.action_filter.currentText()
        if action_text != "Todas as ações":
            action_map = {
                "Upload": "upload",
                "Recebimento → planilha": "__receipt__",
                "Aprovação": "approve",
                "Rejeição": "reject",
                "Login": "login",
                "Logout": "logout"
            }
            action_filter_value = action_map.get(action_text)
            if action_filter_value == "__receipt__":
                filtered_logs = [
                    log for log in filtered_logs
                    if str(log.get("action", "")).startswith("receipt_")
                ]
            elif action_filter_value:
                filtered_logs = [log for log in filtered_logs
                               if log['action'] == action_filter_value or log['action'] == f"{action_filter_value}_duplicate"]
        
        # Filtro por usuário
        user_text = self.user_filter.text().lower().strip()
        if user_text:
            filtered_logs = [
                log for log in filtered_logs 
                if (user_text in log['user_email'].lower() or 
                    user_text in log['user_name'].lower())
            ]
        
        # Atualizar tabela
        self.update_table(filtered_logs)
