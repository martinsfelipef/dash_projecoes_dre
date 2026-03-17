"""
Parser do Cronograma Físico-Financeiro exportado do SIENGE.

Estrutura esperada do Excel:
- Linha de cabeçalho com meses (ex: "Jan/2026", "Fev/2026", ...)
- Coluna 0: código da conta
- Coluna 1: nome da conta
- Colunas seguintes: valores por mês

Retorna dict com:
  - "custos_por_mes": list de floats, um por mês (soma de todas as contas)
  - "data_inicio": {"ano": int, "mes": int}  ← primeiro mês do arquivo
  - "data_fim":    {"ano": int, "mes": int}  ← último mês do arquivo
  - "n_meses": int
  - "contas": list de dicts {codigo, nome, valores: [float * n_meses]}
  - "desvios": list de dicts {conta, comentario}  ← células com comentário
  - "erro": str  ← só presente se falhar
"""

import pandas as pd
import io
import re

# Mapeamento de abreviações de meses em português
_MESES_PT = {
    'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4,
    'mai': 5, 'jun': 6, 'jul': 7, 'ago': 8,
    'set': 9, 'out': 10, 'nov': 11, 'dez': 12
}

def _parse_mes_ano(texto):
    """
    Tenta extrair (mes: int, ano: int) de strings como:
    'Jan/2026', 'jan/26', 'Janeiro 2026', '01/2026', etc.
    Retorna (None, None) se não conseguir.
    """
    texto = str(texto).strip().lower()
    # Tenta padrão "jan/2026" ou "jan/26"
    m = re.match(r'([a-záàâãéêíóôõúç]+)[/\s\-](\d{2,4})', texto)
    if m:
        nome_mes = m.group(1)[:3]
        ano_str  = m.group(2)
        mes = _MESES_PT.get(nome_mes)
        if mes:
            ano = int(ano_str)
            if ano < 100:
                ano += 2000
            return mes, ano
    # Tenta padrão numérico "01/2026"
    m2 = re.match(r'(\d{1,2})[/\-](\d{4})', texto)
    if m2:
        mes = int(m2.group(1))
        ano = int(m2.group(2))
        if 1 <= mes <= 12:
            return mes, ano
    return None, None


def parse_cronograma_sienge(data: bytes) -> dict:
    """
    Recebe bytes de um arquivo Excel e retorna o dicionário com os dados.
    """
    try:
        if isinstance(data, bytes):
            data = io.BytesIO(data)
        df = pd.read_excel(data, header=None)
    except Exception as e:
        return {"erro": f"Não foi possível ler o arquivo Excel: {e}"}

    # --- Encontra a linha de cabeçalho (contém meses) ---
    header_row = None
    col_map = {}  # {indice_coluna: (mes, ano)}

    for i, row in df.iterrows():
        found = {}
        for j, cell in enumerate(row):
            mes, ano = _parse_mes_ano(cell)
            if mes and ano:
                found[j] = (mes, ano)
        if len(found) >= 2:  # linha com pelo menos 2 meses = cabeçalho
            header_row = i
            col_map = found
            break

    if header_row is None:
        return {"erro": "Não foi possível encontrar o cabeçalho com meses no arquivo. "
                        "Verifique se o Excel contém colunas com meses no formato 'Jan/2026'."}

    if not col_map:
        return {"erro": "Nenhuma coluna de mês reconhecida no cabeçalho."}

    # Ordena as colunas pelo mês/ano
    colunas_ordenadas = sorted(col_map.keys(), key=lambda c: (col_map[c][1], col_map[c][0]))
    meses_lista = [col_map[c] for c in colunas_ordenadas]  # [(mes, ano), ...]
    n_meses = len(meses_lista)

    data_inicio = {"mes": meses_lista[0][0],  "ano": meses_lista[0][1]}
    data_fim    = {"mes": meses_lista[-1][0], "ano": meses_lista[-1][1]}

    # --- Lê as linhas de dados (abaixo do cabeçalho) ---
    contas = []
    custos_por_mes = [0.0] * n_meses
    desvios = []

    for i in range(header_row + 1, len(df)):
        row = df.iloc[i]
        codigo = str(row.iloc[0]).strip() if len(row) > 0 else ""
        nome   = str(row.iloc[1]).strip() if len(row) > 1 else ""

        # Ignora linhas sem nome ou com "nan"
        if not nome or nome.lower() == "nan" or not codigo or codigo.lower() == "nan":
            continue

        # Tenta converter código para número (filtra linhas de cabeçalho repetidas)
        try:
            float(codigo.replace(",", "."))
        except ValueError:
            continue

        valores = []
        for j, col_idx in enumerate(colunas_ordenadas):
            try:
                val = float(str(row.iloc[col_idx]).replace(",", ".").replace("nan", "0"))
            except (ValueError, IndexError):
                val = 0.0
            valores.append(val)
            custos_por_mes[j] += val

        contas.append({"codigo": codigo, "nome": nome, "valores": valores})

    if not contas:
        return {"erro": "Nenhuma conta de custo encontrada no arquivo. "
                        "Verifique se o arquivo tem dados abaixo do cabeçalho de meses."}

    # Nota: leitura de comentários do Excel requereria openpyxl avançado.
    # Por ora, desvios retorna vazio. Será implementado em fase futura.
    desvios = []

    return {
        "custos_por_mes": custos_por_mes,
        "data_inicio":    data_inicio,
        "data_fim":       data_fim,
        "n_meses":        n_meses,
        "contas":         contas,
        "desvios":        desvios,
    }
