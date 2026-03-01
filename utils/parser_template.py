# utils/parser_template.py
# Lê o Template DRE Align (gerado pelo sistema) e retorna estrutura padronizada

import pandas as pd
import numpy as np
from io import BytesIO

CODIGOS_MAP = {
    "01":  "rec_bruta",
    "02":  "imp_rec",
    "04":  "cpv",
    "06":  "desp_op",
    "11":  "res_fin",
    "13":  "ir",
}

def parse_template_align(arquivo_bytes):
    """Lê o Template DRE Align e retorna estrutura padronizada."""
    avisos = []
    try:
        xl = pd.ExcelFile(BytesIO(arquivo_bytes), engine="openpyxl")
        if "DRE_2025" not in xl.sheet_names:
            return {"erro": "Aba 'DRE_2025' não encontrada. Use o Template Align."}
        df = pd.read_excel(BytesIO(arquivo_bytes), sheet_name="DRE_2025",
                           header=None, engine="openpyxl")
    except Exception as e:
        return {"erro": f"Erro ao ler arquivo: {e}"}

    # Nome da empresa na célula A1
    nome = str(df.iloc[0, 0]).strip()
    if "NOME DA EMPRESA" in nome.upper() or nome == "nan":
        nome = "Empresa sem nome"
        avisos.append("⚠️ Nome da empresa não preenchido na célula A1.")

    dados = {k: [0.0]*12 for k in set(CODIGOS_MAP.values())}
    dados["rec_liq"]        = [0.0]*12
    dados["lucro_bruto"]    = [0.0]*12
    dados["ebitda"]         = [0.0]*12
    dados["lucro_antes_ir"] = [0.0]*12
    dados["lucro_liq"]      = [0.0]*12

    # Cabeçalho na linha 3 (índice 2), dados a partir da linha 4 (índice 3)
    for row_idx in range(3, len(df)):
        row = df.iloc[row_idx]
        codigo = str(row.iloc[0]).strip()
        if codigo == "nan" or "." in codigo:
            continue  # pula subcategorias e linhas vazias
        if codigo not in CODIGOS_MAP:
            continue

        chave = CODIGOS_MAP[codigo]
        for mes_idx in range(12):
            col_idx = mes_idx + 2  # colunas C a N = índices 2 a 13
            try:
                val = float(row.iloc[col_idx]) if pd.notna(row.iloc[col_idx]) else 0.0
                dados[chave][mes_idx] += val
            except:
                pass

    # Recalcula linhas derivadas
    for i in range(12):
        dados["rec_liq"][i]        = dados["rec_bruta"][i] + dados["imp_rec"][i]
        dados["lucro_bruto"][i]    = dados["rec_liq"][i]   + dados["cpv"][i]
        dados["ebitda"][i]         = dados["lucro_bruto"][i] + dados["desp_op"][i]
        dados["lucro_antes_ir"][i] = dados["ebitda"][i]    + dados["res_fin"][i]
        dados["lucro_liq"][i]      = dados["lucro_antes_ir"][i] + dados["ir"][i]

    # Validações
    if sum(dados["rec_bruta"]) == 0:
        avisos.append("⚠️ Receita Bruta zerada — verifique se preencheu os valores.")

    # Preview
    MESES_L = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
    NOMES = {
        "rec_bruta":"(=) Receita Bruta","imp_rec":"(-) Impostos",
        "rec_liq":"(=) Receita Líquida","cpv":"(-) CPV/CMV/CSP",
        "lucro_bruto":"(=) Lucro Bruto","desp_op":"(-) Despesas Op.",
        "ebitda":"(=) EBITDA","res_fin":"(+/-) Res. Financeiro",
        "lucro_antes_ir":"(=) Lucro antes IR","ir":"(-) IR/CSLL",
        "lucro_liq":"(=) Lucro Líquido"
    }
    rows = []
    for chave, label in NOMES.items():
        row = {"Conta": label}
        for i, m in enumerate(MESES_L):
            row[m] = dados[chave][i]
        row["Total"] = sum(dados[chave])
        rows.append(row)

    return {
        "nome":    nome,
        "periodo": "Jan-Dez 2025",
        "dados":   dados,
        "avisos":  avisos,
        "preview": pd.DataFrame(rows).set_index("Conta"),
        "fonte":   "Template Align"
    }
