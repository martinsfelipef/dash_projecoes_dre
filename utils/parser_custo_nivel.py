"""
Parser do Custo por Nível — SIENGE
Arquivo: emissao_custo_por_nivel-DD-MM-AAAA_-_NomeObra.xlsx

Suporta dois formatos:
- Formato A: total na última linha com valor numérico > 1M na col 0
- Formato B: total na última linha com col 0 = nan e col 3 > 1M (formato formatado da Brocks)
"""
import pandas as pd
import io
import re
from datetime import datetime

def parse_custo_nivel(data: bytes, arquivo_nome: str = "") -> dict:
    """
    Parseia relatório 'Custo por Nível' exportado do SIENGE.
    """
    try:
        df = pd.read_excel(io.BytesIO(data), header=None)

        # ── Detectar linha de total ───────────────────────────────────────
        linha_total = None
        _col_orc = 3   # col orçado (padrão formato B)
        _col_med = 4   # col medido
        _col_rea = 5   # col realizado
        _col_cmp = 10  # col comprometido
        _col_vdi = 12  # col verba disponível
        _col_sld = 13  # col saldo CTP

        # Formato B: última linha onde col 0 é nan/texto E col 3 é > 1M
        for i in range(len(df) - 1, -1, -1):
            row = df.iloc[i]
            col0 = str(row.iloc[0]).strip()
            try:
                # Trata string "1.234,56"
                v3_raw = str(row.iloc[3]).replace('.', '').replace(',', '.')
                val3 = float(v3_raw)
                if val3 > 1_000_000 and (col0 == 'nan' or col0 == '' or not col0[0].isdigit()):
                    linha_total = i
                    break
            except (ValueError, TypeError, IndexError):
                pass

        # Formato A (fallback): última linha com valor > 1M na col 0
        if linha_total is None:
            for i in range(len(df) - 1, -1, -1):
                try:
                    v0_raw = str(df.iloc[i, 0]).replace('.', '').replace(',', '.')
                    val0 = float(v0_raw)
                    if val0 > 1_000_000:
                        linha_total = i
                        _col_orc = 3 # Mesmo no formato A, orçado costuma estar na 3 se exportado via Excel
                        break
                except (ValueError, TypeError):
                    pass

        if linha_total is None:
            return {"erro": "Não foi possível encontrar o 'Total da obra' no arquivo. "
                            "Verifique se o arquivo é um Custo por Nível completo "
                            "(com CUSTOS DIRETOS + INDIRETOS)."}

        row_total = df.iloc[linha_total]

        def _float(val):
            try:
                if pd.isna(val): return 0.0
                if isinstance(val, (int, float)): return float(val)
                # Trata string "1.234,56"
                s = str(val).replace('.', '').replace(',', '.')
                return float(s)
            except (ValueError, TypeError):
                return 0.0

        orcado_total     = _float(row_total.iloc[_col_orc])
        medido_acum      = _float(row_total.iloc[_col_med])
        realizado_acum   = _float(row_total.iloc[_col_rea])
        comprometido     = _float(row_total.iloc[_col_cmp]) if df.shape[1] > _col_cmp else 0.0
        verba_disponivel = _float(row_total.iloc[_col_vdi]) if df.shape[1] > _col_vdi else 0.0
        saldo_ctp        = _float(row_total.iloc[_col_sld]) if df.shape[1] > _col_sld else 0.0

        # ── Cálculos Derivados ───────────────────────────────────────────
        cpi = (medido_acum / realizado_acum) if realizado_acum != 0 else 1.0
        eac = (orcado_total / cpi) if cpi != 0 else orcado_total
        pct_medido = (medido_acum / orcado_total * 100) if orcado_total != 0 else 0.0
        pct_realizado = (realizado_acum / orcado_total * 100) if orcado_total != 0 else 0.0
        custo_minimo = realizado_acum + saldo_ctp

        # ── Etapas nível 2 ────────────────────────────────────────────────
        # Linhas de nível 2: código com exatamente um ponto (ex: "01.001")
        etapas_nivel2 = []
        for i in range(1, linha_total):
            row = df.iloc[i]
            cod = str(row.iloc[0]).strip()
            # Regex para "01.001" ou "1.001"
            if re.match(r'^\d{1,2}\.\d{3}$', cod):
                desc = str(row.iloc[1]).strip() if df.shape[1] > 1 else cod
                orc  = _float(row.iloc[_col_orc])
                med  = _float(row.iloc[_col_med])
                rea  = _float(row.iloc[_col_rea])
                etapas_nivel2.append({
                    "codigo": cod,
                    "descricao": desc,
                    "orcado": orc,
                    "medido": med,
                    "realizado": rea,
                    "cpi": (med / rea) if rea != 0 else 1.0,
                    "pct_medido": (med / orc * 100) if orc != 0 else 0.0,
                })

        # ── Metadados ────────────────────────────────────────────────────
        periodo_final = ""
        if arquivo_nome:
            m = re.search(r'(\d{2})-(\d{2})-(\d{4})', arquivo_nome)
            if m:
                periodo_final = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        if not periodo_final:
            periodo_final = datetime.now().strftime("%Y-%m-%d")

        obra_nome = ""
        if arquivo_nome:
            m2 = re.search(r'_-_(.+?)\.xlsx', arquivo_nome, re.IGNORECASE)
            if m2:
                obra_nome = m2.group(1).replace('_', ' ').strip()

        return {
            "obra_nome":        obra_nome,
            "periodo_final":    periodo_final,
            "orcado_total":     orcado_total,
            "medido_acum":      medido_acum,
            "realizado_acum":   realizado_acum,
            "comprometido":     comprometido,
            "verba_disponivel": verba_disponivel,
            "saldo_ctp":        saldo_ctp,
            "cpi":             round(cpi, 4),
            "eac":             round(eac, 2),
            "pct_medido":      round(pct_medido, 2),
            "pct_realizado":   round(pct_realizado, 2),
            "custo_minimo":    round(custo_minimo, 2),
            "tem_medicao":     medido_acum > 0,
            "etapas_nivel2":   etapas_nivel2,
            "arquivo_nome":    arquivo_nome,
            "data_upload":     datetime.now().isoformat(),
        }

    except Exception as e:
        return {"erro": f"Erro ao processar arquivo: {str(e)}"}
