from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton, QLabel, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
import msal
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
from src.core.config import config_obj
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AuthCodeHandler(BaseHTTPRequestHandler):
    
    auth_code = None
    
    def do_GET(self):
        """Processar GET request do redirect"""
        query_params = parse_qs(urlparse(self.path).query)
        
        if 'code' in query_params:
            AuthCodeHandler.auth_code = query_params['code'][0]
            print(f"[DEBUG] Código de autorização recebido: {AuthCodeHandler.auth_code}")
            
            # Enviar resposta HTML
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            html = """
            <html>
            <body style="font-family: Arial; text-align: center; margin-top: 50px;">
            <h1>✅ Sucesso!</h1>
            <p>Login realizado com sucesso.</p>
            <p>Você pode fechar esta aba e retornar à aplicação.</p>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
        elif 'error' in query_params:
            error = query_params['error'][0]
            error_desc = query_params.get('error_description', [''])[0]
            print(f"[DEBUG] ERRO de autorização: {error} - {error_desc}")
            
            self.send_response(400)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            html = f"""
            <html>
            <body style="font-family: Arial; text-align: center; margin-top: 50px;">
            <h1>❌ Erro</h1>
            <p>{error}: {error_desc}</p>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
    
    def log_message(self, format, *args):
        """Desabilitar logs do servidor"""
        pass


class LoginWorker(QThread):
    """Thread worker para login sem bloquear UI"""
    
    success = pyqtSignal(dict)  # Emite user_info quando sucesso
    error = pyqtSignal(str)  # Emite mensagem de erro
    
    def __init__(self):
        super().__init__()
        self.access_token = None
        self.auth_server = None
        self.auth_server_thread = None
    
    def run(self):
        """Executar login em thread separada"""
        redirect_uri = config_obj.AZURE_REDIRECT_URI
        
        try:
            logger.info("LoginWorker iniciado (Authorization Code Flow)...")
            print(f"[DEBUG] LoginWorker iniciado")
            print(f"[DEBUG] AZURE_CLIENT_ID: {config_obj.AZURE_CLIENT_ID}")
            print(f"[DEBUG] AZURE_SCOPES: {config_obj.AZURE_SCOPES}")
            print(f"[DEBUG] Redirect URI: {redirect_uri}")
            
            # Criar aplicação MSAL (Confidential Client)
            logger.info("Criando ConfidentialClientApplication...")
            app = msal.ConfidentialClientApplication(
                client_id=config_obj.AZURE_CLIENT_ID,
                client_credential=config_obj.AZURE_CLIENT_SECRET,
                authority=config_obj.AZURE_AUTHORITY
            )
            print(f"[DEBUG] ConfidentialClientApplication criada")
            
            # Iniciar servidor HTTP local para receber o código
            logger.info("Iniciando servidor HTTP local...")
            print(f"[DEBUG] Inicializando servidor HTTP em localhost:8080...")
            AuthCodeHandler.auth_code = None
            self.auth_server = HTTPServer(('localhost', 8080), AuthCodeHandler)
            self.auth_server_thread = threading.Thread(target=self.auth_server.serve_forever)
            self.auth_server_thread.daemon = True
            self.auth_server_thread.start()
            print(f"[DEBUG] Servidor HTTP iniciado")
            
            # Iniciar Authorization Code Flow
            logger.info("Iniciando Authorization Code Flow...")
            print(f"[DEBUG] Chamando get_authorization_request_url...")
            auth_url = app.get_authorization_request_url(
                scopes=config_obj.AZURE_SCOPES,
                redirect_uri=redirect_uri
            )
            print(f"[DEBUG] URL de autorização: {auth_url}")
            
            # Abrir navegador
            logger.info("Abrindo navegador para autenticação...")
            print(f"[DEBUG] Abrindo navegador...")
            webbrowser.open(auth_url)
            
            # Aguardar código do servidor HTTP
            logger.info("Aguardando código de autorização...")
            print(f"[DEBUG] Aguardando resposta do navegador...")
            
            auth_code = None
            for i in range(300):  # Aguardar até 5 minutos
                if AuthCodeHandler.auth_code:
                    auth_code = AuthCodeHandler.auth_code
                    break
                threading.Event().wait(0.1)
            
            if not auth_code:
                logger.error("Timeout - usuário não completou a autenticação")
                print(f"[DEBUG] ERRO - Timeout aguardando código")
                self.error.emit("Timeout: Você não completou o login no tempo disponível (5 minutos)")
                return
            
            print(f"[DEBUG] Código recebido: {auth_code}")
            
            # Trocar código por token
            logger.info("Trocando código por token...")
            print(f"[DEBUG] Chamando acquire_token_by_auth_code_flow...")
            result = app.acquire_token_by_authorization_code(
                code=auth_code,
                scopes=config_obj.AZURE_SCOPES,
                redirect_uri=redirect_uri
            )
            print(f"[DEBUG] Resultado: {result}")
            logger.info(f"Auth result: {result}")
            
            if "access_token" in result:
                self.access_token = result["access_token"]
                print(f"[DEBUG] Token obtido com sucesso")
                
                # Extrair info do usuário
                try:
                    import jwt
                    decoded = jwt.decode(
                        self.access_token,
                        options={"verify_signature": False}
                    )
                    user_info = {
                        'email': decoded.get('upn') or decoded.get('email'),
                        'name': decoded.get('name'),
                        'oid': decoded.get('oid')
                    }
                    logger.info(f"[OK] Login bem-sucedido: {user_info['email']}")
                    print(f"[DEBUG] User Info: {user_info}")
                    self.success.emit(user_info)
                except Exception as e:
                    logger.error(f"Erro ao extrair user_info: {str(e)}")
                    print(f"[DEBUG] ERRO ao extrair user_info: {str(e)}")
                    self.success.emit({'email': 'Usuário'})
            else:
                error_msg = result.get('error_description', result.get('error', 'Erro desconhecido'))
                logger.error(f"Erro no login: {error_msg}")
                print(f"[DEBUG] ERRO: {error_msg}")
                self.error.emit(f"Erro ao fazer login:\n{error_msg}")
        
        except Exception as e:
            logger.error(f"Erro na LoginWorker: {str(e)}")
            print(f"[DEBUG] ERRO GERAL: {str(e)}")
            import traceback
            traceback.print_exc()
            self.error.emit(f"Erro ao fazer login:\n{str(e)}")
        
        finally:
            # Parar servidor HTTP
            if self.auth_server:
                self.auth_server.shutdown()


