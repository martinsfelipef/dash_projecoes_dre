"""
Parser do Relatório de Unidades — SIENGE
Arquivo: Relatorio_de_unidades_*.xlsx (Unidades por Empreendimento - Sintético)

Detecta o cabeçalho dinamicamente buscando palavras-chave nas primeiras linhas.
Classifica as unidades por status (col que contém "status" ou "situação").

Retorna dict com:
  "total_unidades"      : int   — aptos+salas SEM garagens/depósitos
  "vendidas"            : int   — status == "Vendida" (ou variações)
  "disponiveis"         : int   — status == "Disponível" (ou variações)
  "permuta"             : int   — status == "Permuta"
  "vgv_vendido"         : float — soma dos valores das unidades Vendidas
  "preco_medio"         : float — vgv_vendido / vendidas, ou 0
  "vgv_disponivel"      : float — soma dos valores das unidades Disponíveis
  "unidades_permuta"    : list  — nomes das unidades em permuta
  "unidades_disponiveis": list  — nomes das unidades disponíveis
  "por_status"          : dict  — {status: {"count", "valor", "unidades"}}
  "obra_nome"           : str
  "arquivo_nome"        : str
  "data_upload"         : str   — ISO datetime
  "erro"                : str   — presente apenas se falhar
"""

import io
import pandas as pd
from datetime import datetime
from collections import defaultdict

# Valores de status aceitos (case-insensitive)
_STATUS_VENDIDA    = {"vendida", "vendido", "vendido/terceiros", "vendido terceiros"}
_STATUS_DISPONIVEL = {"disponível", "disponivel"}
_STATUS_PERMUTA    = {"permuta"}

# Palavras que identificam unidades NÃO comercializáveis (excluir do total)
_EXCLUIR_NOME = {"garagem", "depos", "estacion", "vaga", "box"}


def _e_unidade_principal(nome: str) -> bool:
    """True se a unidade deve ser contabilizada (não é garagem/depósito)."""
    nome_lower = str(nome).lower()
    return not any(p in nome_lower for p in _EXCLUIR_NOME)


def _norm(v) -> str:
    return str(v).lower().strip()


