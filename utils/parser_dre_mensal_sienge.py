"""
parser_dre_mensal_sienge.py
Parseia DRE mensal exportada do SIENGE (um mês por arquivo).

Compatível com:
- DRE da Matriz (valor na coluna 6, colunas intermediárias vazias)
- DRE das SPEs (valor na coluna 2, estrutura compacta)
A coluna de valor é detectada dinamicamente.
"""
import pandas as pd
import io
import re
from datetime import datetime

_MESES_PT = [
    'janeiro', 'fevereiro', 'março', 'marco', 'abril', 'maio', 'junho',
    'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'
]

# Campos que devem ser negativos (custos/despesas)
_NEGATIVOS = {"imp_rec", "cpv", "desp_op", "desp_bdi", "ir"}

# Mapeamento: código SIENGE nível 1 → campo interno
# ATENÇÃO: 14 = Lucro Líquido (NÃO é IR). IR é o código 13.
_MAP_NIVEL1 = {
    "01": "rec_bruta",
    "02": "imp_rec",
    "04": "cpv",
    "06": "desp_op",   # sem BDI — BDI extraído separadamente via 06.03
    "11": "res_fin",
    "13": "ir",        # IR/CSLL — pode não aparecer se for zero
    # 03=Rec.Liq, 05=Lucro Bruto, 07=EBITDA, 12=Lucro Antes IR, 14=Lucro Liq
    # são derivados — não mapeados para evitar confusão
}


def _detectar_periodo(df) -> tuple:
    """Extrai (ano, mes) do cabeçalho. Busca padrão DD/MM/AAAA."""
    for i in range(min(10, len(df))):
        for val in df.iloc[i]:
            m = re.search(r'(\d{2})/(\d{2})/(\d{4})', str(val))
            if m:
                return int(m.group(3)), int(m.group(2))
    return None, None


def _detectar_col_valor(df, header_row: int) -> int:
    """
    Detecta dinamicamente a coluna que contém os values numéricos.
    Busca a coluna do cabeçalho que contém nome de mês (ex: 'Janeiro/2026').
    Fallback: coluna 2.
    """
    for col in range(len(df.columns)):
        cell = str(df.iloc[header_row, col]).lower().strip()
        if any(mes in cell for mes in _MESES_PT):
            return col
    return 2  # fallback


def parse_dre_mensal_sienge(data: bytes, arquivo_nome: str = "") -> dict:
    """
    Parseia DRE mensal do SIENGE — um mês por arquivo.

    Retorna dict com os valores do mês e metadados, ou {"erro": "..."}.

    Campos retornados:
        ano, mes, aaaa_mm          — identificação do período
        rec_bruta                  — Receita Bruta (positivo)
        imp_rec                    — Impostos s/ Receita (negativo)
        cpv                        — Custo dos Imóveis Vendidos (negativo)
        desp_op                    — Despesas Operacionais SEM BDI (negativo)
        desp_bdi                   — Despesas com BDI / código 06.03 (negativo)
        res_fin                    — Resultado Financeiro (pode ser + ou -)
        ir                         — IR/CSLL / código 13 (negativo ou 0)
        arquivo_nome, data_upload  — metadados
    """
    try:
        df = pd.read_excel(io.BytesIO(data), header=None)

        # ── 1. Período ────────────────────────────────────────────────────
        ano, mes = _detectar_periodo(df)
        if ano is None:
            return {"erro": "Período não encontrado. Verifique se o arquivo é uma DRE mensal do SIENGE."}

        # ── 2. Cabeçalho ─────────────────────────────────────────────────
        header_row = None
        for i in range(len(df)):
            if str(df.iloc[i, 0]).strip().lower() in ("código", "codigo"):
                header_row = i
                break
        if header_row is None:
            return {"erro": "Cabeçalho 'Código' não encontrado. Verifique o formato do arquivo."}

        # ── 3. Coluna de valor (dinâmica) ─────────────────────────────────
        col_valor = _detectar_col_valor(df, header_row)

        # ── 4. Processar linhas ───────────────────────────────────────────
        resultado = {k: 0.0 for k in ["rec_bruta", "imp_rec", "cpv", "desp_op", "desp_bdi", "res_fin", "ir"]}

        for i in range(header_row + 1, len(df)):
            row = df.iloc[i]
            cod   = str(row.iloc[0]).strip()
            conta = str(row.iloc[1]).strip() if df.shape[1] > 1 else ""

            if not cod or cod == "nan" or not conta or conta == "nan":
                continue

            try:
                val = float(
                    str(row.iloc[col_valor])
                    .replace(",", ".")
                    .replace("nan", "0")
                )
            except (ValueError, IndexError):
                val = 0.0

            if val == 0.0:
                continue

            cod_norm = cod.strip()

            # BDI: sub-código 06.03 — extrair separadamente antes do totalizador 06
            if cod_norm.startswith("06.03"):
                resultado["desp_bdi"] = val
                continue

            # Só processar totalizadores de nível 1 (sem ponto no código)
            if "." not in cod_norm and cod_norm in _MAP_NIVEL1:
                resultado[_MAP_NIVEL1[cod_norm]] = val

        # ── 5. Garantir sinais corretos ───────────────────────────────────
        for campo in _NEGATIVOS:
            if resultado[campo] > 0:
                resultado[campo] = -resultado[campo]

        # ── 6. Isolar desp_op: remover BDI do totalizador 06 ─────────────
        # O código 06 (totalizador) já inclui o BDI (06.03).
        # Para ter desp_op sem BDI, subtraímos.
        if resultado["desp_bdi"] != 0.0 and resultado["desp_op"] != 0.0:
            resultado["desp_op"] = resultado["desp_op"] - resultado["desp_bdi"]

        return {
            "ano":          ano,
            "mes":          mes,
            "aaaa_mm":      f"{ano:04d}-{mes:02d}",
            "rec_bruta":    resultado["rec_bruta"],
            "imp_rec":      resultado["imp_rec"],
            "cpv":          resultado["cpv"],
            "desp_op":      resultado["desp_op"],
            "desp_bdi":     resultado["desp_bdi"],
            "res_fin":      resultado["res_fin"],
            "ir":           resultado["ir"],
            "arquivo_nome": arquivo_nome,
            "data_upload":  datetime.now().isoformat(),
        }

    except Exception as e:
        return {"erro": f"Erro ao processar arquivo: {str(e)}"}
