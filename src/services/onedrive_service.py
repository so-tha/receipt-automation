"""
OneDrive Personal Service - Integração simples com OneDrive pessoal via Microsoft Graph.

Este serviço é responsável por:
1. Autenticar com Azure AD (via MSAL - client credentials)
2. Acessar /me/drive (OneDrive pessoal)
3. Listar e baixar arquivos
4. Simples e direto - sem precisar de site_id ou drive_id
"""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import logging
import requests
import msal

logger = logging.getLogger(__name__)


@dataclass
class OneDriveFile:
    """Representa um arquivo no OneDrive"""
    item_id: str
    name: str
    size: int
    created_at: datetime
    modified_at: datetime
    web_url: str
    
    def __str__(self) -> str:
        return f"{self.name} ({self.size} bytes)"


class OneDrivePersonalService:
    """Serviço simplificado para OneDrive pessoal"""
    
    GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        user_email: str = None,
        user_principal_name: str = None
    ):
        """
        Inicializar serviço OneDrive.
        
        Args:
            tenant_id: ID do tenant Azure
            client_id: ID do app Azure
            client_secret: Secret do app
            user_email: Email do usuário (opcional, apenas para logs)
            user_principal_name: UPN para acesso (ex: thais_souza@loglifelogistica.com.br)
        """
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_email = user_email
        self.user_principal_name = user_principal_name
        self.access_token = None
        self._using_user_token = False  # Flag para indicar se estamos usando token delegado
        self.authority_url = f"https://login.microsoftonline.com/{tenant_id}"
        self.scopes = ["https://graph.microsoft.com/.default"]
        self.session = requests.Session()
        
        logger.info(f"OneDrivePersonalService inicializado para: {user_email or 'acesso de app'}")
    
    def authenticate(self) -> bool:
        """
        Autenticar com Azure AD usando Client Credentials (app-only).
        Usa /users/{UPN}/drive para acessar o drive.
        
        Returns:
            True se autenticacao bem-sucedida
        """
        try:
            self._using_user_token = False  # Reset flag - estamos usando app token
            
            app = msal.ConfidentialClientApplication(
                self.client_id,
                authority=self.authority_url,
                client_credential=self.client_secret
            )
            
            result = app.acquire_token_for_client(scopes=self.scopes)
            
            if "access_token" in result:
                self.access_token = result["access_token"]
                logger.info("[OK] Token de app obtido com sucesso")
                return True
            else:
                error = result.get("error_description", "Erro desconhecido")
                logger.error(f"Erro na autenticacao: {error}")
                return False
        
        except Exception as e:
            logger.error(f"Erro ao autenticar: {str(e)}")
            return False
    
    def use_user_token(self, user_token: str):
        """
        Usar token de usuario existente (delegated auth).
        Quando usamos token de usuario, acessamos /me/drive.
        """
        self.access_token = user_token
        self._using_user_token = True
        logger.info("[OK] Token de usuario armazenado - usando /me/drive")
    
    def _get_headers(self) -> Dict[str, str]:
        """Headers padrão para requisições"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Dict = None,
        params: Dict = None,
        timeout: int = 30
    ) -> Tuple[bool, Any]:
        """Fazer requisicao ao Microsoft Graph"""
        if not self.access_token:
            logger.error("Sem token. Execute authenticate() primeiro")
            return False, None
        
        url = f"{self.GRAPH_BASE}{endpoint}"
        headers = self._get_headers()
        
        try:
            response = requests.request(
                method=method,
                url=url,
                json=data,
                params=params,
                headers=headers,
                timeout=timeout
            )
            
            if response.status_code in [200, 201, 204]:
                try:
                    return True, response.json() if response.text else {}
                except:
                    return True, response.text
            else:
                error = response.json() if response.text else response.reason
                logger.error(f"Erro {response.status_code}: {error}")
                return False, error
        
        except Exception as e:
            logger.error(f"Erro na requisicao: {str(e)}")
            return False, str(e)
    
    def get_drive_info(self) -> bool:
        """
        Obter informacoes do OneDrive pessoal.
        
        Funciona tanto com delegated como application authentication.
        
        Returns:
            True se bem-sucedido
        """
        try:
            logger.info("Obtendo informacoes do OneDrive pessoal...")
            
            # Usar _get_drive_path() que respeita o tipo de token
            endpoint = f"{self._get_drive_path()}"
            
            logger.info(f"   Tentando: {endpoint}")
            success, response = self._make_request("GET", endpoint)
            
            if success and response:
                drive_id = response.get("id")
                quota = response.get("quota", {})
                logger.info(f"[OK] Drive ID: {drive_id}")
                logger.info(f"   Usado: {quota.get('used', 0) / (1024**3):.2f} GB")
                logger.info(f"   Total: {quota.get('total', 0) / (1024**3):.2f} GB")
                return True
            
            logger.error(f"Nao conseguiu acessar o drive")
            logger.info("   Dica: Para application auth, use user_principal_name no init")
            return False
        
        except Exception as e:
            logger.error(f"Erro: {str(e)}")
            return False
    
    def list_files(self, folder_path: str = "/") -> List[OneDriveFile]:
        """
        Listar arquivos no OneDrive.
        
        Args:
            folder_path: Caminho da pasta (ex: "/", "/Documentos")
        
        Returns:
            Lista de arquivos
        """
        try:
            logger.info(f"Listando arquivos em: {folder_path}")
            
            # Usar _get_drive_path() que respeita o tipo de token
            drive_path = self._get_drive_path()
            
            if folder_path == "/":
                endpoint = f"{drive_path}/root/children"
            else:
                folder_encoded = folder_path.replace(" ", "%20")
                endpoint = f"{drive_path}/root:{folder_encoded}:/children"
            
            success, response = self._make_request("GET", endpoint)
            
            if success and response:
                files = []
                items = response.get("value", [])
                logger.info(f"   Encontrados {len(items)} itens")
                
                for item in items:
                    if "file" in item:
                        file = OneDriveFile(
                            item_id=item["id"],
                            name=item["name"],
                            size=item.get("size", 0),
                            created_at=datetime.fromisoformat(
                                item.get("createdDateTime", "").replace("Z", "+00:00")
                            ),
                            modified_at=datetime.fromisoformat(
                                item.get("lastModifiedDateTime", "").replace("Z", "+00:00")
                            ),
                            web_url=item.get("webUrl", "")
                        )
                        files.append(file)
                        logger.info(f"   - {file.name} ({file.size / 1024:.1f} KB)")
                
                return files
            else:
                logger.error(f"Erro ao listar: {response}")
                return []
        
        except Exception as e:
            logger.error(f"Erro: {str(e)}")
            return []
    
    def download_file(self, file_id: str, output_path: Path) -> bool:
        """
        Baixar arquivo do OneDrive.
        
        Args:
            file_id: ID do arquivo no OneDrive
            output_path: Caminho local para salvar
        
        Returns:
            True se bem-sucedido
        """
        try:
            logger.info(f"Baixando arquivo: {file_id}")
            
            # Usar _get_drive_path() que respeita o tipo de token
            drive_path = self._get_drive_path()
            endpoint = f"{drive_path}/items/{file_id}/content"
            
            if not self.access_token:
                logger.error("Sem token")
                return False
            
            url = f"{self.GRAPH_BASE}{endpoint}"
            headers = self._get_headers()
            
            response = requests.get(url, headers=headers, timeout=60)
            
            if response.status_code == 200:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                
                logger.info(f"[OK] Arquivo salvo em: {output_path}")
                logger.info(f"   Tamanho: {len(response.content) / 1024:.1f} KB")
                return True
            else:
                logger.error(f"Erro {response.status_code} ao baixar")
                return False
        
        except Exception as e:
            logger.error(f"Erro: {str(e)}")
            return False
    
    def find_file(self, filename: str, folder_path: str = "/") -> Optional[OneDriveFile]:
        """
        Procurar um arquivo por nome.
        
        Args:
            filename: Nome do arquivo (parcial ou completo)
            folder_path: Pasta onde procurar
        
        Returns:
            OneDriveFile se encontrado, None se não
        """
        logger.info(f"Procurando: {filename}")
        
        files = self.list_files(folder_path)
        
        for f in files:
            if filename.lower() in f.name.lower():
                logger.info(f"[OK] Encontrado: {f.name}")
                return f
        
        logger.warning(f"Arquivo nao encontrado: {filename}")
        return None
    
    def find_file_by_name(self, filename: str, folder_path: str = "/") -> Optional[OneDriveFile]:
        """Alias para find_file - compatibilidade com SharePointService"""
        return self.find_file(filename, folder_path)
    
    def upload_file(
        self,
        local_path: Path,
        folder_path: str = "/",
        overwrite: bool = True
    ) -> Optional[OneDriveFile]:
        """
        Fazer upload de arquivo para OneDrive.
        
        Args:
            local_path: Caminho do arquivo local
            folder_path: Pasta de destino no OneDrive (ex: "/" para raiz)
            overwrite: Se True, sobrescreve arquivo existente
        
        Returns:
            OneDriveFile se bem-sucedido, None caso contrario
        """
        try:
            if not local_path.exists():
                logger.error(f"Arquivo nao existe: {local_path}")
                return None
            
            file_name = local_path.name
            logger.info(f"Enviando: {file_name} para {folder_path}")
            
            # Usar _get_drive_path() que respeita o tipo de token
            drive_path = self._get_drive_path()
            
            # Construir endpoint
            if folder_path == "/" or folder_path == "":
                endpoint = f"{drive_path}/root:/{file_name}:/content"
            else:
                folder_clean = folder_path.strip("/")
                endpoint = f"{drive_path}/root:/{folder_clean}/{file_name}:/content"
            
            # Ler arquivo
            with open(local_path, "rb") as f:
                file_content = f.read()
            
            url = f"{self.GRAPH_BASE}{endpoint}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/octet-stream"
            }
            
            # Configurar comportamento de conflito
            params = {}
            if overwrite:
                params["@microsoft.graph.conflictBehavior"] = "replace"
            else:
                params["@microsoft.graph.conflictBehavior"] = "rename"
            
            response = requests.put(
                url,
                data=file_content,
                headers=headers,
                params=params,
                timeout=120
            )
            
            if response.status_code in [200, 201]:
                data = response.json()
                file = OneDriveFile(
                    item_id=data["id"],
                    name=data["name"],
                    size=data["size"],
                    created_at=datetime.fromisoformat(
                        data.get("createdDateTime", "").replace("Z", "+00:00")
                    ),
                    modified_at=datetime.fromisoformat(
                        data.get("lastModifiedDateTime", "").replace("Z", "+00:00")
                    ),
                    web_url=data.get("webUrl", "")
                )
                logger.info(f"[OK] Arquivo enviado: {file_name}")
                return file
            else:
                logger.error(f"Erro ao fazer upload: {response.status_code} - {response.text}")
                return None
        
        except Exception as e:
            logger.error(f"Erro ao fazer upload: {str(e)}")
            return None
    
    def download_file_by_path(self, file_path: str, output_path: Path) -> bool:
        """
        Baixar arquivo pelo caminho no OneDrive.
        
        Args:
            file_path: Caminho do arquivo no OneDrive (ex: "/PLANILHA.xlsx")
            output_path: Caminho local para salvar
        
        Returns:
            True se bem-sucedido
        """
        try:
            logger.info(f"Baixando: {file_path}")
            
            # Usar _get_drive_path() que respeita o tipo de token
            drive_path = self._get_drive_path()
            
            # Construir endpoint
            file_path_clean = file_path.strip("/")
            endpoint = f"{drive_path}/root:/{file_path_clean}:/content"
            
            url = f"{self.GRAPH_BASE}{endpoint}"
            headers = self._get_headers()
            
            response = requests.get(url, headers=headers, timeout=60)
            
            if response.status_code == 200:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                logger.info(f"[OK] Arquivo salvo em: {output_path}")
                return True
            else:
                logger.error(f"Erro {response.status_code} ao baixar")
                return False
        
        except Exception as e:
            logger.error(f"Erro: {str(e)}")
            return False
    
    def _get_drive_path(self) -> str:
        """
        Retorna o path base do drive.
        - Token de usuario (delegado): usa /me/drive
        - Token de app (client credentials): usa /users/{UPN}/drive
        """
        # Se estamos usando token de usuario, SEMPRE usar /me/drive
        if getattr(self, '_using_user_token', False):
            return "/me/drive"
        
        # Para client credentials, precisa do UPN
        if self.user_principal_name:
            return f"/users/{self.user_principal_name}/drive"
        
        return "/me/drive"
    
    def adicionar_linhas_excel(
        self,
        file_path: str,
        sheet_name: str,
        rows: list
    ) -> Tuple[bool, str]:
        """
        Adiciona linhas diretamente em uma planilha Excel no OneDrive.
        Usa a API Excel do Microsoft Graph - nao precisa baixar/re-subir o arquivo.
        
        Args:
            file_path: Caminho do arquivo (ex: "PLANILHA.xlsx")
            sheet_name: Nome da aba
            rows: Lista de linhas, cada linha e uma lista de valores
            
        Returns:
            Tupla (sucesso, mensagem)
        """
        try:
            from urllib.parse import quote
            
            if not rows:
                return False, "Nenhuma linha para adicionar"
            
            logger.info(f"Adicionando {len(rows)} linhas em {file_path} / {sheet_name}")
            
            drive_path = self._get_drive_path()
            file_path_clean = file_path.strip("/")
            
            # Encode para URL (espaços, parênteses, etc)
            file_path_encoded = quote(file_path_clean, safe='/')
            sheet_name_encoded = quote(sheet_name, safe='')
            
            # 1. Obter a planilha usada para saber a ultima linha
            endpoint = f"{drive_path}/root:/{file_path_encoded}:/workbook/worksheets/{sheet_name_encoded}/usedRange"
            success, response = self._make_request("GET", endpoint, timeout=60)
            
            if not success:
                # Aba pode nao existir ou estar vazia - tentar criar
                logger.warning(f"Nao foi possivel obter range usado: {response}")
                # Tentar inserir a partir da linha 2 (assumindo cabecalho na linha 1)
                start_row = 2
            else:
                # Calcular proxima linha apos os dados existentes
                row_count = response.get("rowCount", 1)
                start_row = row_count + 1
            
            logger.info(f"Inserindo a partir da linha {start_row}")
            
            # 2. Determinar o range para inserir
            num_cols = len(rows[0]) if rows else 1
            num_rows = len(rows)
            
            # Converter numero de coluna para letra (1=A, 2=B, etc)
            def col_letter(n):
                result = ""
                while n > 0:
                    n, remainder = divmod(n - 1, 26)
                    result = chr(65 + remainder) + result
                return result
            
            end_col = col_letter(num_cols)
            end_row = start_row + num_rows - 1
            range_address = f"A{start_row}:{end_col}{end_row}"
            
            logger.info(f"Range de destino: {range_address}")
            
            # 3. Inserir os dados usando PATCH no range
            endpoint = f"{drive_path}/root:/{file_path_encoded}:/workbook/worksheets/{sheet_name_encoded}/range(address='{range_address}')"
            
            data = {
                "values": rows
            }
            
            url = f"{self.GRAPH_BASE}{endpoint}"
            headers = self._get_headers()
            
            response = requests.patch(
                url,
                json=data,
                headers=headers,
                timeout=120
            )
            
            if response.status_code in [200, 201]:
                msg = f"Adicionadas {num_rows} linhas no range {range_address}"
                logger.info(f"[OK] {msg}")
                return True, msg
            else:
                error_msg = response.text[:500] if response.text else response.reason
                logger.error(f"Erro ao inserir dados: {response.status_code} - {error_msg}")
                return False, f"Erro {response.status_code}: {error_msg}"
        
        except Exception as e:
            msg = f"Erro ao adicionar linhas: {str(e)}"
            logger.error(msg)
            return False, msg
    
    def obter_ultima_linha(self, file_path: str, sheet_name: str) -> int:
        """
        Obtem o numero da ultima linha com dados em uma aba.
        
        Returns:
            Numero da ultima linha, ou 1 se vazia/erro
        """
        try:
            drive_path = self._get_drive_path()
            file_path_clean = file_path.strip("/")
            
            endpoint = f"{drive_path}/root:/{file_path_clean}:/workbook/worksheets/{sheet_name}/usedRange"
            success, response = self._make_request("GET", endpoint, timeout=60)
            
            if success and response:
                return response.get("rowCount", 1)
            return 1
        except:
            return 1
    
    def listar_abas_excel(self, file_path: str) -> Tuple[bool, list]:
        """
        Lista todas as abas de uma planilha Excel no OneDrive.
        
        Args:
            file_path: Caminho do arquivo (ex: "PLANILHA.xlsx")
            
        Returns:
            Tupla (sucesso, lista de nomes das abas)
        """
        try:
            from urllib.parse import quote
            
            drive_path = self._get_drive_path()
            file_path_clean = file_path.strip("/")
            file_path_encoded = quote(file_path_clean, safe='/')
            
            logger.info(f"Listando abas de: {file_path_clean}")
            
            endpoint = f"{drive_path}/root:/{file_path_encoded}:/workbook/worksheets"
            success, response = self._make_request("GET", endpoint, timeout=60)
            
            if success and response:
                sheets = [ws.get("name") for ws in response.get("value", [])]
                logger.info(f"[OK] Abas encontradas: {sheets}")
                return True, sheets
            else:
                logger.error(f"Erro ao listar abas: {response}")
                return False, []
        
        except Exception as e:
            logger.error(f"Erro ao listar abas: {str(e)}")
            return False, []
    
    def verificar_arquivo_existe(self, file_path: str) -> Tuple[bool, dict]:
        """
        Verifica se um arquivo existe no OneDrive.
        
        Args:
            file_path: Caminho do arquivo
            
        Returns:
            Tupla (existe, info do arquivo)
        """
        try:
            from urllib.parse import quote
            
            drive_path = self._get_drive_path()
            file_path_clean = file_path.strip("/")
            file_path_encoded = quote(file_path_clean, safe='/')
            
            logger.info(f"Verificando arquivo: {file_path_clean}")
            
            endpoint = f"{drive_path}/root:/{file_path_encoded}"
            success, response = self._make_request("GET", endpoint, timeout=30)
            
            if success and response:
                logger.info(f"[OK] Arquivo encontrado: {response.get('name')}")
                return True, response
            else:
                logger.warning(f"Arquivo nao encontrado: {file_path_clean}")
                return False, {}
        
        except Exception as e:
            logger.error(f"Erro ao verificar arquivo: {str(e)}")
            return False, {}
    
    def ler_dados_aba(self, file_path: str, sheet_name: str) -> Tuple[bool, List[List]]:
        """
        Lê todos os dados de uma aba da planilha.
        
        Args:
            file_path: Caminho do arquivo
            sheet_name: Nome da aba
            
        Returns:
            Tupla (sucesso, lista de linhas com valores)
        """
        try:
            from urllib.parse import quote
            
            drive_path = self._get_drive_path()
            file_path_clean = file_path.strip("/")
            file_path_encoded = quote(file_path_clean, safe='/')
            sheet_name_encoded = quote(sheet_name, safe='')
            
            logger.info(f"Lendo dados de: {file_path_clean} / {sheet_name}")
            
            endpoint = f"{drive_path}/root:/{file_path_encoded}:/workbook/worksheets/{sheet_name_encoded}/usedRange"
            success, response = self._make_request("GET", endpoint, timeout=120)
            
            if success and response:
                values = response.get("values", [])
                logger.info(f"[OK] Lidas {len(values)} linhas")
                return True, values
            else:
                logger.error(f"Erro ao ler dados: {response}")
                return False, []
        
        except Exception as e:
            logger.error(f"Erro ao ler dados: {str(e)}")
            return False, []
    
    def inserir_em_linha_especifica(
        self,
        file_path: str,
        sheet_name: str,
        linha_inicio: int,
        rows: List[List]
    ) -> Tuple[bool, str]:
        """
        Insere dados em uma linha específica da planilha.
        
        Args:
            file_path: Caminho do arquivo
            sheet_name: Nome da aba
            linha_inicio: Número da linha onde começar (1-based)
            rows: Lista de linhas a inserir
            
        Returns:
            Tupla (sucesso, mensagem)
        """
        try:
            from urllib.parse import quote
            
            if not rows:
                return False, "Nenhuma linha para inserir"
            
            drive_path = self._get_drive_path()
            file_path_clean = file_path.strip("/")
            file_path_encoded = quote(file_path_clean, safe='/')
            sheet_name_encoded = quote(sheet_name, safe='')
            
            logger.info(f"Inserindo {len(rows)} linhas a partir da linha {linha_inicio}")
            
            num_cols = max(len(row) for row in rows)
            num_rows = len(rows)
            
            def col_letter(n):
                result = ""
                while n > 0:
                    n, remainder = divmod(n - 1, 26)
                    result = chr(65 + remainder) + result
                return result
            
            end_col = col_letter(num_cols)
            end_row = linha_inicio + num_rows - 1
            range_address = f"A{linha_inicio}:{end_col}{end_row}"
            
            logger.info(f"Range de destino: {range_address}")
            
            endpoint = f"{drive_path}/root:/{file_path_encoded}:/workbook/worksheets/{sheet_name_encoded}/range(address='{range_address}')"
            
            rows_padded = []
            for row in rows:
                padded = list(row) + [''] * (num_cols - len(row))
                rows_padded.append(padded)
            
            data = {"values": rows_padded}
            
            url = f"{self.GRAPH_BASE}{endpoint}"
            headers = self._get_headers()
            
            response = requests.patch(
                url,
                json=data,
                headers=headers,
                timeout=120
            )
            
            if response.status_code in [200, 201]:
                msg = f"Inseridas {num_rows} linhas no range {range_address}"
                logger.info(f"[OK] {msg}")
                return True, msg
            else:
                error_msg = response.text[:500] if response.text else response.reason
                logger.error(f"Erro ao inserir: {response.status_code} - {error_msg}")
                return False, f"Erro {response.status_code}: {error_msg}"
        
        except Exception as e:
            msg = f"Erro ao inserir linhas: {str(e)}"
            logger.error(msg)
            return False, msg
    
    def inserir_linhas_antes_de(
        self,
        file_path: str,
        sheet_name: str,
        linha_referencia: int,
        num_linhas: int
    ) -> Tuple[bool, str]:
        """
        Insere linhas em branco antes de uma linha de referência.
        Isso desloca as linhas existentes para baixo.
        
        Args:
            file_path: Caminho do arquivo
            sheet_name: Nome da aba
            linha_referencia: Linha antes da qual inserir (1-based)
            num_linhas: Quantidade de linhas a inserir
            
        Returns:
            Tupla (sucesso, mensagem)
        """
        try:
            from urllib.parse import quote
            
            drive_path = self._get_drive_path()
            file_path_clean = file_path.strip("/")
            file_path_encoded = quote(file_path_clean, safe='/')
            sheet_name_encoded = quote(sheet_name, safe='')
            
            logger.info(f"Inserindo {num_linhas} linhas antes da linha {linha_referencia}")
            
            range_address = f"A{linha_referencia}:A{linha_referencia + num_linhas - 1}"
            
            endpoint = f"{drive_path}/root:/{file_path_encoded}:/workbook/worksheets/{sheet_name_encoded}/range(address='{range_address}')/insert"
            
            data = {"shift": "Down"}
            
            url = f"{self.GRAPH_BASE}{endpoint}"
            headers = self._get_headers()
            
            response = requests.post(
                url,
                json=data,
                headers=headers,
                timeout=60
            )
            
            if response.status_code in [200, 201]:
                msg = f"Inseridas {num_linhas} linhas antes da linha {linha_referencia}"
                logger.info(f"[OK] {msg}")
                return True, msg
            else:
                error_msg = response.text[:500] if response.text else response.reason
                logger.error(f"Erro ao inserir linhas: {response.status_code} - {error_msg}")
                return False, f"Erro {response.status_code}: {error_msg}"
        
        except Exception as e:
            msg = f"Erro ao inserir linhas: {str(e)}"
            logger.error(msg)
            return False, msg