"""
Parser do Relatório de Recebíveis — SIENGE
Arquivo: Contas_a_receber_-_recebiveis_*.xlsx

Estrutura:
  L9:  cabeçalho (Data vecto, Cliente, Documento, Título, Parc, TC,
                  Unid. princ, Valor original, ...)
  L10+: dados
  Col 0  (A): Data vencimento
  Col 10 (K): TC (tipo de contrato)
  Col 11 (L): Unidade
  Col 13 (N): Valor original

Tipos de TC:
  PM = Parcela Mensal        → fluxo mensal regular
  FI = Financiamento         → repasse bancário (data futura)
  CH = Cheque/À vista        → recebimento único
  RF = Reforço               → parcelas de reforço
  PC = Parcela Complementar  → complemento de parcelas
  PI = Parcela Inicial       → entrada/sinal
  PE = Permuta               → EXCLUIR (não gera caixa)

Retorna dict com:
  "obra_nome"        : str
  "data_exportacao"  : str   — data do export (do cabeçalho)
  "por_mes"          : dict  — {"AAAA-MM": float} soma de PM+FI+CH+RF+PC+PI
  "pm_por_mes"       : dict  — {"AAAA-MM": float} só parcelas mensais
  "fi_por_mes"       : dict  — {"AAAA-MM": float} só financiamentos
  "resumo_tipos"     : dict  — {TC: {"parcelas": int, "valor": float, "unidades": int}}
  "total_recebiveis" : float — sem PE
  "total_pm"         : float
  "total_fi"         : float
  "unidades_permuta" : list  — unidades com PE
  "n_unidades_pm"    : int
  "n_unidades_fi"    : int
  "arquivo_nome"     : str
  "data_upload"      : str
  "erro"             : str   — só se falhar
"""

import io
import re
from datetime import datetime
from collections import defaultdict


_TC_EXCLUIR = {"PE"}  # permuta — não gera caixa


def parse_recebiveis_sienge(data: bytes, arquivo_nome: str = "") -> dict:
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception as e:
        return {"erro": f"Não foi possível abrir o arquivo: {e}"}

    if not rows:
        return {"erro": "Arquivo vazio."}

    # Extrai nome da obra e data de exportação do cabeçalho
    obra_nome = ""
    data_exportacao = ""
    for row in rows[:8]:
        if row[0] == "Empresa" and row[1]:
            obra_nome = str(row[1]).strip()
        if row[0] and "01/" in str(row[0]):
            # Linha com período: "01/05/2026 a 01/..."
            data_exportacao = str(row[0])[:10]

    def _parse_data(v):
        if not v: return None
        if hasattr(v, 'year'):
            return v
        try:
            return datetime.strptime(str(v).strip(), "%d/%m/%Y")
        except Exception:
            return None

    # Processa linhas de dados
    por_mes      = defaultdict(float)
    pm_por_mes   = defaultdict(float)
    fi_por_mes   = defaultdict(float)
    resumo_tipos = defaultdict(lambda: {"parcelas":0,"valor":0.0,"unidades":set()})
    unidades_permuta = []
    
    hoje = datetime.today()
    total_futuro = 0.0

    for row in rows[9:]:
        if not row[0]: continue
        tc  = str(row[10]).strip() if len(row) > 10 and row[10] else ""
        val = float(row[13]) if len(row) > 13 and row[13] and isinstance(row[13], (int,float)) else 0.0
        un  = str(row[11]).strip() if len(row) > 11 and row[11] else ""
        dt  = _parse_data(row[0])

        if not tc or tc in ["A vencer no período","Total de clientes","Valor médio"]:
            continue

        # Registra no resumo
        resumo_tipos[tc]["parcelas"] += 1
        resumo_tipos[tc]["valor"]    += val
        if un: resumo_tipos[tc]["unidades"].add(un)

        # PE: apenas registra unidade, não soma ao fluxo
        if tc in _TC_EXCLUIR:
            if un and un not in unidades_permuta:
                unidades_permuta.append(un)
            continue

        if val == 0 or dt is None:
            continue

        chave = f"{dt.year}-{dt.month:02d}"
        por_mes[chave]   += val
        if tc == "PM":
            pm_por_mes[chave] += val
        elif tc == "FI":
            fi_por_mes[chave] += val
            
        if (dt.year, dt.month) > (hoje.year, hoje.month):
            total_futuro += val

    if not por_mes:
        return {"erro": (
            "Nenhum recebível encontrado. "
            "Verifique se é o relatório 'Contas a Receber — Recebíveis' do SIENGE."
        )}

    # Converte sets para contagens
    resumo_final = {}
    for tc, d in resumo_tipos.items():
        resumo_final[tc] = {
            "parcelas":  d["parcelas"],
            "valor":     d["valor"],
            "unidades":  len(d["unidades"]),
            "lista_unidades": sorted(d["unidades"]),
        }

    total_recebiveis = sum(por_mes.values())
    total_pm = sum(pm_por_mes.values())
    total_fi = sum(fi_por_mes.values())

    n_unidades_pm = len(resumo_tipos.get("PM",{}).get("unidades",set()))
    n_unidades_fi = len(resumo_tipos.get("FI",{}).get("unidades",set()))

    return {
        "obra_nome":         obra_nome,
        "data_exportacao":   data_exportacao,
        "por_mes":           dict(por_mes),
        "pm_por_mes":        dict(pm_por_mes),
        "fi_por_mes":        dict(fi_por_mes),
        "resumo_tipos":      resumo_final,
        "total_recebiveis":  total_recebiveis,
        "total_futuro":      total_futuro,
        "total_pm":          total_pm,
        "total_fi":          total_fi,
        "unidades_permuta":  unidades_permuta,
        "n_unidades_pm":     n_unidades_pm,
        "n_unidades_fi":     n_unidades_fi,
        "arquivo_nome":      arquivo_nome,
        "data_upload":       datetime.now().isoformat(),
    }