class AzureLoginDialog(QDialog):
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.access_token = None
        self.user_info = None
        self.login_worker = None
        self.login_btn = None
        
        self.setWindowTitle("Login - LogLife")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMinimumHeight(200)
        
        self.setup_ui()
    
    def setup_ui(self):
        """Criar interface com botão único"""
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(40, 40, 40, 40)
        
        # Título
        title = QLabel("Sistema de Recebimento - LogLife")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        # Descrição
        subtitle = QLabel("Faça login com sua conta Microsoft corporativa")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)
        
        # Espaço
        layout.addSpacing(20)
        
        # Botão Login Azure AD
        self.login_btn = QPushButton("🔐 Login com Azure AD")
        self.login_btn.setMinimumHeight(50)
        self.login_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078D4;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1084D7;
            }
            QPushButton:pressed {
                background-color: #005A9E;
            }
        """)
        self.login_btn.clicked.connect(self.on_login_clicked)
        layout.addWidget(self.login_btn)
        
        # Botão Cancelar
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setMinimumHeight(40)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)
        
        # Espaço final
        layout.addStretch()
        
        self.setLayout(layout)
    
    def on_login_clicked(self):
        """Iniciar login em thread separada"""
        logger.info("Iniciando processo de login...")
        print(f"[DEBUG] on_login_clicked chamado")
        
        # Desabilitar botão durante login
        self.login_btn.setEnabled(False)
        self.login_btn.setText("⏳ Abrindo navegador...")
        
        # Criar e iniciar worker
        print(f"[DEBUG] Criando LoginWorker...")
        self.login_worker = LoginWorker()
        print(f"[DEBUG] Conectando signals...")
        self.login_worker.success.connect(self.on_login_success)
        self.login_worker.error.connect(self.on_login_error)
        print(f"[DEBUG] Iniciando worker thread...")
        self.login_worker.start()
        print(f"[DEBUG] Worker thread iniciada")
        
        # Mostrar instrução
        QMessageBox.information(
            self,
            "Login",
            "Seu navegador será aberto para autenticação.\n\n"
            "Faça login com sua conta corporativa.\n\n"
            "A aplicação aguardará a conclusão."
        )
    
    def on_login_success(self, user_info):
        """Login bem-sucedido"""
        logger.info("Login bem-sucedido!")
        print(f"[DEBUG] on_login_success chamado com: {user_info}")
        self.access_token = self.login_worker.access_token
        
        # Incluir o access_token no user_info para facilitar o uso
        user_info['access_token'] = self.access_token
        self.user_info = user_info
        
        QMessageBox.information(
            self,
            "Login Bem-Sucedido",
            f"Bem-vindo, {user_info.get('email', 'Usuario')}!"
        )
        
        self.accept()
    
    def on_login_error(self, error_msg):
        """Erro no login"""
        logger.error(f"Erro no login: {error_msg}")
        print(f"[DEBUG] on_login_error chamado: {error_msg}")
        
        # Reabilitar botão
        self.login_btn.setEnabled(True)
        self.login_btn.setText("🔐 Login com Azure AD")
        
        QMessageBox.critical(
            self,
            "Erro de Login",
            error_msg
        )
    
    def get_token(self):
        """Retorna o token de acesso obtido"""
        return self.access_token
    
    def get_user_info(self):
        """Retorna informações do usuário"""
        return self.user_info
