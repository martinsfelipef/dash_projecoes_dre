"""
parser_unidades_sienge.py
Parseia relatório 'Unidades por Empreendimento' do SIENGE.
Filtra por coluna Tipo — garagens nunca entram no total.
"""
import pandas as pd
import io
from datetime import datetime

# Tipos a INCLUIR no total
_TIPOS_INCLUIR = {"apartamento", "sala térrea", "sala", "comercial", "loja", "cobertura"}
# Tipos a EXCLUIR
_TIPOS_EXCLUIR = {"garagem", "vaga", "depósito", "deposito", "estacionamento", "box"}

_STATUS_VENDIDA    = {"vendida", "vendido"}
_STATUS_DISPONIVEL = {"disponível", "disponivel"}
_STATUS_PERMUTA    = {"permuta"}
_STATUS_TERCEIROS  = {"vendido/terceiros", "vendida/terceiros"}

# Valor máximo aceitável por unidade (filtra corrompidos do SIENGE)
_VALOR_MAX = 10_000_000.0


def _incluir_tipo(tipo: str) -> bool:
    t = str(tipo).lower().strip()
    for excl in _TIPOS_EXCLUIR:
        if excl in t:
            return False
    for incl in _TIPOS_INCLUIR:
        if incl in t:
            return True
    return True  # incluir por padrão se tipo desconhecido


def _classificar_status(status: str) -> str:
    s = str(status).lower().strip()
    if s in _STATUS_VENDIDA:    return "vendida"
    if s in _STATUS_DISPONIVEL: return "disponivel"
    if s in _STATUS_PERMUTA:    return "permuta"
    if any(t in s for t in _STATUS_TERCEIROS): return "terceiros"
    return "outro"


def parse_unidades_sienge(data: bytes, arquivo_nome: str = "") -> dict:
    """
    Parseia relatório 'Unidades por Empreendimento' do SIENGE.

    Retorna dict com totais por status, VGV e preço médio.
    Garagens e depósitos são excluídos pelo campo Tipo.
    """
    try:
        df = pd.read_excel(io.BytesIO(data), header=None)

        # Localizar linha de cabeçalho (coluna 0 = "Unidade")
        header_row = None
        for i in range(min(15, len(df))):
            if str(df.iloc[i, 0]).strip().lower() == "unidade":
                header_row = i
                break

        if header_row is None:
            # Busca secundária
            for i in range(min(15, len(df))):
                vals = [str(v).lower() for v in df.iloc[i]]
                if "tipo" in vals and "unidade" in vals:
                    header_row = i
                    break

        if header_row is None:
            return {"erro": "Cabeçalho 'Unidade' não encontrado no arquivo."}

        # Identificar colunas pelo cabeçalho
        header = df.iloc[header_row]
        col_tipo   = 1   # default
        col_status = 22  # default (Estoque Comercial)
        col_valor  = 15  # default (Valor atual)

        for j, h in enumerate(header):
            h_lower = str(h).lower().strip()
            if h_lower == "tipo":
                col_tipo = j
            elif "estoque" in h_lower or "status" in h_lower:
                col_status = j
            elif "valor atual" in h_lower or h_lower == "valor":
                col_valor = j

        # Processar linhas de dados
        total = vendidas = disponiveis = permuta = terceiros = 0
        vgv_vendido = 0.0
        unidades_permuta = []

        for i in range(header_row + 1, len(df)):
            row  = df.iloc[i]
            nome = str(row.iloc[0]).strip()
            tipo = str(row.iloc[col_tipo]).strip() if col_tipo < len(row) else ""

            # Ignorar linhas vazias e linhas de rodapé/totais
            if not nome or nome.lower() in ("nan", "none", ""): continue
            if not tipo or tipo.lower() in ("nan", "none", ""): continue
            if nome.lower().startswith("unidades "): continue
            if nome.lower() in ("total", "total geral"): continue

            # Filtrar por tipo — garagens ficam de fora
            if not _incluir_tipo(tipo):
                continue

            # Só incrementa total APÓS passar no filtro
            total += 1

            status = str(row.iloc[col_status]).strip() if col_status < len(row) else ""
            valor  = 0.0
            try:
                v_raw = row.iloc[col_valor]
                if isinstance(v_raw, (int, float)):
                    valor = float(v_raw)
                else:
                    valor = float(
                        str(v_raw)
                        .replace("R$", "")
                        .replace(".", "")
                        .replace(",", ".")
                        .replace("nan", "0")
                        .strip()
                    )
            except (ValueError, TypeError, IndexError):
                valor = 0.0

            st = _classificar_status(status)

            if st == "vendida":
                vendidas += 1
                # Ignorar valores corrompidos (> R$ 10M por unidade)
                if 0 < valor <= _VALOR_MAX:
                    vgv_vendido += valor
            elif st == "disponivel":
                disponiveis += 1
            elif st == "permuta":
                permuta += 1
                unidades_permuta.append(nome)
            elif st == "terceiros":
                terceiros += 1
                # Dashboard trata como vendida
                vendidas += 1
                if 0 < valor <= _VALOR_MAX:
                    vgv_vendido += valor

        preco_medio = (vgv_vendido / vendidas) if vendidas > 0 else 0.0

        return {
            "total_unidades":   total,
            "vendidas":         vendidas,
            "disponiveis":      disponiveis,
            "permuta":          permuta,
            "terceiros":        terceiros,
            "vgv_vendido":      round(vgv_vendido, 2),
            "preco_medio":      round(preco_medio, 2),
            "unidades_permuta": sorted(unidades_permuta),
            "arquivo_nome":     arquivo_nome,
            "data_upload":      datetime.now().isoformat(),
        }

    except Exception as e:
        return {"erro": f"Erro ao processar arquivo: {str(e)}"}