def parse_unidades_sienge(data: bytes, arquivo_nome: str = "") -> dict:
    """
    Parseia relatório 'Unidades por Empreendimento (Sintético)' do SIENGE.

    Estratégia:
      1. Lê o Excel sem header (header=None) para ver o arquivo cru.
      2. Procura a linha de cabeçalho varrendo as primeiras 15 linhas.
      3. Identifica colunas de nome/unidade, status e valor por palavras-chave.
      4. Processa cada linha filtrando garagens pelo nome.
    """
    try:
        df_raw = pd.read_excel(io.BytesIO(data), header=None, dtype=str)
    except Exception as e:
        return {"erro": f"Não foi possível abrir o arquivo: {e}"}

    if df_raw.empty:
        return {"erro": "Arquivo vazio."}

    # ── 1. Extrai nome da obra das primeiras linhas ───────────────────
    obra_nome = ""
    for _, row in df_raw.head(10).iterrows():
        vals = [str(v).strip() for v in row if pd.notna(v) and str(v).strip()]
        for i, v in enumerate(vals):
            if "empreendimento" in v.lower() and i + 1 < len(vals):
                obra_nome = vals[i + 1]
                break
        if obra_nome:
            break

    # ── 2. Detecta linha de cabeçalho ────────────────────────────────
    header_row_idx = None
    _HEADER_KEYWORDS = {"unidade", "código", "codigo", "status", "situação", "situacao", "nome"}

    for idx, row in df_raw.head(20).iterrows():
        row_vals = [_norm(v) for v in row if pd.notna(v) and str(v).strip() != "nan"]
        matches = sum(1 for v in row_vals if any(kw in v for kw in _HEADER_KEYWORDS))
        if matches >= 2:
            header_row_idx = idx
            break

    if header_row_idx is None:
        # Fallback: assume linha 7 (índice 7) como cabeçalho (padrão antigo SIENGE)
        header_row_idx = 7

    # ── 3. Reconstrói DataFrame com cabeçalho detectado ──────────────
    df = df_raw.iloc[header_row_idx:].copy()
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)

    # Normaliza nomes de coluna
    col_map = {}
    for c in df.columns:
        key = _norm(c)
        col_map[key] = c

    # ── 4. Identifica colunas por palavras-chave ──────────────────────
    def _find_col(keywords):
        for k, original in col_map.items():
            if any(kw in k for kw in keywords):
                return original
        return None

    col_nome   = _find_col(["unidade", "código", "codigo", "nome da unidade", "nome"])
    col_status = _find_col(["status", "situação", "situacao", "estoque"])
    col_tipo   = _find_col(["tipo", "tipologia", "imóvel", "imovel"])
    col_valor  = _find_col(["valor", "preço", "preco", "vgv", "contrato"])

    # Se não achou col_nome, usa primeira coluna
    if col_nome is None:
        col_nome = df.columns[0]

    # Se não achou status, tenta col 0 (padrão antigo: col A = status)
    if col_status is None:
        # verifica se col 0 parece conter status
        sample = df.iloc[:, 0].dropna().head(10).apply(_norm).tolist()
        status_like = sum(1 for v in sample if any(
            s in v for s in ["vend", "disp", "permuta"]
        ))
        if status_like >= 1:
            col_status = df.columns[0]
            # Nesse caso o nome da unidade provavelmente está na col 2
            if col_nome == df.columns[0] and len(df.columns) > 2:
                col_nome = df.columns[2]

    if col_status is None:
        return {
            "erro": (
                "Coluna de status não encontrada. "
                f"Colunas disponíveis: {list(df.columns)}"
            )
        }

    # ── 5. Processa linhas ────────────────────────────────────────────
    por_status: dict = defaultdict(lambda: {"count": 0, "valor": 0.0, "unidades": []})

    for _, row in df.iterrows():
        nome_raw   = str(row.get(col_nome, "")).strip()
        status_raw = str(row.get(col_status, "")).strip()

        # Ignora linhas vazias ou de totais
        if not nome_raw or nome_raw.lower() in ("nan", "none", "", "total"):
            continue
        if not status_raw or status_raw.lower() in ("nan", "none", ""):
            continue

        # Valor monetário
        valor = 0.0
        if col_valor is not None:
            try:
                v_str = str(row.get(col_valor, "0"))
                v_str = v_str.replace("R$", "").replace(".", "").replace(",", ".").strip()
                valor = float(v_str) if v_str not in ("nan", "none", "") else 0.0
            except (ValueError, TypeError):
                valor = 0.0

        # Filtra garagens/depósitos pelo nome
        if not _e_unidade_principal(nome_raw):
            continue

        # Também filtra pelo tipo se disponível
        if col_tipo is not None:
            tipo_raw = _norm(row.get(col_tipo, ""))
            if any(t in tipo_raw for t in _EXCLUIR_NOME):
                continue

        por_status[status_raw]["count"]    += 1
        por_status[status_raw]["valor"]    += valor
        por_status[status_raw]["unidades"].append(nome_raw)

    if not por_status:
        return {
            "erro": (
                "Nenhuma unidade comercializável encontrada. "
                "Verifique se é o relatório 'Unidades por Empreendimento' do SIENGE.\n"
                f"Cabeçalho detectado na linha {header_row_idx}. "
                f"Colunas: {list(df.columns)}"
            )
        }

    # ── 6. Agrega por categoria de status ────────────────────────────
    total_unidades = sum(d["count"] for d in por_status.values())

    vendidas = sum(
        d["count"] for s, d in por_status.items()
        if s.lower() in _STATUS_VENDIDA
    )
    permuta = sum(
        d["count"] for s, d in por_status.items()
        if s.lower() in _STATUS_PERMUTA
    )
    disponiveis = sum(
        d["count"] for s, d in por_status.items()
        if s.lower() in _STATUS_DISPONIVEL
    )
    vgv_vendido = sum(
        d["valor"] for s, d in por_status.items()
        if s.lower() in _STATUS_VENDIDA
    )
    vgv_disponivel = sum(
        d["valor"] for s, d in por_status.items()
        if s.lower() in _STATUS_DISPONIVEL
    )
    preco_medio = vgv_vendido / vendidas if vendidas > 0 else 0.0

    unidades_permuta = []
    for s, d in por_status.items():
        if s.lower() in _STATUS_PERMUTA:
            unidades_permuta.extend(d["unidades"])

    unidades_disponiveis = []
    for s, d in por_status.items():
        if s.lower() in _STATUS_DISPONIVEL:
            unidades_disponiveis.extend(d["unidades"])

    por_status_final = {
        s: {"count": d["count"], "valor": d["valor"], "unidades": d["unidades"]}
        for s, d in por_status.items()
    }

    return {
        "obra_nome":            obra_nome,
        "total_unidades":       total_unidades,
        "vendidas":             vendidas,
        "permuta":              permuta,
        "disponiveis":          disponiveis,
        "vgv_vendido":          vgv_vendido,
        "preco_medio":          preco_medio,
        "vgv_disponivel":       vgv_disponivel,
        "unidades_permuta":     sorted(unidades_permuta),
        "unidades_disponiveis": sorted(unidades_disponiveis),
        "por_status":           por_status_final,
        "arquivo_nome":         arquivo_nome,
        "data_upload":          datetime.now().isoformat(),
    }
