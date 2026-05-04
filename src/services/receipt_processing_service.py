"""
Serviço de Processamento de Recebimento.

Este serviço é responsável por:
1. Carregar lista de Contas a Receber do Bestsoft (arquivo único)
2. Identificar tipo de documento pelo número de dígitos (3/4/6)
3. Verificar duplicados na planilha de destino
4. Inserir na seção correta da planilha (NFS, NFE, DOC, DACTE)
"""

import logging
import re
import unicodedata
from pathlib import Path
from datetime import datetime, date, timedelta
from dataclasses import replace
from typing import Optional, List, Dict, Tuple, Set, Any, Union
from decimal import Decimal

import pandas as pd

from src.models.receipt_models import (
    TipoDocumento,
    ContaReceber,
    LinhaPlantilhaNFS,
    LinhaPlantilhaNFE,
    LinhaPlantilhaDOC,
    LinhaPlantilhaDACTE,
    SecaoPlanilha,
    ResultadoProcessamento,
    RelatorioProcessamento,
    pd_isna,
    converter_valor_para_date_seguro,
    formatar_data_yyyymmdd_para_chave,
    chave_duplicado_normalizada,
    _norm_documento_duplicado,
)

logger = logging.getLogger(__name__)


class ReceiptProcessingService:
    """Serviço para processamento de contas a receber"""

    @staticmethod
    def _sigla_centro_custo(val: Any) -> str:
        """
        Extrai só a sigla quando o Bestsoft envia 'IHP - INSTITUTO...', 'DLE - ...', 'OUT - OUTROS'.
        """
        if val is None or pd_isna(val):
            return ""
        s = str(val).strip()
        if not s or s.lower() == "nan":
            return ""
        m = re.match(r"^([A-Za-z0-9]{2,12})\s*-\s+", s)
        if m:
            return m.group(1).upper()
        if len(s) <= 12 and re.match(r"^[A-Za-z0-9]+$", s):
            return s.upper()
        return s

    @staticmethod
    def _formatar_cnpj_cpf_cliente(val: Any) -> str:
        """Dígitos (11/14) confiáveis -> máscara BR; rejeita notação científica e floats duvidosos."""
        d = ReceiptProcessingService._digitos_fiscais_confiaveis(val)
        if not d:
            return ""
        if len(d) == 14:
            return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
        if len(d) == 11:
            return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
        return ""

    @staticmethod
    def _validar_cnpj_base(d14: str) -> bool:
        """Rejeita sequências iguais e DV inválido (evita 1,31E+13 convertido em lixo)."""
        if len(d14) != 14 or not d14.isdigit():
            return False
        if d14 == d14[0] * 14:
            return False
        mult1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        mult2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        nums = [int(x) for x in d14]
        s = sum(nums[i] * mult1[i] for i in range(12))
        r = s % 11
        d1 = 0 if r < 2 else 11 - r
        if d1 != nums[12]:
            return False
        s2 = sum(nums[i] * mult2[i] for i in range(13))
        r2 = s2 % 11
        d2 = 0 if r2 < 2 else 11 - r2
        return d2 == nums[13]

    @staticmethod
    def _digitos_fiscais_confiaveis(val: Any) -> Optional[str]:
        """
        Extrai 14 (CNPJ) ou 11 (CPF) dígitos somente se o valor for confiável.
        Excel em número/notação científica costuma corromper CNPJ — retorna None.
        """
        if val is None or pd_isna(val):
            return None
        if isinstance(val, str):
            s = val.strip()
            if not s or s.lower() == "nan":
                return None
            if re.search(r"e[+-]", s, re.I):
                logger.warning("CNPJ/CPF ignorado (notacao cientifica no texto). Prefira coluna como texto no Excel ou use a lista de CT-e/NF.")
                return None
            dig = re.sub(r"\D", "", s)
            if len(dig) == 14 and ReceiptProcessingService._validar_cnpj_base(dig):
                return dig
            if len(dig) == 11:
                return dig
            if len(dig) == 14:
                return None
            return None
        if isinstance(val, bool):
            return None
        if isinstance(val, int):
            if val < 0:
                return None
            dig = str(val)
            if len(dig) == 14 and ReceiptProcessingService._validar_cnpj_base(dig):
                return dig
            if len(dig) == 11:
                return dig
            return None
        if isinstance(val, float):
            if not val == val:
                return None
            s = repr(val).lower()
            if "e" in s:
                logger.warning("CNPJ/CPF ignorado (float em notacao cientifica vindos do Excel). Use lista de CT-e/NF ou exporte como texto.")
                return None
            if abs(round(val) - val) > 1e-6:
                return None
            iv = abs(int(round(val)))
            dig = str(iv)
            if len(dig) < 14:
                dig = dig.zfill(14)
            elif len(dig) > 14:
                return None
            if len(dig) == 14 and ReceiptProcessingService._validar_cnpj_base(dig):
                return dig
            if len(str(iv)) == 11:
                return str(iv)
            logger.warning(
                "CNPJ com 14 digitos sem DV valido (possivel numero grande no Excel). Busca na lista de conhecimento/NF."
            )
            return None
        return None

    # Mapeamento de colunas do arquivo Bestsoft (Contas a Receber)
    COLUNAS_BESTSOFT = {
        'numero_documento': ['Nº Documento', 'N° Documento', 'Documento', 'Núm. Documento', 'Número', 'Nr. Documento', 'Doc'],
        'numero_fatura': ['Fatura', 'Nr. Fatura', 'Núm. Fatura', 'Nº Fatura'],
        'data_baixa': ['Dt. Pagamento', 'Data Pagamento', 'Data Baixa', 'Dt. Baixa', 'Data Recebimento', 'Baixa'],
        'data_emissao': ['Dt. Emissão', 'Data Emissão', 'Emissão'],
        'data_vencimento': ['Dt. Vencimento', 'Data Vencimento', 'Vencimento'],
        'cliente_nome': ['Cliente', 'Nome Cliente', 'Razão Social', 'Cliente - Nome'],
        'cliente_codigo': ['Cód. Centro de Receita', 'Código Cliente', 'Cód. Cliente', 'Cliente - Código'],
        'cliente_cnpj': [
            'CNPJ', 'CNPJ/CPF', 'CPF/CNPJ', 'Cliente - CNPJ', 'Cliente - CNPJ/CPF',
            'CNPJ Cliente', 'CNPJ do Cliente', 'Cliente CNPJ', 'Cliente CNPJ/CPF',
            'Pagador - CNPJ/CPF', 'Pagador - CNPJ', 'Tomador - CNPJ',
            'Sacado - CNPJ', 'Sacado - CNPJ/CPF',
            'Inscrição Federal', 'Inscricao Federal', 'Inscrição', 'Doc. Fiscal Cliente',
        ],
        'valor_bruto': ['Valor Total', 'Valor', 'Valor Bruto', 'Vlr. Bruto'],
        'valor_liquido': ['Valor Pago', 'Valor Líquido', 'Vlr. Líquido', 'Líquido'],
        'juros': ['Juros'],
        'desconto': ['Desconto', 'Descontos'],
        'banco': ['Conta', 'Banco', 'Conta Bancária', 'Local de Cobrança - Descrição'],
        'centro_custo': [
            'Centro de Receita', 'Centro Custo', 'CC', 'Centro de Custo',
            'Descr. Centro de Custo', 'Centro de Custo - Descrição', 'Descrição CC',
        ],
        'observacao': ['Observação', 'Obs', 'Observações', 'Observação da Parcela'],
        'origem': ['Origem do Lançamento'],
        'classificacao': ['Classificação Financeira'],
    }
    
    # Mapeamento de colunas do arquivo de Exportação CTe
    COLUNAS_CTE = {
        'numero_cte': ['Nº CT-e', 'N° CT-e', 'Número CT-e', 'Nr. CT-e'],
        'data_emissao': ['Data', 'Data Emissão'],
        'valor': ['Total', 'Valor'],
        'numero_fatura': ['Nº Fatura', 'N° Fatura', 'Fatura'],
        'situacao_fatura': ['Situação da Fatura', 'Situação'],
        'pagador_codigo': ['Pagador - Código', 'Cód. Pagador'],
        'pagador_nome': ['Pagador - Nome', 'Pagador'],
        'destinatario_codigo': ['Destinatário - Código'],
        'destinatario_nome': ['Destinatário - Nome', 'Destinatário'],
        'destinatario_cnpj': [
            'Destinatário - CNPJ/CPF', 'CNPJ Destinatário', 'CNPJ Dest.', 'Dest. CNPJ',
            'CNPJ/CPF Destinatário', 'CPF/CNPJ Destinatário',
        ],
    }

    # Lista de Notas Fiscais (Fiscal > Listas — layout variável; nomes comuns)
    COLUNAS_LISTA_NF = {
        'numero_documento': [
            'Número', 'Nº Nota', 'N° Nota', 'Nota Fiscal', 'NF', 'NFS', 'NFE', 'NF-e',
            'Nr. Nota', 'Núm. Nota', 'Nº NF', 'Doc', 'Documento',
        ],
        'data_emissao': ['Data Emissão', 'Dt. Emissão', 'Emissão', 'Data', 'Dt Emissão'],
        'cliente_nome': ['Cliente', 'Tomador', 'Destinatário', 'Razão Social', 'Nome'],
        'cliente_cnpj': [
            'CNPJ', 'CNPJ/CPF', 'Tomador - CNPJ', 'Tomador - CNPJ/CPF', 'CNPJ Tomador',
            'Destinatário - CNPJ', 'Destinatário - CNPJ/CPF', 'Cliente - CNPJ',
            'CNPJ Cliente', 'CNPJ Emitente', 'Emitente - CNPJ', 'Empresa - CNPJ',
            'Inscrição Federal', 'Sacado - CNPJ', 'Pagador - CNPJ',
        ],
        'valor_total': ['Valor Total', 'Valor', 'Vlr. Total'],
    }

    def __init__(self):
        self.df_contas: Optional[pd.DataFrame] = None
        self.df_ctes: Optional[pd.DataFrame] = None
        self.df_nf_lista: Optional[pd.DataFrame] = None
        self.contas: List[ContaReceber] = []
        self._ctes_por_fatura: Dict[str, List[dict]] = {}
        self._nf_por_documento: Dict[str, dict] = {}
        self._duplicados_existentes: Set[str] = set()
        self._cnpj_por_numero_cte: Dict[str, str] = {}

    def limpar_bases_auxiliares(self) -> None:
        """Antes de um novo processamento: zera listas de conhecimento e NF mescladas."""
        self.df_ctes = None
        self.df_nf_lista = None
        self._ctes_por_fatura = {}
        self._nf_por_documento = {}
        self._cnpj_por_numero_cte = {}

    @staticmethod
    def _normalizar_id_fatura(val: Any) -> Optional[str]:
        if val is None or pd_isna(val):
            return None
        s = str(val).strip()
        if not s:
            return None
        try:
            if isinstance(val, float) or (s.replace('.', '', 1).replace('-', '').isdigit() and '.' in s):
                return str(int(float(s.replace(",", "."))))
        except (ValueError, TypeError):
            pass
        if s.isdigit():
            return str(int(s))
        return s

    def _ler_dataframe(self, caminho: str) -> pd.DataFrame:
        if caminho.lower().endswith(".csv"):
            return pd.read_csv(caminho, sep=";", encoding="utf-8")
        return pd.read_excel(caminho)

    def _primeira_coluna_existente(
        self,
        df: pd.DataFrame,
        candidatos: Union[str, List[str]],
    ) -> Optional[str]:
        names: List[str] = []
        if isinstance(candidatos, str):
            names = [candidatos]
        else:
            names = list(candidatos or [])
        for col in names:
            if col in df.columns:
                return col
            low = col.lower()
            for c in df.columns:
                if str(c).strip().lower() == low:
                    return c
        return None

    def _primeira_coluna_cnpj_cpf_por_nome(self, df: pd.DataFrame, contexto: str) -> Optional[str]:
        """Cabeçalho com 'cnpj' ou 'cpf/cnpj' / 'cnpj/cpf' (exportações Bestsoft variam)."""
        for c in df.columns:
            low = str(c).strip().lower()
            if "cnpj" in low or "cpf/cnpj" in low or "cnpj/cpf" in low:
                logger.info("%s: coluna CNPJ/CPF detectada pelo nome: %r", contexto, c)
                return c
        return None

    def _filtrar_df_ultimos_meses(self, df: pd.DataFrame, colunas_data: List[str], meses: int = 12) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        col_data = None
        for grp in colunas_data:
            c = self._primeira_coluna_existente(df, grp if isinstance(grp, list) else [grp])
            if c:
                col_data = c
                break
        if not col_data:
            logger.warning("Lista sem coluna de data reconhecida; nao aplicado filtro de %s meses", meses)
            return df
        hoje = date.today()
        inicio = hoje - timedelta(days=30 * meses)
        ser = pd.to_datetime(df[col_data], errors="coerce", dayfirst=True)
        datas = ser.dt.date
        mask = datas.notna() & (datas >= inicio) & (datas <= hoje)
        antes = len(df)
        df2 = df.loc[mask].copy()
        logger.info("Filtro %s meses na coluna %r: %s -> %s linhas", meses, col_data, antes, len(df2))
        return df2

    def carregar_listas_conhecimento(
        self,
        caminhos: List[str],
        filtrar_ultimos_meses: bool = True,
    ) -> Tuple[bool, str]:
        """Varias exportacoes (Fiscal>Listas Lista de Conhecimento + DCT, etc.), mescladas e indexadas."""
        paths = [p for p in (caminhos or []) if p and Path(p).exists()]
        if not paths:
            return True, "Nenhuma lista de conhecimento informada"

        todas: List[pd.DataFrame] = []
        errs: List[str] = []
        for p in paths:
            try:
                df = self._ler_dataframe(p)
                if filtrar_ultimos_meses:
                    cands = list(self.COLUNAS_CTE.get("data_emissao") or []) + ["Data", "Dt. Emissão", "Dt Emissão"]
                    df = self._filtrar_df_ultimos_meses(df, cands, 12)
                todas.append(df)
            except Exception as e:
                errs.append(f"{Path(p).name}: {e}")
        if not todas:
            msg = "; ".join(errs) if errs else "Falha ao ler listas de conhecimento"
            logger.error(msg)
            return False, msg

        self.df_ctes = pd.concat(todas, ignore_index=True)
        self._indexar_ctes_por_fatura()
        nf = len(self._ctes_por_fatura)
        extras = f" ({len(errs)} arquivo(s) com erro)" if errs else ""
        return True, (
            f"{len(self.df_ctes)} CT-e na base mesclada, {nf} faturas indexadas.{extras}"
        )

    def carregar_listas_notas_fiscais(
        self,
        caminhos: List[str],
        filtrar_ultimos_meses: bool = True,
    ) -> Tuple[bool, str]:
        """Listas exportadas Fiscal>Listas para cruzamento com contas NFS/NFE."""
        paths = [p for p in (caminhos or []) if p and Path(p).exists()]
        if not paths:
            return True, "Nenhuma lista de notas fiscal informada"

        todas = []
        errs = []
        for p in paths:
            try:
                df = self._ler_dataframe(p)
                if filtrar_ultimos_meses:
                    dc = []
                    for k in ("data_emissao",):
                        dc.extend(list(self.COLUNAS_LISTA_NF.get(k) or []))
                    df = self._filtrar_df_ultimos_meses(df, dc + ["Data", "Emissão"], 12)
                todas.append(df)
            except Exception as e:
                errs.append(f"{Path(p).name}: {e}")

        if not todas:
            msg = "; ".join(errs) if errs else "Falha ao ler listas de NF"
            return False, msg

        self.df_nf_lista = pd.concat(todas, ignore_index=True)
        self._indexar_nf_por_documento()
        extra = f" ({len(errs)} arquivo(s) com erro)" if errs else ""
        return True, (
            f"{len(self.df_nf_lista)} notas na lista mesclada, "
            f"{len(self._nf_por_documento)} numeros indexados.{extra}"
        )

    def _indexar_nf_por_documento(self) -> None:
        self._nf_por_documento = {}
        if self.df_nf_lista is None or self.df_nf_lista.empty:
            return

        df = self.df_nf_lista
        col_doc = None
        for nome in ["numero_documento"]:
            for alias in self.COLUNAS_LISTA_NF[nome]:
                col_doc = self._primeira_coluna_existente(df, alias)
                if col_doc:
                    break
            if col_doc:
                break
        if not col_doc:
            logger.warning("Lista de NF: coluna de numero da nota nao encontrada")
            return

        col_em = self._primeira_coluna_existente(df, list(self.COLUNAS_LISTA_NF["data_emissao"]))
        col_cli = self._primeira_coluna_existente(df, list(self.COLUNAS_LISTA_NF["cliente_nome"]))
        col_cnpj = self._primeira_coluna_existente(df, list(self.COLUNAS_LISTA_NF["cliente_cnpj"]))
        if not col_cnpj:
            col_cnpj = self._primeira_coluna_cnpj_cpf_por_nome(df, "Lista NF")
        col_vlr = self._primeira_coluna_existente(df, list(self.COLUNAS_LISTA_NF["valor_total"]))

        def norm_nf(v):
            if v is None or pd_isna(v):
                return None
            s = str(v).strip()
            try:
                if isinstance(v, (int, float)) or (s.replace(".", "").isdigit()):
                    return str(int(float(str(v).replace(",", "."))))
            except (ValueError, TypeError):
                pass
            if s.isdigit():
                return str(int(s))
            return s[:32] if s else None

        for _, row in df.iterrows():
            k = norm_nf(row.get(col_doc))
            if not k:
                continue
            d_em = row.get(col_em) if col_em else None
            dem_date = None
            if d_em is not None and not pd_isna(d_em):
                try:
                    dem_date = pd.to_datetime(d_em, dayfirst=True).date()
                except Exception:
                    dem_date = None
            payload = {
                "cliente": str(row[col_cli]).strip() if col_cli and row.get(col_cli) is not None and not pd_isna(row.get(col_cli)) else None,
                "data_emissao": dem_date,
                "cnpj": (
                    self._formatar_cnpj_cpf_cliente(row[col_cnpj])
                    if col_cnpj and row.get(col_cnpj) is not None and not pd_isna(row.get(col_cnpj))
                    else None
                ),
            }
            if col_vlr and row.get(col_vlr) is not None and not pd_isna(row.get(col_vlr)):
                try:
                    payload["valor"] = Decimal(str(row[col_vlr]).replace("R$", "").strip().replace(".", "").replace(",", "."))
                except Exception:
                    pass
            self._nf_por_documento[k] = payload

        logger.info("Indexadas %s notas fiscais (por numero)", len(self._nf_por_documento))
    
    def carregar_contas_receber(self, caminho: str) -> Tuple[bool, str]:
        """
        Carrega o arquivo de Contas a Receber do Bestsoft.
        
        Args:
            caminho: Caminho para o arquivo Excel
            
        Returns:
            Tupla (sucesso, mensagem)
        """
        try:
            logger.info(f"Carregando contas a receber: {caminho}")
            
            if caminho.endswith('.csv'):
                self.df_contas = pd.read_csv(caminho, sep=';', encoding='utf-8')
            else:
                self.df_contas = pd.read_excel(caminho)
            
            logger.info(f"  -> {len(self.df_contas)} registros carregados")
            logger.info(f"  -> Colunas: {list(self.df_contas.columns)}")
            
            self._converter_para_contas()
            
            return True, f"{len(self.contas)} contas carregadas"
            
        except FileNotFoundError as e:
            msg = f"Arquivo não encontrado: {caminho}"
            logger.error(msg)
            return False, msg
        except Exception as e:
            msg = f"Erro ao carregar arquivo: {str(e)}"
            logger.error(msg)
            return False, msg
    
    def carregar_base_ctes(self, caminho: str) -> Tuple[bool, str]:
        """
        Carrega a base de CTEs exportada do Bestsoft (Utilitários > Exportação > DCT).
        Usado para expandir faturas que contêm múltiplos CTEs.
        """
        return self.carregar_listas_conhecimento([caminho], filtrar_ultimos_meses=True)

    def _indexar_ctes_por_fatura(self):
        """Indexa os CTEs pelo número da fatura para busca rápida (lista mesclada)."""
        self._ctes_por_fatura = {}
        self._cnpj_por_numero_cte = {}

        if self.df_ctes is None or self.df_ctes.empty:
            return

        col_fatura = self._encontrar_coluna_cte('numero_fatura')
        col_cte = self._encontrar_coluna_cte('numero_cte')
        col_valor = self._encontrar_coluna_cte('valor')
        col_data = self._encontrar_coluna_cte('data_emissao')
        col_pagador_cod = self._encontrar_coluna_cte('pagador_codigo')
        col_pagador_nome = self._encontrar_coluna_cte('pagador_nome')
        col_dest_nome = self._encontrar_coluna_cte('destinatario_nome')
        col_dest_cnpj = self._encontrar_coluna_cte('destinatario_cnpj')
        col_situacao = self._encontrar_coluna_cte('situacao_fatura')

        def norm_cte_num(v):
            if v is None or pd_isna(v):
                return None
            try:
                return str(int(float(v)))
            except (ValueError, TypeError):
                s = str(v).strip()
                return s or None

        for idx, row in self.df_ctes.iterrows():
            fatura_raw = row.get(col_fatura) if col_fatura else None
            fatura_str = self._normalizar_id_fatura(fatura_raw)
            if not fatura_str:
                continue

            ncte_raw = row.get(col_cte) if col_cte else None
            numero_cte = norm_cte_num(ncte_raw)
            dest_cnpj_fmt = ""
            if col_dest_cnpj:
                dest_cnpj_fmt = self._formatar_cnpj_cpf_cliente(row.get(col_dest_cnpj))
            cte_info = {
                'numero_cte': numero_cte,
                'valor': row.get(col_valor) if col_valor else None,
                'data_emissao': row.get(col_data) if col_data else None,
                'pagador_codigo': str(int(row.get(col_pagador_cod))) if col_pagador_cod and not pd_isna(row.get(col_pagador_cod)) else None,
                'pagador_nome': row.get(col_pagador_nome) if col_pagador_nome else None,
                'destinatario_nome': row.get(col_dest_nome) if col_dest_nome else None,
                'destinatario_cnpj': dest_cnpj_fmt or None,
                'situacao': row.get(col_situacao) if col_situacao else None,
            }

            if numero_cte and dest_cnpj_fmt:
                self._cnpj_por_numero_cte[str(numero_cte)] = dest_cnpj_fmt
            if fatura_str not in self._ctes_por_fatura:
                self._ctes_por_fatura[fatura_str] = []
            existentes = {x.get('numero_cte') for x in self._ctes_por_fatura[fatura_str]}
            if numero_cte and numero_cte in existentes:
                continue
            self._ctes_por_fatura[fatura_str].append(cte_info)

        logger.info(f"Indexados CT-e de {len(self._ctes_por_fatura)} faturas (apos deduplicar)")
    
    def _encontrar_coluna_cte(self, campo: str) -> Optional[str]:
        """Encontra a coluna correspondente no DataFrame de CTEs"""
        if self.df_ctes is None:
            return None
        
        possiveis = self.COLUNAS_CTE.get(campo, [campo])
        
        for col in possiveis:
            if col in self.df_ctes.columns:
                return col
            col_lower = col.lower()
            for df_col in self.df_ctes.columns:
                if df_col.lower() == col_lower:
                    return df_col
        
        return None
    
    def buscar_ctes_por_fatura(self, numero_fatura: str) -> List[dict]:
        """
        Busca todos os CTEs vinculados a uma fatura.
        
        Args:
            numero_fatura: Número da fatura
            
        Returns:
            Lista de dicionários com informações dos CTEs
        """
        if not numero_fatura:
            return []

        fatura_str = self._normalizar_id_fatura(numero_fatura)
        if not fatura_str:
            return []
        return self._ctes_por_fatura.get(fatura_str, [])

    @staticmethod
    def _normalizar_num_nota_curta(val: Any) -> Optional[str]:
        """Chave para lista de NF / contas (documento 3 ou 4 dígitos, etc.)."""
        if val is None or pd_isna(val):
            return None
        s = str(val).strip()
        if not s:
            return None
        try:
            return str(int(float(s.replace(",", "."))))
        except (ValueError, TypeError):
            pass
        if s.isdigit():
            return str(int(s))
        return s[:32]

    @staticmethod
    def _valor_decimal_br(raw: Any) -> Optional[Decimal]:
        if raw is None or pd_isna(raw):
            return None
        if isinstance(raw, (Decimal, int)):
            return Decimal(raw) if not isinstance(raw, Decimal) else raw
        try:
            if isinstance(raw, float):
                return Decimal(str(raw))
            s = str(raw).replace("R$", "").strip()
            if "," in s and "." in s:
                s = s.replace(".", "").replace(",", ".")
            elif "," in s:
                s = s.replace(",", ".")
            return Decimal(s)
        except Exception:
            return None

    @staticmethod
    def _celula_para_data(raw: Any) -> Optional[date]:
        if raw is None or pd_isna(raw):
            return None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            try:
                n = float(raw)
                if 20000 <= n <= 120000:
                    dd = pd.to_datetime(n, unit="D", origin="1899-12-30")
                    if pd.isna(dd):
                        return None
                    return dd.date()
            except Exception:
                pass
        return converter_valor_para_date_seguro(raw)

    def _cliente_sem_cnpj_valido_conta(self, conta: ContaReceber) -> bool:
        """True se vier vazio ou sem 14 dígitos (lista de NF pode completar)."""
        s = (conta.cliente_cnpj or "").strip()
        if not s:
            return True
        d = re.sub(r"\D", "", s)
        return len(d) < 14

    def _nf_info_para_conta(self, conta: ContaReceber) -> Optional[dict]:
        """Procura linha na lista de notas pelo nº da NF ou (fallback) pela fatura com mesmo formato curto."""
        if not self._nf_por_documento:
            return None
        chaves_ord = []
        for campo in (conta.numero_documento, conta.numero_fatura):
            k = self._normalizar_num_nota_curta(campo)
            if k and k not in chaves_ord:
                chaves_ord.append(k)
        for k in chaves_ord:
            info = self._nf_por_documento.get(k)
            if info:
                return info
        return None

    def _enriquecer_conta_com_lista_nf(self, conta: ContaReceber) -> ContaReceber:
        """
        Usa Lista de NF: CNPJ do tomador/destinatário da nota sempre que encontrar pelo número.

        NFS/NFE: atualiza cliente, emissão e CNPJ a partir da lista (costuma refletir a NF oficial).
        Outros tipos: só complementa CNPJ quando falta nos dados da conta ou não há 14 dígitos.
        """
        info = self._nf_info_para_conta(conta)
        if not info:
            return conta

        tipo = conta.tipo_documento
        cli = conta.cliente_nome
        dem = conta.data_emissao

        if tipo in (TipoDocumento.NFS, TipoDocumento.NFE):
            cli = info.get("cliente") or cli
            dem = info.get("data_emissao") or dem

        cnpj_raw = info.get("cnpj")
        cnpj_fmt = self._formatar_cnpj_cpf_cliente(cnpj_raw) if cnpj_raw else ""

        if not cnpj_fmt:
            if tipo in (TipoDocumento.NFS, TipoDocumento.NFE):
                return replace(conta, cliente_nome=cli, data_emissao=dem)
            return conta

        if tipo in (TipoDocumento.NFS, TipoDocumento.NFE):
            cnpj_final = cnpj_fmt
        elif self._cliente_sem_cnpj_valido_conta(conta):
            cnpj_final = cnpj_fmt
        else:
            cnpj_final = conta.cliente_cnpj

        return replace(
            conta,
            cliente_nome=cli,
            data_emissao=dem,
            cliente_cnpj=cnpj_final or conta.cliente_cnpj,
        )

    def _enriquecer_cnpj_dacte_pela_lista_conhecimento(self, conta: ContaReceber) -> ContaReceber:
        """Preenche CNPJ do destinatário a partir da lista de conhecimento (mesmo nº CT-e). Corrige lixo vindos do Excel."""
        if conta.tipo_documento != TipoDocumento.DACTE:
            return conta
        if not self._cnpj_por_numero_cte:
            return conta
        if not self._cliente_sem_cnpj_valido_conta(conta):
            return conta
        nd = (conta.numero_documento or "").strip()
        if not nd:
            return conta
        k = self._normalizar_num_nota_curta(nd) or nd
        cnpj = self._cnpj_por_numero_cte.get(str(k))
        if not cnpj:
            return conta
        return replace(conta, cliente_cnpj=cnpj)

    def expandir_conta_para_ctes_na_lista(self, conta: ContaReceber) -> List[ContaReceber]:
        """
        Lista(s) de conhecimento mescladas: gera uma ContaReceber por CT-e
        quando a fatura aparece várias vezes na base com CT-es diferentes.
        """
        if not self._ctes_por_fatura:
            return [conta]
        if conta.tipo_documento != TipoDocumento.DACTE:
            return [conta]
        nf = self._normalizar_id_fatura(conta.numero_fatura)
        if not nf:
            return [conta]
        ctes = self._ctes_por_fatura.get(nf, [])
        if not ctes:
            return [conta]

        valores_cte = [self._valor_decimal_br(c.get("valor")) for c in ctes]
        if all(v is not None for v in valores_cte) and valores_cte:
            valores_finais = valores_cte
        elif conta.valor_bruto is not None and len(ctes) > 0:
            n = len(ctes)
            parte = conta.valor_bruto / Decimal(n)
            valores_finais = [parte for _ in ctes]
        else:
            valores_finais = [conta.valor_bruto for _ in ctes]

        out: List[ContaReceber] = []
        for cinfo, val in zip(ctes, valores_finais):
            ncte = cinfo.get("numero_cte")
            if not ncte:
                continue
            dem_cte = self._celula_para_data(cinfo.get("data_emissao"))
            dest = cinfo.get("destinatario_nome")
            dcnpj = cinfo.get("destinatario_cnpj")
            cnpj_candidato = (
                str(dcnpj).strip()
                if dcnpj is not None and str(dcnpj).strip() and not pd_isna(dcnpj)
                else None
            ) or conta.cliente_cnpj
            cnpj_fmt = (
                self._formatar_cnpj_cpf_cliente(cnpj_candidato)
                or (cnpj_candidato or "")
            )

            novo = replace(
                conta,
                numero_documento=str(ncte).strip(),
                numero_fatura=nf,
                valor_bruto=val if val is not None else conta.valor_bruto,
                data_emissao=dem_cte or conta.data_emissao,
                cliente_nome=(str(dest).strip() if dest and not pd_isna(dest) else None) or conta.cliente_nome,
                cliente_cnpj=cnpj_fmt,
                _tipo_documento=TipoDocumento.DACTE,
            )
            out.append(novo)

        return out if out else [conta]
    
    def _encontrar_coluna(self, df: pd.DataFrame, campo: str) -> Optional[str]:
        """Encontra a coluna correspondente no DataFrame"""
        possiveis = self.COLUNAS_BESTSOFT.get(campo, [campo])
        
        for col in possiveis:
            if col in df.columns:
                return col
            col_lower = col.lower()
            for df_col in df.columns:
                if df_col.lower() == col_lower:
                    return df_col
        
        return None
    
    def _converter_para_contas(self):
        """Converte o DataFrame para lista de ContaReceber"""
        self.contas = []
        
        if self.df_contas is None:
            return
        
        df = self.df_contas
        
        col_map = {}
        for campo in self.COLUNAS_BESTSOFT:
            col = self._encontrar_coluna(df, campo)
            if col:
                col_map[campo] = col
                logger.debug(f"  Mapeado: {campo} -> {col}")
        if "cliente_cnpj" not in col_map:
            alt = self._primeira_coluna_cnpj_cpf_por_nome(df, "Contas a receber")
            if alt:
                col_map["cliente_cnpj"] = alt
        
        for idx, row in df.iterrows():
            try:
                conta = self._row_to_conta(row, col_map)
                if conta and (conta.numero_documento or conta.valor_bruto):
                    self.contas.append(conta)
            except Exception as e:
                logger.warning(f"Erro ao converter linha {idx}: {e}")
        
        logger.info(f"Convertidas {len(self.contas)} contas válidas")
    
    def _row_to_conta(self, row: pd.Series, col_map: Dict[str, str]) -> Optional[ContaReceber]:
        """Converte uma linha do DataFrame para ContaReceber"""
        
        def get_val(campo: str, default=None):
            col = col_map.get(campo)
            if not col:
                return default
            val = row.get(col, default)
            return default if pd_isna(val) else val
        
        def get_date(campo: str) -> Optional[date]:
            col = col_map.get(campo)
            if not col:
                return None
            val = row.get(col)
            return ReceiptProcessingService._celula_para_data(val)
        
        def get_decimal(campo: str) -> Optional[Decimal]:
            val = get_val(campo)
            if val is None:
                return None
            try:
                if isinstance(val, str):
                    val = val.replace('R$', '').replace('.', '').replace(',', '.').strip()
                return Decimal(str(val))
            except:
                return None
        
        numero_doc = get_val('numero_documento')
        if numero_doc is not None:
            numero_doc = str(numero_doc).strip()
            if numero_doc.replace('.', '').replace(',', '').isdigit():
                numero_doc = str(int(float(numero_doc.replace(',', '.'))))
        
        numero_fatura = get_val('numero_fatura')
        if numero_fatura is not None and not pd_isna(numero_fatura):
            numero_fatura = str(int(float(numero_fatura))) if isinstance(numero_fatura, (int, float)) else str(numero_fatura).strip()
        else:
            numero_fatura = None
        
        origem = str(get_val('origem') or '').lower()
        classificacao = str(get_val('classificacao') or '').lower()
        
        conta = ContaReceber(
            numero_documento=numero_doc,
            numero_fatura=numero_fatura,
            data_baixa=get_date('data_baixa'),
            data_emissao=get_date('data_emissao'),
            data_vencimento=get_date('data_vencimento'),
            cliente_nome=str(get_val('cliente_nome') or ''),
            cliente_codigo=str(get_val('cliente_codigo') or ''),
            cliente_cnpj=self._formatar_cnpj_cpf_cliente(get_val('cliente_cnpj')),
            valor_bruto=get_decimal('valor_bruto'),
            valor_liquido=get_decimal('valor_liquido'),
            juros=get_decimal('juros'),
            desconto=get_decimal('desconto'),
            banco=str(get_val('banco') or 'ITAU'),
            centro_custo=self._sigla_centro_custo(get_val('centro_custo')),
            observacao=str(get_val('observacao') or ''),
        )
        
        if 'conhecimento' in origem:
            conta._tipo_documento = TipoDocumento.DACTE
        elif 'rendimento' in classificacao:
            conta._tipo_documento = TipoDocumento.DOC
        
        return conta
    
    def filtrar_por_data_baixa(self, data_baixa: date) -> List[ContaReceber]:
        """
        Filtra as contas pela data de baixa (pagamento).
        
        Args:
            data_baixa: Data para filtrar
            
        Returns:
            Lista de contas filtradas
        """
        filtradas = [c for c in self.contas if c.data_baixa == data_baixa]
        logger.info(f"Filtradas {len(filtradas)} contas por data de baixa {data_baixa}")
        return filtradas
    
    def classificar_por_tipo(self, contas: List[ContaReceber]) -> Dict[TipoDocumento, List[ContaReceber]]:
        """
        Classifica as contas por tipo de documento.
        
        Args:
            contas: Lista de contas
            
        Returns:
            Dicionário com listas por tipo
        """
        resultado = {
            TipoDocumento.NFS: [],
            TipoDocumento.NFE: [],
            TipoDocumento.DOC: [],
            TipoDocumento.DACTE: [],
        }
        
        for conta in contas:
            tipo = conta.tipo_documento
            resultado[tipo].append(conta)
        
        for tipo, lista in resultado.items():
            logger.info(f"  {tipo.value}: {len(lista)} contas")
        
        return resultado
    
    def registrar_duplicados_existentes(self, duplicados: Set[str]):
        """Registra os documentos que já existem na planilha"""
        self._duplicados_existentes = duplicados
        logger.info(f"Registrados {len(duplicados)} documentos existentes")
    
    def verificar_duplicado(self, conta: ContaReceber) -> bool:
        """Verifica se a conta já existe na planilha"""
        return conta.chave_duplicado in self._duplicados_existentes
    
    def conta_para_linha(self, conta: ContaReceber):
        """
        Converte uma ContaReceber para a linha apropriada baseado no tipo.
        
        Returns:
            LinhaPlantilhaNFS, LinhaPlantilhaNFE, LinhaPlantilhaDOC ou LinhaPlantilhaDACTE
        """
        tipo = conta.tipo_documento
        
        if tipo == TipoDocumento.NFS:
            return LinhaPlantilhaNFS(
                data_recebimento=conta.data_baixa,
                banco=conta.banco,
                nota_fiscal=conta.numero_documento,
                data_emissao=conta.data_emissao,
                cliente=conta.cliente_nome,
                codigo_pagador=conta.cliente_codigo,
                cnpj=self._formatar_cnpj_cpf_cliente(conta.cliente_cnpj) or conta.cliente_cnpj,
                centro_custo=self._sigla_centro_custo(conta.centro_custo) or conta.centro_custo,
                valor_bruto=conta.valor_bruto,
                liquido_receber=conta.valor_liquido or conta.valor_bruto,
                juros=conta.juros,
                descontos=conta.desconto,
            )
        
        elif tipo == TipoDocumento.NFE:
            return LinhaPlantilhaNFE(
                data_recebimento=conta.data_baixa,
                banco=conta.banco,
                nfe=conta.numero_documento,
                data_emissao=conta.data_emissao,
                cliente=conta.cliente_nome,
                codigo_pagador=conta.cliente_codigo,
                cnpj=self._formatar_cnpj_cpf_cliente(conta.cliente_cnpj) or conta.cliente_cnpj,
                centro_custo=self._sigla_centro_custo(conta.centro_custo) or conta.centro_custo,
                valor=conta.valor_bruto,
                liquido_receber=conta.valor_liquido or conta.valor_bruto,
                juros=conta.juros,
                desconto=conta.desconto,
            )
        
        elif tipo == TipoDocumento.DOC:
            mes_ano = ""
            dd_doc = converter_valor_para_date_seguro(conta.data_baixa)
            if dd_doc:
                meses = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun',
                         'jul', 'ago', 'set', 'out', 'nov', 'dez']
                mes_ano = f"{meses[dd_doc.month - 1]}/{str(dd_doc.year)[-2:]}"
            
            return LinhaPlantilhaDOC(
                data_recebimento=conta.data_baixa,
                banco=conta.cliente_nome if not conta.banco else conta.banco,
                doc=mes_ano,
                descricao=conta.observacao or "Serviço a Identificar",
                cliente=conta.cliente_nome,
                centro_custo=self._sigla_centro_custo(conta.centro_custo) or (conta.centro_custo or "LLM"),
                valor=conta.valor_bruto,
            )
        
        else:  # DACTE
            return LinhaPlantilhaDACTE(
                data_recebimento=conta.data_baixa,
                banco=conta.banco or "ITAU S/A",
                dacte=conta.numero_documento,
                status="Único",
                cliente=conta.cliente_nome,
                codigo_pagador=conta.cliente_codigo,
                cnpj=self._formatar_cnpj_cpf_cliente(conta.cliente_cnpj) or conta.cliente_cnpj,
                centro_custo=self._sigla_centro_custo(conta.centro_custo) or conta.centro_custo,
                valor_bruto=conta.valor_bruto,
                numero_fatura=conta.numero_fatura or conta.numero_documento,
            )

    def _metadado_auditoria_linha(self, conta: ContaReceber, tipo: TipoDocumento) -> Dict[str, Any]:
        """Identificadores para log de auditoria (documento / fatura por seção)."""
        if conta.numero_fatura is None or pd_isna(conta.numero_fatura):
            nf = None
        else:
            s = str(conta.numero_fatura).strip()
            nf = s or None

        if tipo == TipoDocumento.DOC:
            doc = (conta.numero_documento or "").strip()
            if not doc:
                doc = ((conta.cliente_nome or "")[:50] or "-")
            return {"secao": tipo.value, "documento": doc, "fatura": nf}

        doc = (conta.numero_documento or "").strip() or "-"
        return {"secao": tipo.value, "documento": doc, "fatura": nf}

    def obter_pacotes_envio_por_secao(
        self,
        relatorio: RelatorioProcessamento,
    ) -> Dict[TipoDocumento, List[Dict[str, Any]]]:
        """
        Linhas da planilha + metadados para auditoria (documento, fatura, seção).
        Cada item: row (list), secao, documento, fatura.
        """
        pacotes: Dict[TipoDocumento, List[Dict[str, Any]]] = {
            TipoDocumento.NFS: [],
            TipoDocumento.NFE: [],
            TipoDocumento.DOC: [],
            TipoDocumento.DACTE: [],
        }
        for resultado in relatorio.resultados:
            if not (resultado.sucesso and not resultado.duplicado):
                continue
            t = resultado.tipo_documento
            linha_obj = self.conta_para_linha(resultado.conta)
            meta = self._metadado_auditoria_linha(resultado.conta, t)
            pacotes[t].append({"row": linha_obj.to_row(), **meta})
        return pacotes

    def processar(
        self,
        data_baixa: date,
        caminho_arquivo: Optional[str] = None
    ) -> RelatorioProcessamento:
        """
        Processa as contas a receber para uma data específica.
        
        Fluxo:
        1. Carrega arquivo se fornecido
        2. Filtra por data de baixa
        3. Classifica por tipo de documento
        4. Prepara linhas para cada seção
        
        Args:
            data_baixa: Data de baixa para filtrar
            caminho_arquivo: Caminho do arquivo (opcional se já carregado)
            
        Returns:
            RelatorioProcessamento com todos os resultados
        """
        meses = ['JAN', 'FEV', 'MAR', 'ABR', 'MAI', 'JUN',
                 'JUL', 'AGO', 'SET', 'OUT', 'NOV', 'DEZ']
        
        relatorio = RelatorioProcessamento(
            data_filtro=data_baixa,
            data_processamento=datetime.now(),
            aba_destino=f"{meses[data_baixa.month - 1]} {str(data_baixa.year)[-2:]}"
        )
        
        if caminho_arquivo:
            sucesso, msg = self.carregar_contas_receber(caminho_arquivo)
            if not sucesso:
                relatorio.erros.append(msg)
                return relatorio
        
        if not self.contas:
            relatorio.erros.append("Nenhuma conta carregada")
            return relatorio
        
        contas_filtradas = self.filtrar_por_data_baixa(data_baixa)
        
        if not contas_filtradas:
            relatorio.erros.append(f"Nenhuma conta encontrada para data {data_baixa}")
            return relatorio
        
        contas_para_classificar: List[ContaReceber] = []
        for conta in contas_filtradas:
            c_nf = self._enriquecer_conta_com_lista_nf(conta)
            c_cte = self._enriquecer_cnpj_dacte_pela_lista_conhecimento(c_nf)
            contas_para_classificar.extend(self.expandir_conta_para_ctes_na_lista(c_cte))
        
        por_tipo = self.classificar_por_tipo(contas_para_classificar)

        chaves_ja_vistas_no_arquivo: Set[str] = set()

        for tipo, contas in por_tipo.items():
            for conta in contas:
                k = conta.chave_duplicado
                duplicado_planilha = self.verificar_duplicado(conta)
                duplicado_repetido_no_arquivo = bool(k) and k in chaves_ja_vistas_no_arquivo
                if k:
                    chaves_ja_vistas_no_arquivo.add(k)

                duplicado = duplicado_planilha or duplicado_repetido_no_arquivo
                if duplicado_planilha:
                    msg = "Duplicado ignorado (ja na planilha)"
                elif duplicado_repetido_no_arquivo:
                    msg = "Duplicado ignorado (mesmo documento e data repetidos no arquivo)"
                else:
                    msg = "Pronto para inserir"

                resultado = ResultadoProcessamento(
                    conta=conta,
                    tipo_documento=tipo,
                    duplicado=duplicado,
                    sucesso=not duplicado,
                    mensagem=msg,
                )

                relatorio.adicionar_resultado(resultado)
        
        logger.info(relatorio.get_resumo())
        return relatorio
    
    def obter_linhas_por_secao(
        self,
        relatorio: RelatorioProcessamento
    ) -> Dict[TipoDocumento, List[list]]:
        """
        Converte os resultados em linhas organizadas por seção.

        Returns:
            Dicionário com listas de linhas por tipo de documento
        """
        return {
            t: [p["row"] for p in lst]
            for t, lst in self.obter_pacotes_envio_por_secao(relatorio).items()
        }

    def exportar_para_excel(
        self,
        relatorio: RelatorioProcessamento,
        caminho_saida: str
    ) -> Tuple[bool, str]:
        """
        Exporta o relatório para um arquivo Excel local.
        """
        try:
            dados = []
            for resultado in relatorio.resultados:
                conta = resultado.conta
                dados.append({
                    'Tipo': resultado.tipo_documento.value,
                    'Número': conta.numero_documento,
                    'Data Baixa': conta.data_baixa,
                    'Data Emissão': conta.data_emissao,
                    'Cliente': conta.cliente_nome,
                    'CNPJ': conta.cliente_cnpj,
                    'Valor Bruto': float(conta.valor_bruto) if conta.valor_bruto else 0,
                    'Centro Custo': conta.centro_custo,
                    'Duplicado': 'Sim' if resultado.duplicado else 'Não',
                    'Status': resultado.mensagem,
                })
            
            df = pd.DataFrame(dados)
            df.to_excel(caminho_saida, index=False, sheet_name='Recebimentos')
            
            logger.info(f"Relatório exportado para: {caminho_saida}")
            return True, f"Exportado com sucesso: {caminho_saida}"
            
        except Exception as e:
            msg = f"Erro ao exportar: {str(e)}"
            logger.error(msg)
            return False, msg


