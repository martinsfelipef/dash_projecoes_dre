"""
parser_dre_mensal_sienge.py
Parseia DRE mensal exportada do SIENGE (um mês por arquivo).
Retorna dict com os valores do mês e metadados.
"""
import pandas as pd
import io
import re
from datetime import datetime

# Mapeamento de códigos para campos internos
# Chave: prefixo do código SIENGE | Valor: campo no dict de retorno
_MAP_CODIGO = {
    "01 ":  "rec_bruta",   # (=) RECEITA BRUTA
    "01.02": "rec_bdi",     # Receita de BDI (para Matriz)
    "01.03": "rec_bdi",     # Alternativa para Receita de BDI
    "02 ":  "imp_rec",     # (-) IMPOSTOS E DEDUÇÕES
    "04 ":  "cpv",         # (-) CUSTO DOS IMÓVEIS VENDIDOS
    "06 ":  "desp_op",     # (-) DESPESAS OPERACIONAIS (sem BDI)
    "03 ":  "desp_op",     # Alternativa para DESPESAS OPERACIONAIS
    "3 ":   "desp_op",     # Alternativa para DESPESAS OPERACIONAIS
    "06.03": "desp_bdi",   # Despesas com BDI (extraído separadamente)
    "11 ":  "res_fin",     # (+/-) RECEITAS E DESPESAS FINANCEIRAS
    "14 ":  "ir",          # será zero se não houver IR — código 13 ou 14
}

# Campos que devem ser negativos (custos/despesas)
_NEGATIVOS = {"imp_rec", "cpv", "desp_op", "desp_bdi", "ir"}


def _extrair_periodo(df) -> tuple:
    """
    Extrai ano e mês do cabeçalho do arquivo.
    Busca linha com "Período" e lê a data no formato "01/MM/AAAA a DD/MM/AAAA".
    Retorna (ano: int, mes: int) ou (None, None) se não encontrar.
    """
    for i in range(min(10, len(df))):
        row_vals = [str(v).strip() for v in df.iloc[i]]
        for val in row_vals:
            # Busca padrão "01/MM/AAAA a DD/MM/AAAA"
            m = re.search(r'(\d{2})/(\d{2})/(\d{4})', val)
            if m:
                return int(m.group(3)), int(m.group(2))
    return None, None


def parse_dre_mensal_sienge(data: bytes, arquivo_nome: str = "") -> dict:
    """
    Parseia DRE mensal do SIENGE.

    Retorna:
    {
        "ano":      int,        # ex: 2026
        "mes":      int,        # ex: 1 (janeiro)
        "aaaa_mm":  str,        # ex: "2026-01"
        "rec_bruta": float,
        "imp_rec":   float,     # negativo
        "cpv":       float,     # negativo
        "desp_op":   float,     # negativo (SEM BDI)
        "desp_bdi":  float,     # negativo — extraído de 06.03
        "res_fin":   float,
        "ir":        float,     # negativo ou zero
        "arquivo_nome": str,
        "data_upload":  str,    # ISO datetime
    }
    Ou {"erro": "mensagem"} em caso de falha.
    """
    try:
        df = pd.read_excel(io.BytesIO(data), header=None)

        # ── Extrair ano/mês do cabeçalho ─────────────────────────────
        ano, mes = _extrair_periodo(df)
        if ano is None:
            return {"erro": "Período não encontrado no arquivo. Verifique o formato."}

        # ── Localizar linha de cabeçalho de colunas ──────────────────
        header_row = None
        for i in range(len(df)):
            cell = str(df.iloc[i, 0]).strip().lower()
            if cell in ("código", "codigo"):
                header_row = i
                break

        if header_row is None:
            return {"erro": "Cabeçalho 'Código' não encontrado no arquivo."}

        # ── Identificar coluna de valor (3ª coluna = índice 2) ───────
        # O arquivo tem: Col0=Código, Col1=Conta, Col2=Valor do mês, Col3=Total
        col_valor = 2

        # ── Inicializar resultado ─────────────────────────────────────
        resultado = {
            "rec_bruta": 0.0,
            "rec_bdi":   0.0,
            "imp_rec":   0.0,
            "cpv":       0.0,
            "desp_op":   0.0,
            "desp_bdi":  0.0,
            "res_fin":   0.0,
            "ir":        0.0,
        }

        # ── Processar linhas da DRE ───────────────────────────────────
        for i in range(header_row + 1, len(df)):
            row = df.iloc[i]
            cod   = str(row.iloc[0]).strip()
            conta = str(row.iloc[1]).strip() if len(row) > 1 else ""

            if not cod or cod == "nan" or not conta or conta == "nan":
                continue

            # Extrair valor
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

            # ── Mapear código para campo ──────────────────────────────
            cod_norm = cod.strip()
            
            # Prioridade 1: Códigos específicos (BDI)
            if cod_norm.startswith("06.03"):
                resultado["desp_bdi"] = val
                continue
            if cod_norm.startswith("01.02") or cod_norm.startswith("01.03"):
                resultado["rec_bdi"] = val
                continue

            # Prioridade 2: Totalizadores de nível 0
            nivel = cod_norm.count(".")
            if nivel == 0:
                # Mapear totalizadores nível 0 (01, 02, 04, 06, 11, 14)
                for prefixo, campo in _MAP_CODIGO.items():
                    if prefixo.strip() == cod_norm:
                        resultado[campo] = val
                        break

        # ── Garantir sinais corretos ──────────────────────────────────
        # Custos e despesas devem ser negativos
        for campo in _NEGATIVOS:
            if resultado[campo] > 0:
                resultado[campo] = -resultado[campo]

        # ── desp_op não deve incluir BDI ─────────────────────────────
        # Se desp_op foi lido do totalizador 06 (que inclui BDI),
        # subtrair o BDI para não duplicar
        if resultado["desp_bdi"] != 0.0 and resultado["desp_op"] != 0.0:
            # desp_op do totalizador 06 already includes desp_bdi
            # Subtract to isolate operational expenses without BDI
            resultado["desp_op"] = resultado["desp_op"] - resultado["desp_bdi"]

        aaaa_mm = f"{ano:04d}-{mes:02d}"

        return {
            "ano":          ano,
            "mes":          mes,
            "aaaa_mm":      aaaa_mm,
            "rec_bruta":    resultado["rec_bruta"],
            "rec_bdi":      resultado["rec_bdi"],
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
