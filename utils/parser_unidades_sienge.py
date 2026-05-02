"""
parser_unidades_sienge.py
Parseia relatório 'Unidades por Empreendimento' do SIENGE.
Filtra por coluna Tipo — garagens nunca entram no total.
"""
import pandas as pd
import io
from datetime import datetime

_TIPOS_INCLUIR = {"apartamento", "sala térrea", "sala", "comercial", "loja", "cobertura"}
_TIPOS_EXCLUIR = {"garagem", "vaga", "depósito", "deposito", "estacionamento", "box"}
_VALOR_MAX     = 10_000_000.0  # ignora valores corrompidos no SIENGE


def _incluir_tipo(tipo: str) -> bool:
    t = tipo.lower().strip()
    for excl in _TIPOS_EXCLUIR:
        if excl in t:
            return False
    for incl in _TIPOS_INCLUIR:
        if incl in t:
            return True
    return True


def _classificar_status(status: str) -> str:
    s = status.lower().strip()
    if s in {"vendida", "vendido"}:             return "vendida"
    if s in {"disponível", "disponivel"}:       return "disponivel"
    if s == "permuta":                          return "permuta"
    if "terceiros" in s:                        return "terceiros"
    return "outro"


def parse_unidades_sienge(data: bytes, arquivo_nome: str = "") -> dict:
    try:
        df = pd.read_excel(io.BytesIO(data), header=None)

        # Localizar cabeçalho
        header_row = None
        for i in range(min(15, len(df))):
            if str(df.iloc[i, 0]).strip().lower() == "unidade":
                header_row = i
                break
        if header_row is None:
            return {"erro": "Cabeçalho 'Unidade' não encontrado."}

        # Identificar colunas pelo cabeçalho
        header    = df.iloc[header_row]
        col_tipo   = 1
        col_status = 22
        col_valor  = 15
        for j, h in enumerate(header):
            h_lower = str(h).lower().strip()
            if h_lower == "tipo":
                col_tipo = j
            elif "estoque" in h_lower:
                col_status = j
            elif "valor atual" in h_lower:
                col_valor = j

        total = vendidas = disponiveis = permuta = terceiros = 0
        vgv_vendido = 0.0
        unidades_permuta = []

        for i in range(header_row + 1, len(df)):
            row  = df.iloc[i]
            nome = str(row.iloc[0]).strip()
            tipo = str(row.iloc[col_tipo]).strip() if col_tipo < len(row) else ""

            if not nome or nome == "nan": continue
            if not tipo or tipo == "nan": continue
            if nome.lower().startswith("unidades "): continue
            if nome.lower() in ("total", "total geral"): continue
            if not _incluir_tipo(tipo): continue  # garagem — pula SEM incrementar total

            total += 1  # só incrementa APÓS passar no filtro

            status = str(row.iloc[col_status]).strip() if col_status < len(row) else ""
            valor  = 0.0
            try:
                valor = float(
                    str(row.iloc[col_valor])
                    .replace(",", ".")
                    .replace("nan", "0")
                )
            except (ValueError, TypeError, IndexError):
                valor = 0.0

            st = _classificar_status(status)
            if st == "vendida":
                vendidas += 1
                if 0 < valor <= _VALOR_MAX:
                    vgv_vendido += valor
            elif st == "disponivel":
                disponiveis += 1
            elif st == "permuta":
                permuta += 1
                unidades_permuta.append(nome)
            elif st == "terceiros":
                terceiros += 1

        preco_medio = (vgv_vendido / vendidas) if vendidas > 0 else 0.0

        return {
            "total_unidades":   total,
            "vendidas":         vendidas,
            "disponiveis":      disponiveis,
            "permuta":          permuta,
            "terceiros":        terceiros,
            "vgv_vendido":      round(vgv_vendido, 2),
            "preco_medio":      round(preco_medio, 2),
            "unidades_permuta": unidades_permuta,
            "arquivo_nome":     arquivo_nome,
            "data_upload":      datetime.now().isoformat(),
        }

    except Exception as e:
        return {"erro": f"Erro ao processar arquivo: {str(e)}"}
