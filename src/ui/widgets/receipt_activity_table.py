"""
Tabela de atividade recente: envios de recebimento para planilha (auditoria).
"""

from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
from PyQt6.QtCore import Qt

from src.services.audit_service import AuditService


_ACTION_LABEL = {
    "receipt_send_ok": "Sucesso",
    "receipt_send_partial": "Parcial",
    "receipt_send_failed": "Falha",
}


def format_insercoes_resumo(details: dict, max_itens: int = 40, max_chars: int = 1600) -> str:
    """Texto legível: linha na planilha, seção, documento (CTE/NFS/NFE), fatura quando houver."""
    ins = details.get("insercoes") or []
    if not ins:
        return ""
    partes = []
    for it in ins[:max_itens]:
        ln = it.get("linha_planilha")
        sec = it.get("secao", "")
        doc = it.get("documento", "")
        fat = it.get("fatura")
        ped = f"L{ln} {sec} {doc}"
        if fat:
            ped += f" NF {fat}"
        partes.append(ped)
    texto = " • ".join(partes)
    total = int(details.get("insercoes_total") or len(ins))
    mostrados = min(len(ins), max_itens)
    extras = total - mostrados
    if extras > 0:
        texto += f" — …+{extras} não listados aqui"
    if details.get("insercoes_truncadas"):
        texto += " [log salva no máximo 500 itens]"
    if len(texto) > max_chars:
        texto = texto[: max_chars - 1] + "…"
    return texto


def _resumo_linha(details: dict, action: str) -> str:
    if not details:
        return "—"
    tot = details.get("total_inseridos")
    pc = details.get("por_secao") or {}
    tb = ",".join(f"{k}:{v}" for k, v in sorted(pc.items())) if pc else ""
    parts = []
    if tot is not None:
        parts.append(f"{tot} linha(s)")
    if tb:
        parts.append(tb)
    if action in ("receipt_send_failed", "receipt_send_partial"):
        msg = details.get("mensagem_erro") or ""
        if msg:
            parts.append(str(msg)[:120])
    ar = details.get("arquivo_entrada_contas")
    if ar:
        parts.append(f"entrada: {ar}")
    ins_txt = format_insercoes_resumo(details)
    if ins_txt:
        parts.append(ins_txt)
    return " | ".join(parts) if parts else "—"


class ReceiptActivityTable(QTableWidget):
    """Últimos envios registrados ao gravar na planilha do OneDrive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        cols = ["Data/Hora", "Usuário", "E-mail", "Resultado", "Destino", "Linhas", "Resumo"]
        self.setColumnCount(len(cols))
        self.setHorizontalHeaderLabels(cols)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def refresh(self, limit: int = 50) -> None:
        logs = AuditService.get_recent_receipt_audit_logs(limit=limit)
        self.setRowCount(0)
        for raw in logs:
            f = AuditService.format_log(raw)
            details = f.get("details") or {}
            row = self.rowCount()
            self.insertRow(row)

            dest = (
                details.get("filename")
                or f"{details.get('planilha', '')} [{details.get('aba', '')}]".strip()
            )

            valores = [
                f.get("created_at_formatted", "—"),
                f.get("user_name", "—"),
                f.get("user_email", "—"),
                _ACTION_LABEL.get(f.get("action", ""), f.get("action", "—")),
                str(dest)[:200] if dest else "—",
                str(details.get("total_inseridos", "—")),
                _resumo_linha(details, f.get("action", "")),
            ]
            for col, texto in enumerate(valores):
                it = QTableWidgetItem(str(texto))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(row, col, it)
