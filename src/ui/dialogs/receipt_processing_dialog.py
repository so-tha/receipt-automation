"""
Diálogo para processamento de recebimento.

Interface gráfica para:
1. Carregar arquivo de Contas a Receber (Bestsoft)
2. Filtrar por data de baixa
3. Classificar automaticamente por tipo de documento (3/4/6 dígitos)
4. Verificar duplicados
5. Enviar para planilha no OneDrive (seções corretas)
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QDateEdit,
    QGroupBox, QTableWidget, QTableWidgetItem,
    QFileDialog, QMessageBox, QProgressBar,
    QHeaderView, QTextEdit, QSplitter, QWidget,
    QComboBox, QTabWidget, QListWidget, QAbstractItemView, QCheckBox
)
from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor

from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Set, Any
import logging
import os

from src.services.receipt_processing_service import ReceiptProcessingService, PlanilhaLocator
from src.services.onedrive_service import OneDrivePersonalService
from src.services.audit_service import AuditService
from src.services.authentication_service import AuthenticationService
from src.models.receipt_models import (
    TipoDocumento,
    RelatorioProcessamento,
    ResultadoProcessamento,
    formatar_data_br_para_planilha,
    formatar_data_yyyymmdd_para_chave,
)

logger = logging.getLogger(__name__)


class ProcessingWorker(QThread):
    """Worker thread para processamento em background"""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(
        self,
        service: ReceiptProcessingService,
        data_baixa: date,
        caminho_arquivo: str,
        caminho_ctes_legacy: str = None,
        caminhos_lista_conhecimento: List[str] = None,
        caminhos_lista_nf: List[str] = None,
        filtrar_listas_ultimos_meses: bool = True,
    ):
        super().__init__()
        self.service = service
        self.data_baixa = data_baixa
        self.caminho_arquivo = caminho_arquivo
        lc = list(caminhos_lista_conhecimento or [])
        leg = caminho_ctes_legacy
        if leg and Path(leg).exists() and leg not in lc:
            lc.insert(0, leg)
        self.caminhos_lista_conhecimento = lc
        self.caminhos_lista_nf = list(caminhos_lista_nf or [])
        self.filtrar_listas_ultimos_meses = filtrar_listas_ultimos_meses

    def run(self):
        try:
            self.service.limpar_bases_auxiliares()

            if self.caminhos_lista_conhecimento:
                self.progress.emit("Carregando listas de conhecimento (mescladas)...")
                ok, msg = self.service.carregar_listas_conhecimento(
                    self.caminhos_lista_conhecimento,
                    filtrar_ultimos_meses=self.filtrar_listas_ultimos_meses,
                )
                self.progress.emit(msg if ok else f"Aviso listas CT-e: {msg}")

            if self.caminhos_lista_nf:
                self.progress.emit("Carregando listas de notas fiscais...")
                ok, msg = self.service.carregar_listas_notas_fiscais(
                    self.caminhos_lista_nf,
                    filtrar_ultimos_meses=self.filtrar_listas_ultimos_meses,
                )
                self.progress.emit(msg if ok else f"Aviso listas NF: {msg}")

            self.progress.emit("Carregando contas a receber...")
            relatorio = self.service.processar(
                data_baixa=self.data_baixa,
                caminho_arquivo=self.caminho_arquivo
            )
            self.finished.emit(relatorio)
        except Exception as e:
            self.error.emit(str(e))


class CloudUploadWorker(QThread):
    """Worker thread para envio ao OneDrive"""
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)
    
    def __init__(
        self, 
        service: ReceiptProcessingService,
        relatorio: RelatorioProcessamento,
        config: dict,
        user_token: str = None
    ):
        super().__init__()
        self.service = service
        self.relatorio = relatorio
        self.config = config
        self.user_token = user_token
        self.upload_stats: Dict[str, Any] = {}
    
    def run(self):
        self.upload_stats = {}
        try:
            self._enviar_onedrive()
        except Exception as e:
            self.upload_stats['erro_fatal'] = str(e)
            self.finished.emit(False, str(e))

    def _reaplicar_duplicados_no_relatorio(self) -> None:
        """O processar() não vê OneDrive; na hora do envio marca itens já presentes na planilha."""
        for resultado in self.relatorio.resultados:
            dup_planilha = self.service.verificar_duplicado(resultado.conta)
            dup = bool(resultado.duplicado or dup_planilha)
            resultado.duplicado = dup
            resultado.sucesso = not dup
            if dup_planilha:
                resultado.mensagem = "Duplicado ignorado (ja na planilha)"
            elif not dup:
                resultado.mensagem = "Pronto para inserir"
        self.relatorio.recompute_counts()

    def _enviar_onedrive(self):
        """Envia para OneDrive pessoal usando client credentials (permissões de aplicativo)"""
        self.progress.emit("Conectando ao OneDrive...")
        
        od_service = OneDrivePersonalService(
            tenant_id=self.config['tenant_id'],
            client_id=self.config['client_id'],
            client_secret=self.config['client_secret'],
            user_principal_name=self.config.get('user_principal_name')
        )
        
        # Usar client credentials (permissões de aplicativo) que já estão concedidas
        # em vez do token de usuário (permissões delegadas) que requer consent do admin
        logger.info("Usando client credentials para OneDrive (permissoes de aplicativo)")
        if not od_service.authenticate():
            self.upload_stats = {'etapa': 'auth', 'erro': 'Falha na autenticacao do OneDrive'}
            self.finished.emit(False, "Falha na autenticacao do OneDrive")
            return
        
        file_path = self.config['caminho_planilha']
        sheet_name = self.config.get('nome_aba', 'ABR 26')
        
        self.upload_stats.update({'planilha': file_path, 'aba': sheet_name})
        
        self.progress.emit(f"Lendo dados da aba {sheet_name}...")
        success, dados_planilha = od_service.ler_dados_aba(file_path, sheet_name)
        
        if not success:
            self.upload_stats.update({'etapa': 'ler_planilha', 'erro': str(dados_planilha)})
            self.finished.emit(False, f"Erro ao ler planilha: {dados_planilha}")
            return
        
        self.progress.emit("Localizando secoes...")
        secoes = PlanilhaLocator.localizar_secoes(dados_planilha)
        
        if not secoes:
            self.upload_stats.update({'etapa': 'secoes', 'erro': 'Nenhuma secao NFS/NFE/DOC/DACTE encontrada'})
            self.finished.emit(False, "Nao foi possivel localizar as secoes na planilha")
            return
        
        self.progress.emit("Verificando duplicados...")
        existentes = PlanilhaLocator.extrair_documentos_existentes(dados_planilha, secoes)
        self.service.registrar_duplicados_existentes(existentes)
        self._reaplicar_duplicados_no_relatorio()

        pacotes_por_secao = self.service.obter_pacotes_envio_por_secao(self.relatorio)

        MAX_INSERCOES_AUDIT = 500
        total_inseridos = 0
        erros = []
        linhas_detalhes: Dict[str, int] = {}
        insercoes_registro: List[Dict[str, Any]] = []

        for tipo, pacotes in pacotes_por_secao.items():
            if not pacotes:
                continue

            rows = [p["row"] for p in pacotes]

            if tipo not in secoes:
                erros.append(f"Secao {tipo.value} nao encontrada na planilha")
                continue

            secao = secoes[tipo]
            linha_destino = secao.proxima_linha_vazia

            self.progress.emit(f"Inserindo {len(rows)} linhas em {tipo.value}...")

            if secao.inserir_linhas_antes:
                ok_ins, msg_ins = od_service.inserir_linhas_antes_de(
                    file_path=file_path,
                    sheet_name=sheet_name,
                    linha_referencia=linha_destino,
                    num_linhas=len(rows),
                )
                if not ok_ins:
                    erros.append(f"{tipo.value} (deslocar linhas): {msg_ins}")
                    continue

            success, msg = od_service.inserir_em_linha_especifica(
                file_path=file_path,
                sheet_name=sheet_name,
                linha_inicio=linha_destino,
                rows=rows
            )

            if success:
                total_inseridos += len(rows)
                linhas_detalhes[tipo.value] = len(rows)
                for i, p in enumerate(pacotes):
                    insercoes_registro.append({
                        "secao": p.get("secao") or tipo.value,
                        "linha_planilha": linha_destino + i,
                        "documento": str(p.get("documento") or ""),
                        "fatura": p.get("fatura"),
                    })
                logger.info(f"Inseridos {len(rows)} em {tipo.value}")
            else:
                erros.append(f"{tipo.value}: {msg}")

        n_ins = len(insercoes_registro)
        insercoes_truncadas = n_ins > MAX_INSERCOES_AUDIT
        self.upload_stats = {
            'planilha': file_path,
            'aba': sheet_name,
            'total_inseridos': total_inseridos,
            'por_secao': linhas_detalhes,
            'data_filtro': self.relatorio.data_filtro.isoformat() if self.relatorio.data_filtro else '',
            'erros': erros,
            'insercoes': insercoes_registro[:MAX_INSERCOES_AUDIT],
            'insercoes_truncadas': insercoes_truncadas,
            'insercoes_total': n_ins,
        }
        
        if erros:
            self.finished.emit(False, f"Erros: {'; '.join(erros)}")
        else:
            self.finished.emit(True, f"Inseridos {total_inseridos} registros com sucesso!")


class ReceiptProcessingDialog(QDialog):
    """Diálogo principal para processamento de recebimento"""
    
    def __init__(self, parent=None, current_user=None):
        super().__init__(parent)
        self.current_user = current_user
        self.service = ReceiptProcessingService()
        self.relatorio: RelatorioProcessamento = None
        self.worker: ProcessingWorker = None
        self.upload_worker: CloudUploadWorker = None
        
        self.init_ui()
        self._carregar_config()
    
    def init_ui(self):
        """Inicializa a interface"""
        self.setWindowTitle("Processamento de Recebimento - Contas a Receber")
        self.setMinimumSize(1000, 750)
        self.resize(1100, 850)
        
        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.setContentsMargins(15, 15, 15, 15)
        
        layout.addWidget(self._create_input_group())
        layout.addWidget(self._create_destino_group())
        layout.addWidget(self._create_filter_group())
        layout.addWidget(self._create_actions_group())
        layout.addWidget(self._create_results_group(), stretch=1)
        
        self.setLayout(layout)
    
    def _create_input_group(self) -> QGroupBox:
        """Cria grupo de entrada de arquivos"""
        group = QGroupBox("1. Arquivos de Entrada")
        group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 11pt; }")

        layout = QGridLayout()
        layout.setSpacing(10)

        layout.addWidget(QLabel("Contas a Receber:"), 0, 0)
        self.txt_arquivo = QLineEdit()
        self.txt_arquivo.setPlaceholderText("Arquivo de Contas a Receber (Bestsoft)...")
        self.txt_arquivo.setReadOnly(True)
        layout.addWidget(self.txt_arquivo, 0, 1)

        btn_selecionar = QPushButton("...")
        btn_selecionar.setMaximumWidth(40)
        btn_selecionar.clicked.connect(self._selecionar_arquivo)
        layout.addWidget(btn_selecionar, 0, 2)

        layout.addWidget(QLabel("Listas Conhecimento:"), 1, 0)
        self.list_lista_cte = QListWidget()
        self.list_lista_cte.setMaximumHeight(100)
        self.list_lista_cte.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.list_lista_cte, 1, 1)

        box_cte = QVBoxLayout()
        b_add_cte = QPushButton("+ Arquivos")
        b_add_cte.clicked.connect(self._adicionar_listas_conhecimento)
        b_rm_cte = QPushButton("Remover")
        b_rm_cte.clicked.connect(lambda: self._remover_itens_lista(self.list_lista_cte))
        box_cte.addWidget(b_add_cte)
        box_cte.addWidget(b_rm_cte)
        box_cte.addStretch()
        ww = QWidget()
        ww.setLayout(box_cte)
        layout.addWidget(ww, 1, 2)

        layout.addWidget(QLabel("Listas NF:"), 2, 0)
        self.list_lista_nf = QListWidget()
        self.list_lista_nf.setMaximumHeight(100)
        self.list_lista_nf.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.list_lista_nf, 2, 1)

        box_nf = QVBoxLayout()
        b_add_nf = QPushButton("+ Arquivos")
        b_add_nf.clicked.connect(self._adicionar_listas_nf)
        b_rm_nf = QPushButton("Remover")
        b_rm_nf.clicked.connect(lambda: self._remover_itens_lista(self.list_lista_nf))
        box_nf.addWidget(b_add_nf)
        box_nf.addWidget(b_rm_nf)
        box_nf.addStretch()
        ww2 = QWidget()
        ww2.setLayout(box_nf)
        layout.addWidget(ww2, 2, 2)

        self.chk_filtrar_listas_12m = QCheckBox(
            "Filtrar registros das listas (Conhecimento e NF) para aproximadamente "
            "os últimos 12 meses pela coluna de data detectada."
        )
        self.chk_filtrar_listas_12m.setChecked(True)
        self.chk_filtrar_listas_12m.setStyleSheet("font-weight: normal; font-size: 9pt;")
        layout.addWidget(self.chk_filtrar_listas_12m, 3, 0, 1, 3)

        nota = QLabel(
            "Inclua todas as exportações necessárias (Fiscal>Listas Lista de Conhecimento, "
            "Utilitários>Exportação DCT, Lista de NF). O sistema mescla todos os arquivos e "
            "gera uma linha DACTE por CT-e quando a fatura tiver vários conhecimentos.\n"
            "No arquivo de Contas a Receber, use a exportação que traga a coluna CNPJ/CPF como texto quando possível."
        )
        nota.setStyleSheet("color: #666; font-size: 9pt; font-style: italic;")
        nota.setWordWrap(True)
        layout.addWidget(nota, 4, 0, 1, 3)

        group.setLayout(layout)
        return group
    
    def _create_destino_group(self) -> QGroupBox:
        """Cria grupo de configuração do destino"""
        group = QGroupBox("2. Destino (OneDrive)")
        group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 11pt; }")
        
        layout = QGridLayout()
        layout.setSpacing(10)
        
        self.txt_upn = QLineEdit()
        self.txt_upn.setPlaceholderText("usuario@empresa.com.br")
        
        self.txt_caminho_planilha = QLineEdit()
        self.txt_caminho_planilha.setPlaceholderText("PLANILHA DE RECEBIMENTO_2026.xlsx")
        
        self.txt_nome_aba = QLineEdit()
        self.txt_nome_aba.setPlaceholderText("Ex: ABR 26 (calculado automaticamente)")
        
        layout.addWidget(QLabel("Email do Usuario:"), 0, 0)
        layout.addWidget(self.txt_upn, 0, 1)
        
        layout.addWidget(QLabel("Arquivo Planilha:"), 1, 0)
        layout.addWidget(self.txt_caminho_planilha, 1, 1)
        
        layout.addWidget(QLabel("Aba do Mes:"), 2, 0)
        layout.addWidget(self.txt_nome_aba, 2, 1)
        
        nota = QLabel("* A aba e calculada automaticamente pela data de baixa selecionada")
        nota.setStyleSheet("color: #666; font-size: 9pt; font-style: italic;")
        layout.addWidget(nota, 3, 0, 1, 2)
        
        group.setLayout(layout)
        return group
    
    def _create_filter_group(self) -> QGroupBox:
        """Cria grupo de filtros"""
        group = QGroupBox("3. Filtro por Data de Baixa")
        group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 11pt; }")
        
        layout = QHBoxLayout()
        layout.setSpacing(15)
        
        layout.addWidget(QLabel("Data de Baixa:"))
        
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate().addDays(-1))
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.date_edit.setMinimumWidth(150)
        self.date_edit.dateChanged.connect(self._on_data_changed)
        layout.addWidget(self.date_edit)
        
        layout.addWidget(QLabel("(Data do pagamento/recebimento efetivo)"))
        layout.addStretch()
        
        group.setLayout(layout)
        return group
    
    def _calcular_nome_aba(self, data: QDate) -> str:
        """Calcula o nome da aba baseado na data (formato: ABR 26)"""
        meses = {
            1: 'JAN', 2: 'FEV', 3: 'MAR', 4: 'ABR',
            5: 'MAI', 6: 'JUN', 7: 'JUL', 8: 'AGO',
            9: 'SET', 10: 'OUT', 11: 'NOV', 12: 'DEZ'
        }
        mes = meses.get(data.month(), 'JAN')
        ano = str(data.year())[-2:]
        return f"{mes} {ano}"
    
    def _on_data_changed(self):
        """Atualiza nome da aba quando a data muda"""
        if hasattr(self, 'txt_nome_aba') and hasattr(self, 'date_edit'):
            nome_aba = self._calcular_nome_aba(self.date_edit.date())
            self.txt_nome_aba.setText(nome_aba)
    
    def _create_actions_group(self) -> QGroupBox:
        """Cria grupo de ações"""
        group = QGroupBox("4. Acoes")
        group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 11pt; }")
        
        layout = QHBoxLayout()
        layout.setSpacing(10)
        
        self.btn_processar = QPushButton("1. Processar")
        self.btn_processar.setMinimumWidth(130)
        self.btn_processar.setStyleSheet("QPushButton { background-color: #007bff; color: white; font-weight: bold; }")
        self.btn_processar.clicked.connect(self._processar)
        layout.addWidget(self.btn_processar)
        
        self.btn_aprovar = QPushButton("2. Aprovar e Enviar")
        self.btn_aprovar.setMinimumWidth(150)
        self.btn_aprovar.setEnabled(False)
        self.btn_aprovar.setStyleSheet("""
            QPushButton { background-color: #28a745; color: white; font-weight: bold; }
            QPushButton:disabled { background-color: #cccccc; color: #888888; }
        """)
        self.btn_aprovar.clicked.connect(self._aprovar_e_enviar)
        layout.addWidget(self.btn_aprovar)
        
        self.btn_rejeitar = QPushButton("Rejeitar")
        self.btn_rejeitar.setMinimumWidth(90)
        self.btn_rejeitar.setEnabled(False)
        self.btn_rejeitar.setStyleSheet("""
            QPushButton { background-color: #dc3545; color: white; }
            QPushButton:disabled { background-color: #cccccc; color: #888888; }
        """)
        self.btn_rejeitar.clicked.connect(self._rejeitar)
        layout.addWidget(self.btn_rejeitar)
        
        self.btn_exportar = QPushButton("Exportar Local")
        self.btn_exportar.setMinimumWidth(110)
        self.btn_exportar.setEnabled(False)
        self.btn_exportar.clicked.connect(self._exportar)
        layout.addWidget(self.btn_exportar)
        
        layout.addStretch()
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumWidth(180)
        layout.addWidget(self.progress_bar)
        
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #666;")
        layout.addWidget(self.lbl_status)
        
        group.setLayout(layout)
        return group
    
    def _create_results_group(self) -> QGroupBox:
        """Cria grupo de resultados com abas por tipo de documento"""
        group = QGroupBox("5. Resultados por Tipo de Documento")
        group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 11pt; }")
        
        layout = QVBoxLayout()
        
        self.tab_widget = QTabWidget()
        
        self.table_nfs = self._create_table(['Data Baixa', 'Numero', 'Cliente', 'CNPJ', 'Valor', 'Centro Custo'])
        self.table_nfe = self._create_table(['Data Baixa', 'Numero', 'Cliente', 'Valor', 'Centro Custo'])
        self.table_doc = self._create_table(['Data Baixa', 'Descricao', 'Cliente', 'Valor'])
        self.table_dacte = self._create_table(['Data Baixa', 'DACTE', 'Cliente', 'CNPJ', 'Valor', 'Fatura'])
        
        self.tab_widget.addTab(self.table_nfs, "NFS (3 digitos)")
        self.tab_widget.addTab(self.table_nfe, "NFE (4 digitos)")
        self.tab_widget.addTab(self.table_doc, "DOC (a identificar)")
        self.tab_widget.addTab(self.table_dacte, "DACTE (6 digitos)")
        
        layout.addWidget(self.tab_widget)
        
        self.txt_resumo = QTextEdit()
        self.txt_resumo.setReadOnly(True)
        self.txt_resumo.setMaximumHeight(100)
        self.txt_resumo.setPlaceholderText("Resumo do processamento aparecera aqui...")
        layout.addWidget(self.txt_resumo)
        
        group.setLayout(layout)
        return group
    
    def _create_table(self, headers: List[str]) -> QTableWidget:
        """Cria uma tabela com os cabeçalhos especificados"""
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setStyleSheet("""
            QTableWidget { gridline-color: #E0E0E0; background-color: white; }
            QTableWidget::item:alternate { background-color: #F5F5F5; }
        """)
        return table
    
    def _carregar_config(self):
        """Carrega configurações do .env"""
        try:
            upn = os.getenv('ONEDRIVE_USER_PRINCIPAL_NAME', '')
            if upn:
                self.txt_upn.setText(upn)
            
            planilha = os.getenv('SHAREPOINT_TEST_FILE', 'PLANILHA DE RECEBIMENTO_2026.xlsx')
            if planilha:
                self.txt_caminho_planilha.setText(planilha)
            
            self._on_data_changed()
        except:
            pass
    
    def _selecionar_arquivo(self):
        """Abre diálogo para selecionar arquivo de Contas a Receber"""
        caminho, _ = QFileDialog.getOpenFileName(
            self,
            'Selecionar Contas a Receber',
            "",
            "Excel/CSV Files (*.xlsx *.xls *.csv);;All Files (*)"
        )
        
        if caminho:
            self.txt_arquivo.setText(caminho)

    def _paths_em_list_widget(self, lw: QListWidget) -> List[str]:
        return [lw.item(i).text() for i in range(lw.count())]

    def _lista_contem_path(self, lw: QListWidget, path: str) -> bool:
        return path in self._paths_em_list_widget(lw)

    def _adicionar_listas_conhecimento(self) -> None:
        caminhos, _ = QFileDialog.getOpenFileNames(
            self,
            "Listas de Conhecimento (vários arquivos)",
            "",
            "Excel/CSV (*.xlsx *.xls *.csv);;Todos (*)",
        )
        for c in caminhos:
            if c and not self._lista_contem_path(self.list_lista_cte, c):
                self.list_lista_cte.addItem(c)

    def _adicionar_listas_nf(self) -> None:
        caminhos, _ = QFileDialog.getOpenFileNames(
            self,
            "Listas de Notas Fiscais (vários arquivos)",
            "",
            "Excel/CSV (*.xlsx *.xls *.csv);;Todos (*)",
        )
        for c in caminhos:
            if c and not self._lista_contem_path(self.list_lista_nf, c):
                self.list_lista_nf.addItem(c)

    def _remover_itens_lista(lw: QListWidget) -> None:
        linhas = sorted({ix.row() for ix in lw.selectedIndexes()}, reverse=True)
        for r in linhas:
            lw.takeItem(r)
    
    def _validar_campos(self) -> bool:
        """Valida se todos os campos estão preenchidos"""
        if not self.txt_arquivo.text():
            QMessageBox.warning(self, "Atencao", "Selecione o arquivo de Contas a Receber")
            return False
        
        if not self.txt_caminho_planilha.text():
            QMessageBox.warning(self, "Atencao", "Informe o nome da planilha de destino")
            return False
        
        tenant_id = os.getenv('AZURE_TENANT_ID', '')
        client_id = os.getenv('AZURE_CLIENT_ID', '')
        client_secret = os.getenv('AZURE_CLIENT_SECRET', '')
        
        if not all([tenant_id, client_id, client_secret]):
            QMessageBox.warning(
                self, "Configuracao", 
                "Configure as credenciais Azure no .env:\n\n"
                "AZURE_TENANT_ID=...\nAZURE_CLIENT_ID=...\nAZURE_CLIENT_SECRET=..."
            )
            return False
        
        return True
    
    def _processar(self):
        """Inicia o processamento"""
        if not self._validar_campos():
            return
        
        self.btn_processar.setEnabled(False)
        self.btn_exportar.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.lbl_status.setText("Processando...")
        
        data_baixa = self.date_edit.date().toPyDate()

        self.worker = ProcessingWorker(
            service=self.service,
            data_baixa=data_baixa,
            caminho_arquivo=self.txt_arquivo.text(),
            caminhos_lista_conhecimento=self._paths_em_list_widget(self.list_lista_cte),
            caminhos_lista_nf=self._paths_em_list_widget(self.list_lista_nf),
            filtrar_listas_ultimos_meses=self.chk_filtrar_listas_12m.isChecked(),
        )
        self.worker.finished.connect(self._on_processamento_concluido)
        self.worker.error.connect(self._on_processamento_erro)
        self.worker.progress.connect(self._on_progress)
        self.worker.start()
    
    def _on_progress(self, msg: str):
        self.lbl_status.setText(msg)
    
    def _on_processamento_concluido(self, relatorio: RelatorioProcessamento):
        """Callback quando processamento termina"""
        self.relatorio = relatorio
        
        self._preencher_tabelas(relatorio)
        self._atualizar_resumo(relatorio)
        self._atualizar_abas_tabs(relatorio)
        
        self.progress_bar.setVisible(False)
        self.btn_processar.setEnabled(True)
        
        total_validos = (relatorio.total_nfs + relatorio.total_nfe + 
                        relatorio.total_doc + relatorio.total_dacte)
        
        if total_validos == 0:
            self.lbl_status.setText("Nenhum registro valido encontrado")
            return
        
        self.lbl_status.setText(f"{total_validos} registro(s) prontos para revisao. Aprove ou rejeite.")
        self.lbl_status.setStyleSheet("color: #007bff; font-weight: bold;")
        self.btn_aprovar.setEnabled(True)
        self.btn_rejeitar.setEnabled(True)
        self.btn_exportar.setEnabled(True)
    
    def _preencher_tabelas(self, relatorio: RelatorioProcessamento):
        """Preenche as tabelas com os resultados por tipo"""
        self.table_nfs.setRowCount(0)
        self.table_nfe.setRowCount(0)
        self.table_doc.setRowCount(0)
        self.table_dacte.setRowCount(0)
        
        for resultado in relatorio.resultados:
            if resultado.duplicado:
                continue
            
            conta = resultado.conta
            tipo = resultado.tipo_documento
            
            def fmt_valor(v):
                if v is None:
                    return "-"
                return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            
            def fmt_data(d):
                x = formatar_data_br_para_planilha(d)
                return x if x else "-"
            
            if tipo == TipoDocumento.NFS:
                row = self.table_nfs.rowCount()
                self.table_nfs.insertRow(row)
                self.table_nfs.setItem(row, 0, QTableWidgetItem(fmt_data(conta.data_baixa)))
                self.table_nfs.setItem(row, 1, QTableWidgetItem(conta.numero_documento or "-"))
                self.table_nfs.setItem(row, 2, QTableWidgetItem(conta.cliente_nome[:40] if conta.cliente_nome else "-"))
                self.table_nfs.setItem(row, 3, QTableWidgetItem(conta.cliente_cnpj or "-"))
                self.table_nfs.setItem(row, 4, QTableWidgetItem(fmt_valor(conta.valor_bruto)))
                self.table_nfs.setItem(row, 5, QTableWidgetItem(conta.centro_custo or "-"))
            
            elif tipo == TipoDocumento.NFE:
                row = self.table_nfe.rowCount()
                self.table_nfe.insertRow(row)
                self.table_nfe.setItem(row, 0, QTableWidgetItem(fmt_data(conta.data_baixa)))
                self.table_nfe.setItem(row, 1, QTableWidgetItem(conta.numero_documento or "-"))
                self.table_nfe.setItem(row, 2, QTableWidgetItem(conta.cliente_nome[:40] if conta.cliente_nome else "-"))
                self.table_nfe.setItem(row, 3, QTableWidgetItem(fmt_valor(conta.valor_bruto)))
                self.table_nfe.setItem(row, 4, QTableWidgetItem(conta.centro_custo or "-"))
            
            elif tipo == TipoDocumento.DOC:
                row = self.table_doc.rowCount()
                self.table_doc.insertRow(row)
                self.table_doc.setItem(row, 0, QTableWidgetItem(fmt_data(conta.data_baixa)))
                self.table_doc.setItem(row, 1, QTableWidgetItem(conta.observacao[:30] if conta.observacao else "Servico a Identificar"))
                self.table_doc.setItem(row, 2, QTableWidgetItem(conta.cliente_nome[:40] if conta.cliente_nome else "-"))
                self.table_doc.setItem(row, 3, QTableWidgetItem(fmt_valor(conta.valor_bruto)))
            
            elif tipo == TipoDocumento.DACTE:
                row = self.table_dacte.rowCount()
                self.table_dacte.insertRow(row)
                self.table_dacte.setItem(row, 0, QTableWidgetItem(fmt_data(conta.data_baixa)))
                self.table_dacte.setItem(row, 1, QTableWidgetItem(conta.numero_documento or "-"))
                self.table_dacte.setItem(row, 2, QTableWidgetItem(conta.cliente_nome[:40] if conta.cliente_nome else "-"))
                self.table_dacte.setItem(row, 3, QTableWidgetItem(conta.cliente_cnpj or "-"))
                self.table_dacte.setItem(row, 4, QTableWidgetItem(fmt_valor(conta.valor_bruto)))
                self.table_dacte.setItem(row, 5, QTableWidgetItem(conta.numero_fatura or conta.numero_documento or "-"))
    
    def _atualizar_abas_tabs(self, relatorio: RelatorioProcessamento):
        """Atualiza os títulos das abas com contadores"""
        self.tab_widget.setTabText(0, f"NFS (3 digitos) [{relatorio.total_nfs}]")
        self.tab_widget.setTabText(1, f"NFE (4 digitos) [{relatorio.total_nfe}]")
        self.tab_widget.setTabText(2, f"DOC (a identificar) [{relatorio.total_doc}]")
        self.tab_widget.setTabText(3, f"DACTE (6 digitos) [{relatorio.total_dacte}]")
    
    def _atualizar_resumo(self, relatorio: RelatorioProcessamento):
        """Atualiza o resumo do processamento"""
        resumo = relatorio.get_resumo()
        if relatorio.erros:
            resumo += "\n\nErros:\n" + "\n".join(f"  - {e}" for e in relatorio.erros[:10])
        self.txt_resumo.setText(resumo)
    
    def _aprovar_e_enviar(self):
        """Envia os dados aprovados para o OneDrive"""
        if not self.relatorio:
            QMessageBox.warning(self, "Atencao", "Nao ha dados para enviar")
            return
        
        self.btn_aprovar.setEnabled(False)
        self.btn_rejeitar.setEnabled(False)
        self.btn_processar.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.lbl_status.setText("Enviando para OneDrive...")
        self.lbl_status.setStyleSheet("color: #666;")
        
        config = {
            'tenant_id': os.getenv('AZURE_TENANT_ID', ''),
            'client_id': os.getenv('AZURE_CLIENT_ID', ''),
            'client_secret': os.getenv('AZURE_CLIENT_SECRET', ''),
            'user_principal_name': self.txt_upn.text(),
            'caminho_planilha': self.txt_caminho_planilha.text(),
            'nome_aba': self.txt_nome_aba.text() or 'ABR 26'
        }
        
        user_token = None
        if self.current_user:
            if isinstance(self.current_user, dict):
                user_token = self.current_user.get('access_token')
            elif hasattr(self.current_user, 'access_token'):
                user_token = self.current_user.access_token
        
        self.upload_worker = CloudUploadWorker(
            service=self.service,
            relatorio=self.relatorio,
            config=config,
            user_token=user_token
        )
        self.upload_worker.finished.connect(self._on_upload_concluido)
        self.upload_worker.progress.connect(self._on_progress)
        self.upload_worker.start()
    
    def _on_upload_concluido(self, sucesso: bool, mensagem: str):
        """Callback quando envio termina"""
        self.progress_bar.setVisible(False)
        self.btn_processar.setEnabled(True)
        self.btn_exportar.setEnabled(True)

        if self.relatorio:
            self._preencher_tabelas(self.relatorio)
            self._atualizar_resumo(self.relatorio)
            self._atualizar_abas_tabs(self.relatorio)

        stats = getattr(self.upload_worker, "upload_stats", None) or {}
        total_ins = int(stats.get("total_inseridos") or 0)
        erros = list(stats.get("erros") or [])
        if stats.get("erro_fatal"):
            erros.append(str(stats["erro_fatal"]))
        etapa_erro = stats.get("erro")

        planilha = stats.get("planilha") or ""
        aba = stats.get("aba") or ""
        if not planilha:
            planilha = self.txt_caminho_planilha.text()
        if not aba:
            aba = self.txt_nome_aba.text() or ""
        try:
            plan_display = Path(planilha).name if planilha else ""
        except Exception:
            plan_display = (planilha or "")[:120]
        filename_col = f"{plan_display} [{aba}]".strip() if plan_display or aba else (planilha or "—")

        data_baixa_str = ""
        if self.relatorio and self.relatorio.data_filtro:
            data_baixa_str = self.relatorio.data_filtro.isoformat()
        elif stats.get("data_filtro"):
            data_baixa_str = str(stats["data_filtro"])

        ar_ctes = ""
        nomes_lc = []
        if hasattr(self, "list_lista_cte"):
            for i in range(self.list_lista_cte.count()):
                try:
                    nomes_lc.append(Path(self.list_lista_cte.item(i).text()).name)
                except Exception:
                    nomes_lc.append(self.list_lista_cte.item(i).text()[:80])
            ar_ctes = ", ".join(nomes_lc)[:450]

        ar_nf_list = ""
        nomes_nf = []
        if hasattr(self, "list_lista_nf"):
            for i in range(self.list_lista_nf.count()):
                try:
                    nomes_nf.append(Path(self.list_lista_nf.item(i).text()).name)
                except Exception:
                    nomes_nf.append(self.list_lista_nf.item(i).text()[:80])
            ar_nf_list = ", ".join(nomes_nf)[:450]

        ar_contas = ""
        if self.txt_arquivo.text():
            try:
                ar_contas = Path(self.txt_arquivo.text()).name
            except Exception:
                ar_contas = self.txt_arquivo.text()[:120]

        msg_erro_audit = ""
        if sucesso:
            audit_action = "receipt_send_ok"
        elif total_ins > 0:
            audit_action = "receipt_send_partial"
            msg_erro_audit = "; ".join(erros) if erros else (mensagem or "")
        else:
            audit_action = "receipt_send_failed"
            msg_erro_audit = (
                "; ".join(erros)
                if erros
                else (mensagem or (str(etapa_erro) if etapa_erro else "Erro desconhecido"))
            )
        msg_erro_audit = (msg_erro_audit or "")[:800]

        details = {
            "filename": filename_col,
            "planilha": planilha,
            "aba": aba,
            "data_baixa": data_baixa_str,
            "total_inseridos": total_ins,
            "por_secao": dict(stats.get("por_secao") or {}),
            "arquivo_entrada_contas": ar_contas,
            "arquivo_entrada_ctes": ar_ctes,
            "arquivo_listas_nf": ar_nf_list,
            "etapa": stats.get("etapa"),
        }
        ins = stats.get("insercoes")
        if ins:
            details["insercoes"] = list(ins)
            details["insercoes_truncadas"] = bool(stats.get("insercoes_truncadas"))
            details["insercoes_total"] = int(stats.get("insercoes_total") or len(ins))
        if msg_erro_audit:
            details["mensagem_erro"] = msg_erro_audit

        user_id = AuthenticationService.ensure_user_record_for_audit(self.current_user)
        if not user_id:
            user_id = AuthenticationService.ensure_user_record_for_audit({
                "email": "nao-identificado@sistema.local",
                "name": "Usuario nao identificado",
            })
        if user_id:
            AuditService.log_action(user_id, audit_action, details=details)

        logger.info(
            "Envio recebimento OneDrive: acao_auditoria=%s total_inseridos=%s por_secao=%s data_baixa=%s arquivo=%s",
            audit_action,
            total_ins,
            details.get("por_secao"),
            data_baixa_str,
            filename_col,
        )

        if sucesso:
            self.lbl_status.setText("Enviado com sucesso!")
            self.lbl_status.setStyleSheet("color: green;")
            QMessageBox.information(self, "Sucesso", mensagem)
        else:
            self.lbl_status.setText("Erro no envio")
            self.lbl_status.setStyleSheet("color: red;")
            QMessageBox.critical(self, "Erro", f"Erro ao enviar:\n{mensagem}")
            self.btn_aprovar.setEnabled(True)
            self.btn_rejeitar.setEnabled(True)
    
    def _on_processamento_erro(self, erro: str):
        """Callback quando processamento falha"""
        self.progress_bar.setVisible(False)
        self.btn_processar.setEnabled(True)
        self.lbl_status.setText("Erro!")
        self.lbl_status.setStyleSheet("color: red;")
        QMessageBox.critical(self, "Erro", f"Erro no processamento:\n{erro}")
    
    def _rejeitar(self):
        """Rejeita os dados processados sem enviar"""
        resposta = QMessageBox.question(
            self, "Rejeitar",
            "Deseja rejeitar os dados processados?\nOs resultados serao descartados.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if resposta == QMessageBox.StandardButton.Yes:
            self.relatorio = None
            self.table_nfs.setRowCount(0)
            self.table_nfe.setRowCount(0)
            self.table_doc.setRowCount(0)
            self.table_dacte.setRowCount(0)
            self.txt_resumo.clear()
            self.btn_aprovar.setEnabled(False)
            self.btn_rejeitar.setEnabled(False)
            self.btn_exportar.setEnabled(False)
            self._atualizar_abas_tabs(RelatorioProcessamento(date.today(), datetime.now()))
            self.lbl_status.setText("Dados rejeitados. Processe novamente se necessario.")
            self.lbl_status.setStyleSheet("color: #dc3545;")
    
    def _exportar(self):
        """Exporta resultados para Excel local"""
        if not self.relatorio:
            QMessageBox.warning(self, "Atencao", "Nao ha resultados para exportar")
            return
        
        caminho, _ = QFileDialog.getSaveFileName(
            self, "Salvar Relatorio",
            f"recebimentos_{formatar_data_yyyymmdd_para_chave(self.relatorio.data_filtro) or 'export'}.xlsx",
            "Excel Files (*.xlsx)"
        )
        
        if caminho:
            sucesso, msg = self.service.exportar_para_excel(self.relatorio, caminho)
            if sucesso:
                QMessageBox.information(self, "Sucesso", msg)
            else:
                QMessageBox.critical(self, "Erro", msg)
