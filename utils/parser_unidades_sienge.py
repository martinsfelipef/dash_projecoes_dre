"""
parser_unidades_sienge.py
Parseia relatório 'Unidades por Empreendimento (Sintético)' do SIENGE.
Usa a coluna 'Tipo' para filtrar garagens — não o nome da unidade.
"""
import pandas as pd
import io
from datetime import datetime

# Tipos de unidade a INCLUIR no total (residencial + comercial)
_TIPOS_INCLUIR = {"apartamento", "sala térrea", "sala", "comercial", "loja", "cobertura", "unid. res."}
# Tipos a EXCLUIR explicitamente
_TIPOS_EXCLUIR = {"garagem", "vaga", "depósito", "deposito", "estacionamento", "box", "vaga de garagem"}

# Mapeamento de status do SIENGE para campos internos
_STATUS_VENDIDA    = {"vendida", "vendido"}
_STATUS_DISPONIVEL = {"disponível", "disponivel"}
_STATUS_PERMUTA    = {"permuta"}
_STATUS_TERCEIROS  = {"vendido/terceiros", "vendida/terceiros"}


def _classificar_status(status: str) -> str:
    s = str(status).lower().strip()
    if s in _STATUS_VENDIDA:    return "vendida"
    if s in _STATUS_DISPONIVEL: return "disponivel"
    if s in _STATUS_PERMUTA:    return "permuta"
    if s in _STATUS_TERCEIROS:  return "terceiros"
    # Fallbacks parciais
    if "vendida" in s or "vendido" in s: 
        if "terceiro" in s: return "terceiros"
        return "vendida"
    if "dispon" in s: return "disponivel"
    if "permuta" in s: return "permuta"
    return "outro"


def _incluir_tipo(tipo: str) -> bool:
    """Retorna True se o tipo deve ser contado como unidade habitacional."""
    t = str(tipo).lower().strip()
    if not t or t == "nan": return True # Fallback se não tiver tipo
    if t in _TIPOS_EXCLUIR:  return False
    if t in _TIPOS_INCLUIR:  return True
    # Fallback: incluir se não for explicitamente excluído
    for excl in _TIPOS_EXCLUIR:
        if excl in t: return False
    return True


def parse_unidades_sienge(data: bytes, arquivo_nome: str = "") -> dict:
    """
    Parseia relatório 'Unidades por Empreendimento' do SIENGE.
    """
    try:
        df = pd.read_excel(io.BytesIO(data), header=None)

        # ── Localizar linha de cabeçalho ──────────────────────────────
        header_row = None
        for i in range(min(20, len(df))):
            cell = str(df.iloc[i, 0]).strip().lower()
            if cell == "unidade" or cell == "código" or cell == "codigo":
                header_row = i
                break

        if header_row is None:
            # Busca secundária por qualquer linha que contenha 'Tipo' e 'Status' ou 'Estoque'
            for i in range(min(20, len(df))):
                row_str = " ".join(str(v).lower() for v in df.iloc[i])
                if "tipo" in row_str and ("status" in row_str or "estoque" in row_str):
                    header_row = i
                    break

        if header_row is None:
            return {"erro": "Cabeçalho 'Unidade' ou 'Tipo/Estoque' não encontrado no arquivo."}

        # ── Identificar colunas por cabeçalho ─────────────────────────
        header = df.iloc[header_row]
        col_tipo   = None
        col_status = None
        col_valor  = None

        for j, h in enumerate(header):
            h_lower = str(h).lower().strip()
            if h_lower == "tipo":
                col_tipo = j
            elif "estoque" in h_lower or "status" in h_lower or h_lower == "situação":
                col_status = j
            elif "valor atual" in h_lower or h_lower == "valor" or h_lower == "vgv":
                col_valor = j

        # Fallbacks por posição conhecida do arquivo real se falhar a detecção
        if col_tipo   is None: col_tipo   = 1
        if col_status is None: col_status = 22 if len(header) > 22 else (len(header)-1)
        if col_valor  is None: col_valor  = 15 if len(header) > 15 else (len(header)-2)

        # ── Processar linhas ──────────────────────────────────────────
        total = vendidas = disponiveis = permuta = terceiros = 0
        vgv_vendido = 0.0
        unidades_permuta = []

        for i in range(header_row + 1, len(df)):
            row    = df.iloc[i]
            nome   = str(row.iloc[0]).strip()
            tipo   = str(row.iloc[col_tipo]).strip()   if col_tipo < len(row)   else ""
            status = str(row.iloc[col_status]).strip() if col_status < len(row) else ""
            valor  = 0.0
            
            try:
                # Trata strings "1.234,56" ou floats
                v_raw = row.iloc[col_valor]
                if pd.isna(v_raw):
                    valor = 0.0
                elif isinstance(v_raw, (int, float)):
                    valor = float(v_raw)
                else:
                    valor = float(str(v_raw).replace(".", "").replace(",", ".").replace("R$", "").strip())
            except (ValueError, TypeError, IndexError):
                valor = 0.0

            # Ignorar linhas vazias e linhas de totais/rodapé
            if not nome or nome.lower() in ("nan", "none", ""):
                continue
            if nome.lower().startswith("unidades ") or nome.lower() == "total" or "total " in nome.lower():
                continue  # linha de resumo no rodapé

            # Filtrar por tipo — garagens ficam de fora
            if not _incluir_tipo(tipo):
                continue

            total += 1
            st = _classificar_status(status)

            if st == "vendida":
                vendidas += 1
                # Ignorar valores claramente corrompidos (> R$ 10 bilhões)
                if valor < 10_000_000_000:
                    vgv_vendido += valor
            elif st == "disponivel":
                disponiveis += 1
            elif st == "permuta":
                permuta += 1
                unidades_permuta.append(nome)
            elif st == "terceiros":
                terceiros += 1
                # No dashboard, terceiros costuma ser tratado como vendida
                vendidas += 1
                if valor < 10_000_000_000:
                    vgv_vendido += valor

        preco_medio = (vgv_vendido / vendidas) if vendidas > 0 else 0.0

        return {
            "total_unidades":   total,
            "vendidas":         vendidas,
            "disponiveis":      disponiveis,
            "permuta":          permuta,
            "terceiros":        terceiros,
            "vgv_vendido":      vgv_vendido,
            "preco_medio":      preco_medio,
            "unidades_permuta": sorted(unidades_permuta),
            "arquivo_nome":     arquivo_nome,
            "data_upload":      datetime.now().isoformat(),
        }

    except Exception as e:
        return {"erro": f"Erro ao processar arquivo: {str(e)}"}
