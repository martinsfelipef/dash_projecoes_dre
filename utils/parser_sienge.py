# utils/parser_sienge.py
# Lê exports do SIENGE e retorna estrutura padronizada para o dashboard

import pandas as pd
import numpy as np
from io import BytesIO

MESES_MAP = {
    "Janeiro":   0, "Fevereiro": 1, "Março":    2, "Abril":   3,
    "Maio":      4, "Junho":     5, "Julho":    6, "Agosto":  7,
    "Setembro":  8, "Outubro":   9, "Novembro":10, "Dezembro":11
}

# Contas que nos interessam (código → chave interna)
CONTAS_ALVO = {
    "01":  "rec_bruta",
    "02":  "imp_rec",
    "03":  "rec_liq",
    "04":  "cpv",
    "05":  "lucro_bruto",
    "06":  "desp_op",
    "07":  "ebitda",
    "11":  "res_fin",
    "12":  "lucro_antes_ir",
    "13":  "ir",
    "14":  "lucro_liq",
}

# Sub-contas exibidas separadamente na DRE
CONTAS_DETALHE = {
    "01.04": "rec_bdi",
    "06.03": "desp_bdi",
}

def _limpar_valor(v):
    """Converte células do Excel em float."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 0.0
    try:
        return float(str(v).replace(",","").replace(" ",""))
    except:
        return 0.0

def _encontrar_linha_header(df):
    """Encontra a linha onde está o cabeçalho com 'Código'."""
    for i, row in df.iterrows():
        vals = [str(v).strip() for v in row.values if pd.notna(v)]
        if "Código" in vals or "Codigo" in vals:
            return i
    return None

def _extrair_nome_empresa(df):
    """Extrai o nome da empresa do cabeçalho do SIENGE."""
    for i, row in df.iterrows():
        for v in row.values:
            s = str(v).strip()
            if "LTDA" in s.upper() or "S.A" in s.upper() or "SPE" in s.upper():
                # Remove o prefixo numérico se houver (ex: "23 - BROCKS...")
                partes = s.split(" - ", 1)
                return partes[1].strip() if len(partes) > 1 else s
    return "Empresa não identificada"

def _extrair_periodo(df):
    """Extrai o período do relatório."""
    for i, row in df.iterrows():
        for v in row.values:
            s = str(v).strip()
            if "a" in s and "/" in s and len(s) < 30:
                return s
    return ""

def _mapear_colunas_meses(header_row):
    """Mapeia colunas para índices de meses (0-11)."""
    col_mes = {}
    col_total = None
    for col_idx, val in enumerate(header_row):
        s = str(val).strip()
        for nome_mes, idx_mes in MESES_MAP.items():
            if nome_mes in s:
                col_mes[col_idx] = idx_mes
                break
        if "Total" in s or "TOTAL" in s:
            col_total = col_idx
    return col_mes, col_total

def parse_sienge(arquivo_bytes):
    """
    Lê um arquivo Excel exportado do SIENGE.
    Retorna dict com:
      - nome: str
      - periodo: str
      - dados: dict {chave_interna: list[12 floats]}
      - avisos: list[str]
      - raw_df: DataFrame para preview
    """
    avisos = []

    try:
        df_raw = pd.read_excel(BytesIO(arquivo_bytes), header=None, engine="openpyxl")
    except Exception as e:
        return {"erro": f"Não foi possível ler o arquivo: {e}"}

    nome_empresa = _extrair_nome_empresa(df_raw)
    periodo      = _extrair_periodo(df_raw)

    # Inicializa valores zerados para 12 meses
    dados = {k: [0.0]*12 for k in list(CONTAS_ALVO.values()) + list(CONTAS_DETALHE.values())}

    # Encontra todas as seções de header (pode haver 2 no SIENGE: Jan-Set e Out-Dez)
    headers_encontrados = []
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row.values if pd.notna(v)]
        if "Código" in vals:
            headers_encontrados.append(i)

    if not headers_encontrados:
        avisos.append("⚠️ Estrutura do arquivo não reconhecida. Verifique se é um export SIENGE.")
        return {"nome": nome_empresa, "periodo": periodo, "dados": dados,
                "avisos": avisos, "raw_df": df_raw.head(20)}

    # Processa cada seção
    for header_idx in headers_encontrados:
        header_row = df_raw.iloc[header_idx].tolist()
        col_mes, col_total = _mapear_colunas_meses(header_row)

        if not col_mes:
            continue

        # Lê as linhas de dados após o header
        for row_idx in range(header_idx + 1, len(df_raw)):
            row = df_raw.iloc[row_idx]
            codigo = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""

            # Para quando encontrar linha vazia ou próximo header
            if not codigo or codigo == "nan":
                # Verifica se é linha completamente vazia
                if row.isna().all():
                    break
                continue

            # Verifica se é sub-conta de detalhe (ex: 01.04, 06.03)
            if codigo in CONTAS_DETALHE:
                chave = CONTAS_DETALHE[codigo]
                for col_idx, mes_idx in col_mes.items():
                    if col_idx < len(row):
                        val = _limpar_valor(row.iloc[col_idx])
                        dados[chave][mes_idx] += val
                continue

            # Só processa códigos de 2 dígitos (contas principais, não subcategorias)
            if codigo not in CONTAS_ALVO:
                continue

            chave = CONTAS_ALVO[codigo]

            for col_idx, mes_idx in col_mes.items():
                if col_idx < len(row):
                    val = _limpar_valor(row.iloc[col_idx])
                    dados[chave][mes_idx] += val

    # Validações automáticas
    rb = sum(dados["rec_bruta"])
    ll = sum(dados["lucro_liq"])

    if rb == 0:
        avisos.append("⚠️ Receita Bruta zerada — verifique se o arquivo contém dados de receita.")

    # Verifica consistência: Lucro Bruto deve = Rec Líquida + CPV
    for i in range(12):
        lb_calc = dados["rec_liq"][i] + dados["cpv"][i]
        lb_dado = dados["lucro_bruto"][i]
        if abs(lb_calc - lb_dado) > 1.0:  # tolerância R$ 1
            avisos.append(f"⚠️ Inconsistência no Lucro Bruto em {list(MESES_MAP.keys())[i]}: "
                         f"calculado R$ {lb_calc:,.2f} vs arquivo R$ {lb_dado:,.2f}")

    # Monta DataFrame de preview
    MESES_LABELS = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    NOMES_LINHAS = {
        "rec_bruta":     "(=) Receita Bruta",
        "rec_bdi":       "   ↳ Receita de BDI",
        "imp_rec":       "(-) Impostos s/ Receita",
        "rec_liq":       "(=) Receita Líquida",
        "cpv":           "(-) CPV / CSP",
        "lucro_bruto":   "(=) Lucro Bruto",
        "desp_op":       "(-) Despesas Operacionais",
        "desp_bdi":      "   ↳ Despesa de BDI",
        "ebitda":        "(=) EBITDA",
        "res_fin":       "(+/-) Resultado Financeiro",
        "lucro_antes_ir":"(=) Lucro antes IR",
        "ir":            "(-) IR / CSLL",
        "lucro_liq":     "(=) Lucro Líquido",
    }
    rows = []
    for chave, label in NOMES_LINHAS.items():
        row = {"Conta": label}
        for i, m in enumerate(MESES_LABELS):
            row[m] = dados[chave][i]
        row["Total"] = sum(dados[chave])
        rows.append(row)
    preview_df = pd.DataFrame(rows).set_index("Conta")

    return {
        "nome":     nome_empresa,
        "periodo":  periodo,
        "dados":    dados,
        "avisos":   avisos,
        "preview":  preview_df,
    }
