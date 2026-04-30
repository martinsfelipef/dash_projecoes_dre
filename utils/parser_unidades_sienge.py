"""
Parser do Relatório de Unidades — SIENGE
Arquivo: Relatorio_de_unidades_*.xlsx (Unidades por Empreendimento - Sintético)

Retorna dict com:
  "total_unidades"      : int   — aptos+salas SEM garagens/depósitos
  "vendidas"            : int   — status == "Vendida" (ou variações)
  "disponiveis"         : int   — status == "Disponível" (ou variações)
  "permuta"             : int   — status == "Permuta"
  "vgv_vendido"         : float — soma dos valores das unidades Vendidas
  "preco_medio"         : float — vgv_vendido / vendidas, ou 0
  "unidades_permuta"    : list  — nomes das unidades em permuta
  "arquivo_nome"        : str
  "data_upload"         : str   — ISO datetime
  "erro"                : str   — presente apenas se falhar
"""

import io
import pandas as pd
from datetime import datetime

# Mapeamento de status para categoria
_STATUS_VENDIDA    = {"vendida", "vendido", "vendido/terceiros", "vendido terceiros"}
_STATUS_DISPONIVEL = {"disponível", "disponivel"}
_STATUS_PERMUTA    = {"permuta"}

# Palavras que identificam garagens/depósitos (excluir do total)
_EXCLUIR_NOME = {"garagem", "depos", "estacion", "vaga", "box"}


def _e_unidade_principal(nome: str) -> bool:
    """Retorna True se a unidade deve ser contabilizada (não é garagem/depósito)."""
    nome_lower = str(nome).lower()
    return not any(p in nome_lower for p in _EXCLUIR_NOME)


def parse_unidades_sienge(data: bytes, arquivo_nome: str = "") -> dict:
    """
    Parseia relatório 'Unidades por Empreendimento (Sintético)' do SIENGE.
    """
    try:
        # Lê sem converter tipos para não estragar floats
        df = pd.read_excel(io.BytesIO(data), header=None)

        # ── Localizar linha de cabeçalho ──────────────────────────────
        header_row = None
        _HEADER_KEYWORDS = {"unidade", "código", "codigo", "status", "situação", "situacao", "nome", "estoque"}
        
        for i, row in df.head(15).iterrows():
            vals = [str(v).lower().strip() for v in row if pd.notna(v)]
            matches = sum(1 for v in vals if any(kw in v for kw in _HEADER_KEYWORDS))
            if matches >= 2:
                header_row = i
                break

        if header_row is None:
            return {"erro": "Cabeçalho não encontrado. Verifique se o arquivo é o 'Unidades por Empreendimento - Sintético'."}

        df.columns = df.iloc[header_row]
        df = df.iloc[header_row + 1:].reset_index(drop=True)

        # ── Identificar colunas por nome ──────────────────────────────
        col_map = {str(c).lower().strip(): c for c in df.columns}

        def _find_col(keywords):
            for k, original in col_map.items():
                if any(kw in k for kw in keywords):
                    return original
            return None

        col_nome   = _find_col(["unidade", "código", "codigo", "nome"])
        if col_nome is None: col_nome = df.columns[0]
        
        col_status = _find_col(["status", "situação", "situacao", "estoque"])
        col_valor  = _find_col(["valor", "preço", "preco", "vgv", "contrato"])

        if col_status is None:
            return {"erro": f"Coluna de status/estoque não encontrada. Colunas: {list(df.columns)}"}

        # ── Processar linhas ──────────────────────────────────────────
        total = vendidas = disponiveis = permuta = 0
        vgv_vendido = 0.0
        unidades_permuta = []

        for _, row in df.iterrows():
            nome   = str(row.get(col_nome, "")).strip()
            status = str(row.get(col_status, "")).strip().lower()

            if not nome or nome.lower() in ("nan", "none", "total"):
                continue
            if not _e_unidade_principal(nome):
                continue  # garagem/depósito — ignora

            # Parse monetário seguro
            valor = 0.0
            if col_valor is not None:
                val_raw = row.get(col_valor, 0)
                if isinstance(val_raw, (int, float)):
                    valor = float(val_raw)
                else:
                    try:
                        v_str = str(val_raw).replace("R$", "").strip()
                        if v_str.lower() in ("nan", "none", ""):
                            valor = 0.0
                        elif "," in v_str and "." in v_str:
                            valor = float(v_str.replace(".", "").replace(",", "."))
                        elif "," in v_str:
                            valor = float(v_str.replace(",", "."))
                        else:
                            valor = float(v_str)
                    except (ValueError, TypeError):
                        valor = 0.0

            total += 1

            if any(s in status for s in _STATUS_VENDIDA):
                vendidas += 1
                vgv_vendido += valor
            elif any(s in status for s in _STATUS_DISPONIVEL):
                disponiveis += 1
            elif any(s in status for s in _STATUS_PERMUTA):
                permuta += 1
                unidades_permuta.append(nome)

        preco_medio = vgv_vendido / vendidas if vendidas > 0 else 0.0

        return {
            "total_unidades":   total,
            "vendidas":         vendidas,
            "disponiveis":      disponiveis,
            "permuta":          permuta,
            "vgv_vendido":      vgv_vendido,
            "preco_medio":      preco_medio,
            "unidades_permuta": sorted(unidades_permuta),
            "arquivo_nome":     arquivo_nome,
            "data_upload":      datetime.now().isoformat(),
        }

    except Exception as e:
        return {"erro": f"Erro ao processar arquivo: {str(e)}"}