class PlanilhaLocator:
    """Localiza seções dentro da planilha de destino"""
    
    # Vários gabaritos usam nomes diferentes na coluna do rótulo (ex.: só "CTE" ou "CT-E" sem "DACTE").
    IDENTIFICADORES_SECAO: Dict[TipoDocumento, Tuple[str, ...]] = {
        # NFE antes de NFS: células "NOTA FISCAL ELETRÔNICA" contêm também o trecho "NOTA FISCAL".
        TipoDocumento.NFE: ("NOTA FISCAL ELETRÔNICA", "NF-E", "NFE"),
        TipoDocumento.NFS: ("NOTA FISCAL", "NFS", "NF SERVIÇO"),
        TipoDocumento.DOC: ("DOCUMENTO", "DOC"),
        TipoDocumento.DACTE: (
            "DACTE",
            "DACT-E",
            "CT-E",
            "CONHECIMENTO DE TRANSPORTE",
            "CONHECIMENTO",
            "CTE",
        ),
    }

    @staticmethod
    def _texto_secao_para_match(val: Any) -> str:
        if val is None or val == "":
            return ""
        s = unicodedata.normalize("NFD", str(val).strip())
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        s = s.upper().replace("–", "-").replace("—", "-")
        return re.sub(r"\s+", " ", s).strip()
    
    @classmethod
    def localizar_secoes(cls, dados_planilha: List[List]) -> Dict[TipoDocumento, SecaoPlanilha]:
        """
        Localiza as seções dentro dos dados da planilha.
        
        Args:
            dados_planilha: Lista de linhas (valores) da planilha
            
        Returns:
            Dicionário com informações de cada seção encontrada
        """
        secoes = {}
        
        for idx, linha in enumerate(dados_planilha):
            if len(linha) < 4:
                continue
            
            celula_d = str(linha[3]).strip() if linha[3] else ""
            valor_norm = cls._texto_secao_para_match(celula_d)

            for tipo, marcas_brutas in cls.IDENTIFICADORES_SECAO.items():
                # Marcadores mais longos primeiro para evitar "CTE" ganhar de "DACTE".
                ordenadas = sorted(marcas_brutas, key=len, reverse=True)
                encontrou = False
                for marca in ordenadas:
                    m = cls._texto_secao_para_match(marca)
                    if m and m in valor_norm:
                        proxima, inserir_antes = cls._encontrar_linha_insercao(
                            dados_planilha, idx + 1, tipo
                        )
                        secoes[tipo] = SecaoPlanilha(
                            tipo=tipo,
                            linha_cabecalho=idx + 1,
                            coluna_identificadora=marca,
                            proxima_linha_vazia=proxima,
                            inserir_linhas_antes=inserir_antes,
                        )
                        logger.info(
                            "Seção %s encontrada na linha %s (marcador %r na célula %r)",
                            tipo.value,
                            idx + 1,
                            marca,
                            celula_d[:80],
                        )
                        encontrou = True
                        break
                if encontrou:
                    break
        
        return secoes
    
    @classmethod
    def _celula_nao_vazia(cls, c: Any) -> bool:
        if c is None or pd_isna(c):
            return False
        s = str(c).strip()
        return bool(s) and s.upper() != "#N/D"

    @classmethod
    def _expandir_linha_planilha(
        cls, linha: List[Any], min_cols: int = 14
    ) -> List[Any]:
        """Evita linha cortada pela API Graph (usedRange não preenche células vazias à direita)."""
        if len(linha) >= min_cols:
            return list(linha)
        return list(linha) + [""] * (min_cols - len(linha))

    @classmethod
    def _documento_tipico_coluna_d(cls, linha: List) -> bool:
        """Coluna D com número de NF/CT — linha típica de lançamento, não subtotal."""
        if len(linha) < 4:
            return False
        nd = _norm_documento_duplicado(linha[3])
        return bool(nd and nd.isdigit() and len(nd) >= 2)

    @classmethod
    def _linha_parece_lancamento_por_secao(
        cls, tipo: TipoDocumento, linha: List
    ) -> bool:
        """
        Identifica linha de dados da seção. DOC pode ter texto/abrev em D — não só dígitos;
        só olhar dígitos marca lançamentos de DOC falsamente como rodapé.
        """
        linha_pad = cls._expandir_linha_planilha(linha)
        if len(linha_pad) < 2:
            return False

        tem_banco_ou_doc = cls._celula_nao_vazia(
            linha_pad[2] if len(linha_pad) > 2 else None
        ) or cls._celula_nao_vazia(linha_pad[3] if len(linha_pad) > 3 else None)

        if converter_valor_para_date_seguro(linha_pad[1]) is not None:
            return True

        if tipo == TipoDocumento.DOC:
            if cls._celula_nao_vazia(linha_pad[3]):
                return True
            if len(linha_pad) > 4 and cls._celula_nao_vazia(linha_pad[4]):
                return True
            if len(linha_pad) > 5 and cls._celula_nao_vazia(linha_pad[5]):
                return True
            return False

        if tipo in (
            TipoDocumento.NFS,
            TipoDocumento.NFE,
            TipoDocumento.DACTE,
        ):
            if cls._documento_tipico_coluna_d(linha_pad):
                return True
            if converter_valor_para_date_seguro(linha_pad[1]) is not None and (
                cls._celula_nao_vazia(linha_pad[2])
                or cls._celula_nao_vazia(linha_pad[3])
            ):
                return True
            return False

        return cls._documento_tipico_coluna_d(linha_pad) or tem_banco_ou_doc

    @classmethod
    def _bcd_sem_identificacao(cls, linha: List) -> bool:
        """Rodapês somam só valores: B,C,D costumam vazios nos DOC/DACTE analisados."""
        linha_eff = cls._expandir_linha_planilha(linha)
        for idx in (1, 2, 3):
            if cls._celula_nao_vazia(linha_eff[idx]):
                return False
        return True

    @classmethod
    def _valor_numerico_parece_total(cls, val: Any) -> bool:
        """Total sem texto R$ nem decimais (1182 inteiro ou 1182.0)."""
        if isinstance(val, float):
            fv = abs(float(val))
            if fv < 1e-9:
                return False
            if not float(val).is_integer():
                return True
            iv = int(round(fv))
        elif isinstance(val, int):
            iv = abs(int(val))
        else:
            return False
        if iv < 50:
            return False
        if 2000 <= iv <= 2100:
            return False
        if iv <= 31:
            return False
        return True

    @classmethod
    def _linha_so_totais_numer_sem_texto_extra(cls, linha: List) -> bool:
        linha_eff = cls._expandir_linha_planilha(linha)
        if not cls._bcd_sem_identificacao(linha_eff):
            return False
        vistos = 0
        for c in linha_eff[4:]:
            if not cls._celula_nao_vazia(c):
                continue
            if cls._texto_indicio_monetario_br(c):
                return True
            if cls._valor_numerico_parece_total(c):
                vistos += 1
        return vistos >= 1

    @classmethod
    def _texto_indicio_monetario_br(cls, val: Any) -> bool:
        if isinstance(val, float):
            return not float(val).is_integer()
        if isinstance(val, int):
            return False
        if not cls._celula_nao_vazia(val):
            return False
        s = str(val).strip().upper()
        if "R$" in s:
            return True
        return bool(
            re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}\b|\d+,\d{2}\b", str(val).strip())
        )

    @classmethod
    def _linha_tem_valores_na_parte_direita(
        cls, linha: List, primeira_col_direita_py: int = 5
    ) -> bool:
        """Coluna F em diante (índice ≥5): típico de subtotais só à direita, B-E vazias."""
        if len(linha) <= primeira_col_direita_py:
            return False
        return any(cls._celula_nao_vazia(c) for c in linha[primeira_col_direita_py:])

    @classmethod
    def _linha_tem_indicadores_de_total(cls, linha: List) -> bool:
        """
        Inclui: R$/formato brasileiro, número decimal (Excel frequentemente não manda 'R$'),
        ou células preenchidas só após coluna E.
        """
        if not linha:
            return False
        textos = [
            str(c).strip().upper()
            for c in linha
            if cls._celula_nao_vazia(c)
        ]
        joined = " ".join(textos)
        if "SUBTOTAL" in joined or re.search(r"\bSOMA\s*\(", joined):
            return True
        if cls._linha_tem_valores_na_parte_direita(linha):
            return True
        for c in linha:
            if cls._texto_indicio_monetario_br(c):
                return True
        return False

    @classmethod
    def _linha_eh_rodape_resumo(cls, linha: List, tipo: TipoDocumento) -> bool:
        """
        Linha de total/resumo no fim da seção. Depende do tipo: DOC usa texto em D;
        DACTE pode ter total só em coluna numérica inteira com B-D vazios.
        """
        if not linha:
            return False
        linha_pad = cls._expandir_linha_planilha(linha)
        if cls._linha_parece_lancamento_por_secao(tipo, linha):
            return False
        if cls._linha_tem_indicadores_de_total(linha_pad):
            return True
        if cls._linha_so_totais_numer_sem_texto_extra(linha_pad):
            return True
        return False

    @classmethod
    def _encontrar_linha_insercao(
        cls,
        dados: List[List],
        linha_cabecalho_1based: int,
        tipo: TipoDocumento,
    ) -> Tuple[int, bool]:
        """
        Onde gravar a próxima linha (1-based Excel) e se é preciso insert (deslocar) antes.

        Se existir linha de total/rodapé antes da primeira linha em branco em B–E,
        devolve essa linha e `inserir_linhas_antes=True` para não sobrescrever o total.
        """
        first_py = linha_cabecalho_1based
        for py in range(first_py, len(dados)):
            linha_raw = dados[py]
            if not isinstance(linha_raw, list):
                linha_raw = []
            linha_pad = cls._expandir_linha_planilha(linha_raw)

            if cls._linha_eh_rodape_resumo(linha_raw, tipo):
                return py + 1, True

            tem_dados = any(
                cell and str(cell).strip() and str(cell).strip() != "#N/D"
                for cell in linha_pad[1:5]
            )

            if not tem_dados:
                return py + 1, False

        return len(dados) + 1, False

    @classmethod
    def extrair_documentos_existentes(
        cls,
        dados_planilha: List[List],
        secoes: Dict[TipoDocumento, SecaoPlanilha]
    ) -> Set[str]:
        """
        Extrai os documentos já existentes na planilha para verificação de duplicados.
        
        Returns:
            Set de chaves de duplicado (numero_data)
        """
        existentes = set()
        
        for tipo, secao in secoes.items():
            inicio_py = secao.linha_cabecalho
            insert_linha_excel = secao.proxima_linha_vazia
            fim_exclusivo_py = min(insert_linha_excel - 1, len(dados_planilha))

            for idx_linha in range(inicio_py, fim_exclusivo_py):
                linha = dados_planilha[idx_linha]

                if len(linha) < 4:
                    continue

                raw_doc = linha[3]
                raw_bt = linha[1] if len(linha) > 1 else None
                chave = chave_duplicado_normalizada(raw_doc, raw_bt)
                num_part = _norm_documento_duplicado(raw_doc)
                if num_part and str(num_part).strip().upper() != "#N/D":
                    existentes.add(chave)

        logger.info(f"Extraídos {len(existentes)} documentos existentes nas seções")
        return existentes
