"""
Modelos de dados para o processamento de recebimento.

Este módulo define as estruturas de dados para:
- Conta a Receber (entrada do Bestsoft)
- Tipos de documentos (NFS, NFE, DOC, DACTE)
- Seções da planilha de destino
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, List, Any
from decimal import Decimal
from enum import Enum


def _norm_documento_duplicado(val: Any) -> str:
    """Mesma lógica do número na coluna D da planilha (inteiro/excel → string estável)."""
    if val is None:
        return ""
    try:
        import pandas as pd
        if pd.isna(val):
            return ""
    except Exception:
        pass
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return ""
    try:
        return str(int(float(s.replace(",", "."))))
    except (ValueError, TypeError):
        pass
    if s.isdigit():
        try:
            return str(int(s))
        except ValueError:
            pass
    return s[:32]


def converter_valor_para_date_seguro(val: Any) -> Optional[date]:
    """
    Normaliza entrada do Excel/pandas para datetime.date ou None.
    Evita pandas NaT chegar até strftime (erro NaTType does not support strftime).
    """
    if val is None:
        return None
    try:
        import pandas as pd

        if pd.isna(val):
            return None
    except Exception:
        pass

    if isinstance(val, date) and not isinstance(val, datetime):
        return val

    if isinstance(val, datetime):
        try:
            import pandas as pd

            if pd.isna(val):
                return None
        except Exception:
            pass
        try:
            return val.date()
        except (AttributeError, ValueError, OSError):
            return None

    try:
        import pandas as pd

        t = pd.to_datetime(val, errors="coerce", dayfirst=True)
        if pd.isna(t):
            return None
        return t.date()
    except Exception:
        return None


def formatar_data_br_para_planilha(val: Any) -> str:
    d = converter_valor_para_date_seguro(val)
    if d is None:
        return ""
    try:
        return d.strftime("%d/%m/%Y")
    except (ValueError, OSError):
        return ""


def formatar_data_yyyymmdd_para_chave(val: Any) -> str:
    d = converter_valor_para_date_seguro(val)
    if d is None:
        return ""
    try:
        return d.strftime("%Y%m%d")
    except (ValueError, OSError):
        return ""


def chave_duplicado_normalizada(numero_raw: Any, data_raw: Any) -> str:
    """
    Chave única: número na coluna D (normalizado) + data de baixa YYYYMMDD.
    Mesma regra para ContaReceber, leitura da planilha e deduplicação do arquivo.
    """
    num = _norm_documento_duplicado(numero_raw)
    data_str = formatar_data_yyyymmdd_para_chave(data_raw)
    return f"{num}_{data_str}"


class TipoDocumento(Enum):
    """Tipos de documento baseado no número de dígitos"""
    NFS = "NFS"      # Nota Fiscal de Serviço - 3 dígitos
    NFE = "NFE"      # Nota Fiscal Eletrônica (Venda) - 4 dígitos
    DOC = "DOC"      # Documento/Recebimento a identificar - sem número
    DACTE = "DACTE"  # Conhecimento de Transporte - 6 dígitos
    
    @classmethod
    def identificar_por_digitos(cls, numero: str) -> "TipoDocumento":
        """
        Identifica o tipo de documento pelo número de dígitos.
        
        Regras:
        - 3 dígitos = NFS (Nota Fiscal de Serviço)
        - 4 dígitos = NFE (Nota Fiscal Eletrônica/Venda)
        - 6 dígitos = DACTE (CTE)
        - Sem número ou outros = DOC (a identificar)
        """
        if not numero:
            return cls.DOC
        
        numero_limpo = str(numero).strip()
        
        if not numero_limpo.isdigit():
            return cls.DOC
        
        num_digitos = len(numero_limpo)
        
        if num_digitos == 3:
            return cls.NFS
        elif num_digitos == 4:
            return cls.NFE
        elif num_digitos == 6:
            return cls.DACTE
        else:
            return cls.DOC


@dataclass
class ContaReceber:
    """
    Representa uma conta a receber do Bestsoft.
    Esta é a entrada principal do sistema.
    """
    # Identificação
    numero_documento: Optional[str] = None
    numero_fatura: Optional[str] = None
    
    # Datas
    data_baixa: Optional[date] = None  # Data do recebimento/pagamento
    data_emissao: Optional[date] = None
    data_vencimento: Optional[date] = None
    
    # Cliente
    cliente_nome: Optional[str] = None
    cliente_codigo: Optional[str] = None
    cliente_cnpj: Optional[str] = None
    
    # Valores
    valor_bruto: Optional[Decimal] = None
    valor_liquido: Optional[Decimal] = None
    juros: Optional[Decimal] = None
    desconto: Optional[Decimal] = None
    
    # Outros
    banco: Optional[str] = None
    centro_custo: Optional[str] = None
    observacao: Optional[str] = None
    
    # Calculados
    _tipo_documento: Optional[TipoDocumento] = field(default=None, repr=False)
    
    @property
    def tipo_documento(self) -> TipoDocumento:
        """Retorna o tipo de documento baseado no número de dígitos ou tipo forçado"""
        if self._tipo_documento is None:
            self._tipo_documento = TipoDocumento.identificar_por_digitos(self.numero_documento)
        return self._tipo_documento
    
    @tipo_documento.setter
    def tipo_documento(self, valor: TipoDocumento):
        """Permite definir o tipo de documento manualmente"""
        self._tipo_documento = valor
    
    @property
    def chave_duplicado(self) -> str:
        """Número na coluna D (normalizado) + data de baixa YYYYMMDD."""
        return chave_duplicado_normalizada(self.numero_documento, self.data_baixa)
    
    @property
    def eh_fatura_com_ctes(self) -> bool:
        """Verifica se é uma fatura que pode conter múltiplos CTEs"""
        if self._tipo_documento == TipoDocumento.DACTE and self.numero_fatura:
            return True
        return False


@dataclass
class LinhaPlantilhaNFS:
    """Linha para seção NOTA FISCAL (Serviço) - 3 dígitos"""
    data_recebimento: Optional[date] = None
    banco: Optional[str] = None
    nota_fiscal: Optional[str] = None  # 3 dígitos
    data_emissao: Optional[date] = None
    cliente: Optional[str] = None
    codigo_pagador: Optional[str] = None
    cnpj: Optional[str] = None
    centro_custo: Optional[str] = None
    valor_bruto: Optional[Decimal] = None
    iss_retido: str = "NÃO"
    liquido_receber: Optional[Decimal] = None
    juros: Optional[Decimal] = None
    descontos: Optional[Decimal] = None
    
    def to_row(self) -> list:
        """Converte para linha do Excel"""
        def fmt_valor(v):
            if v is None:
                return "R$ -"
            return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        return [
            "",  # Coluna A vazia
            formatar_data_br_para_planilha(self.data_recebimento),
            self.banco or "ITAU",
            self.nota_fiscal or "",
            formatar_data_br_para_planilha(self.data_emissao),
            self.cliente or "",
            self.codigo_pagador or "",
            self.cnpj or "",
            self.centro_custo or "",
            fmt_valor(self.valor_bruto),
            self.iss_retido,
            fmt_valor(self.liquido_receber or self.valor_bruto),
            fmt_valor(self.juros),
            fmt_valor(self.descontos),
        ]


@dataclass
class LinhaPlantilhaNFE:
    """Linha para seção NFE — mesmas colunas físicas da seção NFS (modelo único na planilha)."""
    data_recebimento: Optional[date] = None
    banco: Optional[str] = None
    nfe: Optional[str] = None  # 4 dígitos → coluna D (mesmo índice que NOTA FISCAL)
    data_emissao: Optional[date] = None
    cliente: Optional[str] = None
    codigo_pagador: Optional[str] = None
    cnpj: Optional[str] = None
    centro_custo: Optional[str] = None
    valor: Optional[Decimal] = None
    iss_retido: str = "NÃO"
    liquido_receber: Optional[Decimal] = None
    juros: Optional[Decimal] = None
    desconto: Optional[Decimal] = None

    def to_row(self) -> list:
        """Mesma ordem de colunas que LinhaPlantilhaNFS (A vazia + B..N)."""
        def fmt_valor(v):
            if v is None:
                return "R$ -"
            return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        return [
            "",
            formatar_data_br_para_planilha(self.data_recebimento),
            self.banco or "ITAU",
            self.nfe or "",
            formatar_data_br_para_planilha(self.data_emissao),
            self.cliente or "",
            self.codigo_pagador or "",
            self.cnpj or "",
            self.centro_custo or "",
            fmt_valor(self.valor),
            self.iss_retido,
            fmt_valor(self.liquido_receber or self.valor),
            fmt_valor(self.juros),
            fmt_valor(self.desconto),
        ]


@dataclass
class LinhaPlantilhaDOC:
    """Linha para seção DOC (Documentos/Recebimentos a identificar)"""
    data_recebimento: Optional[date] = None
    banco: Optional[str] = None
    doc: Optional[str] = None  # Geralmente mês/ano (ex: abr/26)
    descricao: Optional[str] = None
    cliente: Optional[str] = None
    centro_custo: Optional[str] = None
    valor: Optional[Decimal] = None
    ctes: Optional[str] = None  # CTE's vinculados quando identificar
    
    def to_row(self) -> list:
        """Converte para linha do Excel"""
        def fmt_valor(v):
            if v is None:
                return ""
            return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        return [
            "",  # Coluna A vazia
            formatar_data_br_para_planilha(self.data_recebimento),
            self.banco or "",
            self.doc or "",
            self.descricao or "Serviço a Identificar",
            self.cliente or "",
            self.centro_custo or "LLM",
            fmt_valor(self.valor),
            self.ctes or "",
        ]


@dataclass
class LinhaPlantilhaDACTE:
    """Linha para seção DACTE (Conhecimento de Transporte) - 6 dígitos"""
    data_recebimento: Optional[date] = None
    banco: Optional[str] = None
    dacte: Optional[str] = None  # 6 dígitos
    status: str = "Único"
    cliente: Optional[str] = None
    codigo_pagador: Optional[str] = None
    cnpj: Optional[str] = None
    centro_custo: Optional[str] = None
    valor_bruto: Optional[Decimal] = None
    numero_fatura: Optional[str] = None
    
    def to_row(self) -> list:
        """Converte para linha do Excel"""
        def fmt_valor(v):
            if v is None:
                return "R$ -"
            return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        return [
            "",  # Coluna A vazia
            formatar_data_br_para_planilha(self.data_recebimento),
            self.banco or "ITAU S/A",
            self.dacte or "",
            self.status,
            self.cliente or "",
            self.codigo_pagador or "",
            self.cnpj or "",
            self.centro_custo or "",
            fmt_valor(self.valor_bruto),
            self.numero_fatura or "",
        ]


@dataclass
class SecaoPlanilha:
    """Informações sobre uma seção da planilha"""
    tipo: TipoDocumento
    linha_cabecalho: int
    coluna_identificadora: str  # Texto que identifica a seção no cabeçalho (coluna D)
    proxima_linha_vazia: int = 0
    """Número da linha Excel (1-based) onde gravar os dados."""
    inserir_linhas_antes: bool = False
    """Se True, insere linhas em branco antes dessa linha para não sobrescrever o rodapé/total."""

    @classmethod
    def get_identificador_coluna(cls, tipo: TipoDocumento) -> str:
        """Retorna o texto que identifica a seção na coluna D do cabeçalho"""
        mapping = {
            TipoDocumento.NFS: "NOTA FISCAL",
            TipoDocumento.NFE: "NFE",
            TipoDocumento.DOC: "DOC",
            TipoDocumento.DACTE: "DACTE",
        }
        return mapping.get(tipo, "")


@dataclass
class ResultadoProcessamento:
    """Resultado do processamento de uma conta a receber"""
    conta: ContaReceber
    tipo_documento: TipoDocumento
    linha_destino: Optional[int] = None
    sucesso: bool = False
    mensagem: str = ""
    duplicado: bool = False


@dataclass
class RelatorioProcessamento:
    """Relatório completo do processamento"""
    data_filtro: date
    data_processamento: datetime
    aba_destino: str = ""  # Ex: "ABR 26"
    total_processados: int = 0
    total_nfs: int = 0
    total_nfe: int = 0
    total_doc: int = 0
    total_dacte: int = 0
    total_duplicados: int = 0
    total_erros: int = 0
    resultados: List[ResultadoProcessamento] = field(default_factory=list)
    erros: List[str] = field(default_factory=list)
    
    def get_resumo(self) -> str:
        """Retorna resumo do processamento"""
        return (
            f"Processamento de {formatar_data_br_para_planilha(self.data_filtro)}\n"
            f"Aba destino: {self.aba_destino}\n"
            f"- Total processados: {self.total_processados}\n"
            f"  - NFS (3 dígitos): {self.total_nfs}\n"
            f"  - NFE (4 dígitos): {self.total_nfe}\n"
            f"  - DOC (a identificar): {self.total_doc}\n"
            f"  - DACTE (6 dígitos): {self.total_dacte}\n"
            f"- Duplicados ignorados: {self.total_duplicados}\n"
            f"- Erros: {self.total_erros}"
        )
    
    def adicionar_resultado(self, resultado: ResultadoProcessamento):
        """Adiciona um resultado e atualiza contadores"""
        self.resultados.append(resultado)
        self.total_processados += 1
        
        if resultado.duplicado:
            self.total_duplicados += 1
        elif resultado.sucesso:
            tipo = resultado.tipo_documento
            if tipo == TipoDocumento.NFS:
                self.total_nfs += 1
            elif tipo == TipoDocumento.NFE:
                self.total_nfe += 1
            elif tipo == TipoDocumento.DOC:
                self.total_doc += 1
            elif tipo == TipoDocumento.DACTE:
                self.total_dacte += 1
        else:
            self.total_erros += 1

    def recompute_counts(self) -> None:
        """Recalcula totais a partir de `resultados` (ex.: após marcar duplicados da planilha)."""
        self.total_processados = len(self.resultados)
        self.total_duplicados = 0
        self.total_nfs = 0
        self.total_nfe = 0
        self.total_doc = 0
        self.total_dacte = 0
        self.total_erros = 0
        for resultado in self.resultados:
            if resultado.duplicado:
                self.total_duplicados += 1
            elif resultado.sucesso:
                t = resultado.tipo_documento
                if t == TipoDocumento.NFS:
                    self.total_nfs += 1
                elif t == TipoDocumento.NFE:
                    self.total_nfe += 1
                elif t == TipoDocumento.DOC:
                    self.total_doc += 1
                elif t == TipoDocumento.DACTE:
                    self.total_dacte += 1
            else:
                self.total_erros += 1


def pd_isna(value) -> bool:
    """Verifica se valor é NaN/None de forma segura"""
    if value is None:
        return True
    try:
        import pandas as pd
        return pd.isna(value)
    except:
        return False


# Manter compatibilidade com código antigo (deprecated)
@dataclass
class NotaFiscalServico:
    """DEPRECATED: Use ContaReceber + TipoDocumento.NFS"""
    codigo: Optional[int] = None
    numero: Optional[int] = None
    serie: Optional[int] = None
    data_emissao: Optional[date] = None
    cliente_codigo: Optional[int] = None
    cliente_nome: Optional[str] = None
    cliente_cnpj: Optional[str] = None
    valor_total: Optional[Decimal] = None
    condicao_pagamento: Optional[str] = None
    observacoes: Optional[str] = None
    situacao: Optional[str] = None
    situacao_nfse: Optional[str] = None
    documento_a_receber: Optional[float] = None


@dataclass
class ConhecimentoTransporte:
    """DEPRECATED: Use ContaReceber + TipoDocumento.DACTE"""
    numero: Optional[int] = None
    serie: Optional[int] = None
    data_emissao: Optional[date] = None
    data_entrega: Optional[date] = None
    data_chegada: Optional[date] = None
    notas_fiscais: Optional[str] = None
    valor_frete: Optional[Decimal] = None
    total: Optional[Decimal] = None
    remetente_nome: Optional[str] = None
    remetente_cnpj: Optional[str] = None
    destinatario_nome: Optional[str] = None
    destinatario_cnpj: Optional[str] = None
    entrega_status: Optional[str] = None


@dataclass
class NotaFiscal:
    """DEPRECATED: Use ContaReceber + TipoDocumento.NFE"""
    numero: Optional[int] = None
    serie: Optional[int] = None
    data_emissao: Optional[date] = None
    numero_nota_fiscal: Optional[int] = None
    serie_nota_fiscal: Optional[int] = None
    data_emissao_nota_fiscal: Optional[date] = None
    valor_nota_fiscal: Optional[Decimal] = None
    peso: Optional[float] = None
    volumes: Optional[int] = None
    remetente_nome: Optional[str] = None
    remetente_cnpj: Optional[str] = None
    destinatario_nome: Optional[str] = None
    destinatario_cnpj: Optional[str] = None


@dataclass
class ResultadoCruzamento:
    """DEPRECATED: Use ResultadoProcessamento"""
    nfse: NotaFiscalServico = field(default_factory=NotaFiscalServico)
    ctes_encontrados: List[ConhecimentoTransporte] = field(default_factory=list)
    nfs_encontradas: List[NotaFiscal] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {}
