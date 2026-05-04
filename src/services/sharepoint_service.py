"""
SharePoint Service - Integração com Microsoft Graph para SharePoint da empresa.

Este serviço é responsável por:
1. Autenticar com Azure AD (via MSAL)
2. Fazer requisições ao Microsoft Graph
3. Baixar arquivos do SharePoint
4. Fazer upload de dados
5. Gerenciar versões de arquivo
"""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import logging
import json

import requests
import msal

logger = logging.getLogger(__name__)


@dataclass
class SharePointFile:
    """Representa um arquivo no SharePoint"""
    item_id: str
    name: str
    size: int
    created_at: datetime
    modified_at: datetime
    web_url: str
    version: str = "1.0"
    
    def __str__(self) -> str:
        return f"{self.name} ({self.size} bytes) - v{self.version}"


@dataclass
class SharePointFolder:
    """Representa uma pasta no SharePoint"""
    folder_id: str
    name: str
    path: str
    item_count: int = 0
    
    def __str__(self) -> str:
        return f"{self.name} ({self.item_count} items)"


class SharePointService:
    """Serviço de integração com SharePoint via Microsoft Graph"""
    
    # Endpoints do Microsoft Graph
    GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    GRAPH_BETA = "https://graph.microsoft.com/beta"
    
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        site_url: str,
        user_email: str = None
    ):
        """
        Inicializar serviço SharePoint.
        
        Args:
            tenant_id: ID do tenant Azure (ex: 7df8c4cf-1a79-4386...)
            client_id: ID do app Azure registrado
            client_secret: Secret do app (para aplicação server-side)
            site_url: URL do site SharePoint (ex: https://empresa.sharepoint.com/sites/seu-site)
            user_email: Email do usuário (opcional, para fluxos com usuario)
        """
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.site_url = site_url
        self.user_email = user_email
        
        self.access_token = None
        self.token_expiry = None
        self.site_id = None
        self.drive_id = None
        
        # Endpoints de autenticação
        self.authority_url = f"https://login.microsoftonline.com/{tenant_id}"
        self.scopes = ["https://graph.microsoft.com/.default"]
        
        # Sessão HTTP
        self.session = requests.Session()
        
        logger.info(f"SharePointService inicializado para: {site_url}")
    
    def authenticate(self) -> bool:
        """
        Autenticar com Azure AD usando Client Credentials.
        
        **Importante**: Este fluxo é para aplicações servidor.
        Para autenticação do usuário, use o token existente do MSAL.
        
        Returns:
            True se autenticação bem-sucedida
        """
        try:
            app = msal.ConfidentialClientApplication(
                self.client_id,
                authority=self.authority_url,
                client_credential=self.client_secret
            )
            
            result = app.acquire_token_for_client(scopes=self.scopes)
            
            if "access_token" in result:
                self.access_token = result["access_token"]
                self.token_expiry = result.get("expires_in", 3600)
                logger.info("✅ Token de autenticação obtido com sucesso")
                return True
            else:
                error = result.get("error_description", "Erro desconhecido")
                logger.error(f"❌ Erro na autenticação: {error}")
                return False
        
        except Exception as e:
            logger.error(f"❌ Erro ao autenticar: {str(e)}")
            return False
    
    def use_user_token(self, user_token: str):
        """
        Usar token de usuário existente (do fluxo de login do app).
        
        Args:
            user_token: Token JWT obtido via MSAL/Azure AD
        """
        self.access_token = user_token
        logger.info("✅ Token de usuário armazenado")
    
    def _get_headers(self) -> Dict[str, str]:
        """Retorna headers padrão para requisições com autenticação"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Dict = None,
        params: Dict = None,
        timeout: int = 30
    ) -> Tuple[bool, Any]:
        """
        Fazer requisição ao Microsoft Graph.
        
        Args:
            method: GET, POST, PATCH, DELETE
            endpoint: URL do endpoint (sem base)
            data: Dados para POST/PATCH
            params: Query parameters
            timeout: Timeout em segundos
        
        Returns:
            (success, response_data)
        """
        if not self.access_token:
            logger.error("❌ Sem token de acesso. Execute authenticate() primeiro")
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
                logger.error(f"❌ Erro {response.status_code}: {error}")
                return False, error
        
        except Exception as e:
            logger.error(f"❌ Erro na requisição: {str(e)}")
            return False, str(e)
    
    def get_site_info(self) -> bool:
        """
        Obter informações do site SharePoint (ID, Drive ID, etc).
        
        **ESSENCIAL**: Execute isso após autenticar!
        
        Returns:
            True se obtivo as informações
        """
        try:
            # Formatar URL do site para a chamada
            site_path = self.site_url.replace("https://", "").replace(".sharepoint.com", "")
            site_parts = site_path.split("/sites/")
            
            if len(site_parts) != 2:
                logger.error(f"❌ URL do site inválida: {self.site_url}")
                return False
            
            hostname, site_name = site_parts
            
            # Chamada: GET /sites/{hostname}:/sites/{site_name}
            endpoint = f"/sites/{hostname}:/sites/{site_name}"
            success, response = self._make_request("GET", endpoint)
            
            if success and response:
                self.site_id = response.get("id")
                logger.info(f"✅ Site ID obtido: {self.site_id}")
                
                # Obter Drive ID (padrão da biblioteca de documentos)
                return self.get_drive_id()
            else:
                logger.error("❌ Não foi possível obter informações do site")
                return False
        
        except Exception as e:
            logger.error(f"❌ Erro ao obter site info: {str(e)}")
            return False
    
    def get_drive_id(self) -> bool:
        """
        Obter ID da biblioteca de documentos padrão (drive).
        
        Returns:
            True se obtevo o Drive ID
        """
        if not self.site_id:
            logger.error("❌ Site ID não configurado. Execute get_site_info() primeiro")
            return False
        
        try:
            endpoint = f"/sites/{self.site_id}/drive"
            success, response = self._make_request("GET", endpoint)
            
            if success and response:
                self.drive_id = response.get("id")
                logger.info(f"✅ Drive ID obtido: {self.drive_id}")
                return True
            else:
                logger.error("❌ Não foi possível obter Drive ID")
                return False
        
        except Exception as e:
            logger.error(f"❌ Erro ao obter Drive ID: {str(e)}")
            return False
    
    def list_files(self, folder_path: str = "/") -> List[SharePointFile]:
        """
        Listar arquivos em um diretório do SharePoint.
        
        Args:
            folder_path: Caminho da pasta (ex: "/Documentos", "/")
        
        Returns:
            Lista de SharePointFile
        """
        if not self.drive_id:
            logger.error("❌ Drive ID não configurado")
            return []
        
        try:
            # URL do item pelo caminho
            endpoint = f"/me/drive/root{folder_path}:/children"
            success, response = self._make_request("GET", endpoint)
            
            if success and response:
                files = []
                for item in response.get("value", []):
                    if "file" in item:  # É um arquivo
                        file = SharePointFile(
                            item_id=item["id"],
                            name=item["name"],
                            size=item["size"],
                            created_at=datetime.fromisoformat(item.get("createdDateTime", "")),
                            modified_at=datetime.fromisoformat(item.get("lastModifiedDateTime", "")),
                            web_url=item.get("webUrl", "")
                        )
                        files.append(file)
                        logger.info(f"  📄 {file.name}")
                
                logger.info(f"✅ {len(files)} arquivos encontrados")
                return files
            else:
                logger.error(f"❌ Erro ao listar arquivos: {response}")
                return []
        
        except Exception as e:
            logger.error(f"❌ Erro ao listar arquivos: {str(e)}")
            return []
    
    def download_file(self, file_id: str, output_path: Path) -> bool:
        """
        Baixar arquivo do SharePoint.
        
        Args:
            file_id: ID do arquivo no SharePoint
            output_path: Caminho local para salvar
        
        Returns:
            True se download bem-sucedido
        """
        try:
            endpoint = f"/me/drive/items/{file_id}/content"
            
            url = f"{self.GRAPH_BASE}{endpoint}"
            headers = self._get_headers()
            
            response = requests.get(url, headers=headers, timeout=60)
            
            if response.status_code == 200:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"✅ Arquivo baixado: {output_path}")
                return True
            else:
                logger.error(f"❌ Erro ao baixar arquivo: {response.status_code}")
                return False
        
        except Exception as e:
            logger.error(f"❌ Erro ao baixar: {str(e)}")
            return False
    
    def upload_file(
        self,
        local_path: Path,
        folder_path: str = "/",
        overwrite: bool = True
    ) -> Optional[SharePointFile]:
        """
        Fazer upload de arquivo para SharePoint.
        
        Args:
            local_path: Caminho do arquivo local
            folder_path: Pasta de destino no SharePoint (ex: "/Sincronizações")
            overwrite: Se True, sobrescreve arquivo existente
        
        Returns:
            SharePointFile se bem-sucedido, None caso contrário
        """
        if not self.drive_id:
            logger.error("❌ Drive ID não configurado")
            return None
        
        if not local_path.exists():
            logger.error(f"❌ Arquivo não existe: {local_path}")
            return None
        
        try:
            file_name = local_path.name
            
            # Endpoint: PUT /me/drive/root/{folder_path}/{file_name}:/content
            endpoint = f"/me/drive/root{folder_path.rstrip('/')}/{file_name}:/content"
            
            with open(local_path, "rb") as f:
                file_content = f.read()
            
            url = f"{self.GRAPH_BASE}{endpoint}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/octet-stream"
            }
            
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
                timeout=60
            )
            
            if response.status_code in [200, 201]:
                data = response.json()
                file = SharePointFile(
                    item_id=data["id"],
                    name=data["name"],
                    size=data["size"],
                    created_at=datetime.fromisoformat(data.get("createdDateTime", "")),
                    modified_at=datetime.fromisoformat(data.get("lastModifiedDateTime", "")),
                    web_url=data.get("webUrl", "")
                )
                logger.info(f"✅ Arquivo enviado: {file_name} → {folder_path}")
                return file
            else:
                logger.error(f"❌ Erro ao fazer upload: {response.status_code} - {response.text}")
                return None
        
        except Exception as e:
            logger.error(f"❌ Erro ao fazer upload: {str(e)}")
            return None
    
    def get_file_content_as_text(self, file_id: str, encoding: str = "utf-8") -> Optional[str]:
        """
        Baixar conteúdo de arquivo como texto (para CSV, TXT, etc).
        
        Args:
            file_id: ID do arquivo
            encoding: Encoding do arquivo
        
        Returns:
            Conteúdo do arquivo como string, None se erro
        """
        try:
            endpoint = f"/me/drive/items/{file_id}/content"
            url = f"{self.GRAPH_BASE}{endpoint}"
            headers = self._get_headers()
            
            response = requests.get(url, headers=headers, timeout=60)
            
            if response.status_code == 200:
                return response.content.decode(encoding)
            else:
                logger.error(f"❌ Erro ao baixar conteúdo: {response.status_code}")
                return None
        
        except Exception as e:
            logger.error(f"❌ Erro ao obter conteúdo: {str(e)}")
            return None
    
    def find_file_by_name(self, file_name: str, folder_path: str = "/") -> Optional[SharePointFile]:
        """
        Procurar arquivo por nome no SharePoint.
        
        Args:
            file_name: Nome do arquivo (ex: "DATABASE.csv")
            folder_path: Pasta onde procurar
        
        Returns:
            SharePointFile se encontrado, None caso contrário
        """
        files = self.list_files(folder_path)
        for file in files:
            if file.name == file_name:
                return file
        
        logger.warning(f"⚠️  Arquivo não encontrado: {file_name}")
        return None
    
    def delete_file(self, file_id: str) -> bool:
        """
        Deletar arquivo do SharePoint.
        
        Args:
            file_id: ID do arquivo
        
        Returns:
            True se deletado com sucesso
        """
        try:
            endpoint = f"/me/drive/items/{file_id}"
            success, response = self._make_request("DELETE", endpoint)
            
            if success:
                logger.info(f"[OK] Arquivo deletado: {file_id}")
                return True
            else:
                logger.error(f"Erro ao deletar: {response}")
                return False
        
        except Exception as e:
            logger.error(f"Erro ao deletar: {str(e)}")
            return False
    
    def adicionar_linhas_excel(
        self,
        file_path: str,
        sheet_name: str,
        rows: list
    ) -> tuple:
        """
        Adiciona linhas diretamente em uma planilha Excel no SharePoint.
        Usa a API Excel do Microsoft Graph - nao precisa baixar/re-subir o arquivo.
        
        Args:
            file_path: Caminho do arquivo (ex: "/Documentos/PLANILHA.xlsx")
            sheet_name: Nome da aba
            rows: Lista de linhas, cada linha e uma lista de valores
            
        Returns:
            Tupla (sucesso, mensagem)
        """
        try:
            if not rows:
                return False, "Nenhuma linha para adicionar"
            
            if not self.drive_id:
                return False, "Drive ID nao configurado. Execute get_site_info() primeiro."
            
            logger.info(f"Adicionando {len(rows)} linhas em {file_path} / {sheet_name}")
            
            file_path_clean = file_path.strip("/")
            
            # 1. Obter a planilha usada para saber a ultima linha
            endpoint = f"/drives/{self.drive_id}/root:/{file_path_clean}:/workbook/worksheets/{sheet_name}/usedRange"
            success, response = self._make_request("GET", endpoint, timeout=60)
            
            if not success:
                logger.warning(f"Nao foi possivel obter range usado: {response}")
                start_row = 2
            else:
                row_count = response.get("rowCount", 1)
                start_row = row_count + 1
            
            logger.info(f"Inserindo a partir da linha {start_row}")
            
            # 2. Determinar o range para inserir
            num_cols = len(rows[0]) if rows else 1
            num_rows = len(rows)
            
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
            endpoint = f"/drives/{self.drive_id}/root:/{file_path_clean}:/workbook/worksheets/{sheet_name}/range(address='{range_address}')"
            
            data = {"values": rows}
            
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


# ============================================================================
# EXEMPLO DE USO
# ============================================================================

"""
from pathlib import Path
from src.services.sharepoint_service import SharePointService

# 1. Inicializar com credenciais do Azure
service = SharePointService(
    tenant_id="7df8c4cf-1a79-4386-b093-3138754b6a22",
    client_id="44b822d5-dff6-441d-9a8b-6dfd3f0a6544",
    client_secret="seu-secret",
    site_url="https://empresa.sharepoint.com/sites/seu-site"
)

# 2. Autenticar
if service.authenticate():
    print("✅ Autenticado!")
    
    # 3. Obter informações do site
    if service.get_site_info():
        print(f"Site ID: {service.site_id}")
        print(f"Drive ID: {service.drive_id}")
        
        # 4. Listar arquivos
        files = service.list_files("/")
        for file in files:
            print(f"  📄 {file.name} ({file.size} bytes)")
        
        # 5. Fazer download
        database_file = service.find_file_by_name("DATABASE.csv")
        if database_file:
            service.download_file(
                database_file.item_id,
                Path("local_database.csv")
            )
        
        # 6. Fazer upload
        service.upload_file(
            Path("novo_relatorio.xlsx"),
            folder_path="/Sincronizações"
        )
else:
    print("❌ Falha na autenticação")
"""
