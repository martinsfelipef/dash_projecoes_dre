"""
Parser para cronograma físico-financeiro do SIENGE.

Retorna dict com chaves:
  - cpv_real:  lista de 12 floats (custo mensal realizado)
  - dop_real:  lista de 12 floats (despesas operacionais mensais)
  - rf_real:   lista de 12 floats (resultado financeiro mensal)
  - ir_real:   lista de 12 floats (IR/CSLL mensal)

NOTA: Este parser ainda é básico. Quando Felipe fornecer um arquivo
modelo real do SIENGE, a lógica de extração será refinada.
"""

import pandas as pd
import io


_MESES_ABREV = {
    'jan': 0, 'fev': 1, 'mar': 2, 'abr': 3, 'mai': 4, 'jun': 5,
    'jul': 6, 'ago': 7, 'set': 8, 'out': 9, 'nov': 10, 'dez': 11,
}


def parse_cronograma_sienge(data) -> dict:
    """
    Recebe bytes de um arquivo Excel (.xlsx/.xls) exportado do SIENGE
    e retorna um dicionário com os custos reais mensais.

    Parâmetros
    ----------
    data : bytes ou file-like
        Conteúdo do arquivo Excel.

    Retorna
    -------
    dict com chaves: cpv_real, dop_real, rf_real, ir_real
        Cada valor é uma lista de 12 floats (Jan–Dez).
        Em caso de erro, retorna {"erro": "mensagem"}.
    """
    try:
        if isinstance(data, bytes):
            data = io.BytesIO(data)

        df = pd.read_excel(data, header=0)
        df.columns = [str(c).strip().lower() for c in df.columns]

        resultado = {
            'cpv_real': [0.0] * 12,
            'dop_real': [0.0] * 12,
            'rf_real':  [0.0] * 12,
            'ir_real':  [0.0] * 12,
        }

        # Tenta mapear colunas com nomes de meses para CPV
        for col in df.columns:
            for abrev, idx in _MESES_ABREV.items():
                if abrev in col:
                    for row in df.itertuples():
                        try:
                            v = float(getattr(row, col.replace(' ', '_'), 0) or 0)
                        except (ValueError, TypeError):
                            v = 0.0
                        resultado['cpv_real'][idx] += v

        return resultado

    except Exception as e:
        return {"erro": f"Erro ao processar cronograma SIENGE: {e}"}
