"""
Parser do Relatório de Vendas — SIENGE
Arquivo: Vendas_por_Empreendimento_Simplificado_*.xlsx
"""
import pandas as pd
import io
import re
from datetime import datetime

# Termos para excluir garagens/vagas do cálculo de vendas
_EXCLUIR_VENDAS = {"garagem", "vaga", "pvg", "vgs", "box", "deposito", "depósito", "estacionamento"}

def _e_unidade_vendas(nome: str) -> bool:
    """Retorna True se for uma unidade principal (não garagem)."""
    n = str(nome).lower().strip()
    return not any(ex in n for ex in _EXCLUIR_VENDAS)

def parse_vendas_sienge(data: bytes, arquivo_nome: str = "") -> dict:
    """
    Parseia relatório 'Vendas por Empreendimento — Simplificado' do SIENGE.
    """
    try:
        df = pd.read_excel(io.BytesIO(data), header=None)

        # ── Localizar linha de cabeçalho ──────────────────────────────
        header_row = None
        for i in range(min(25, len(df))):
            cell = str(df.iloc[i, 0]).strip().lower()
            # O Sienge costuma ter 'N. do contrato' ou 'Unidade' na primeira coluna do cabeçalho
            if "n. do contrato" in cell or "contrato" in cell or cell == "unidade":
                header_row = i
                break
        
        if header_row is None:
            # Fallback: procurar em qualquer coluna por 'Data Contrato'
            for i in range(min(25, len(df))):
                row_str = " ".join(str(v).lower() for v in df.iloc[i])
                if "data" in row_str and ("contrato" in row_str or "venda" in row_str):
                    header_row = i
                    break

        if header_row is None:
            return {"erro": "Cabeçalho não encontrado no relatório de vendas. Verifique se o arquivo é o 'Vendas por Empreendimento — Simplificado'."}

        # ── Mapeamento de colunas ─────────────────────────────────────
        # No formato padrão Simplificado da Brocks:
        # Col 0: Unidade
        # Col 2: Data do Contrato
        # Col 14: Valor do Contrato
        col_unidade = 0
        col_data    = 2
        col_valor   = 14
        
        # Tenta extrair nome da obra das primeiras linhas (acima do cabeçalho)
        obra_nome = ""
        for i in range(header_row):
            row_vals = [str(v).lower().strip() for v in df.iloc[i] if pd.notna(v)]
            if "empreendimento" in row_vals or "obra" in row_vals:
                # O valor costuma estar na célula seguinte
                for j, v in enumerate(df.iloc[i]):
                    if pd.notna(v) and ("empreendimento" in str(v).lower() or "obra" in str(v).lower()):
                        if j + 1 < len(df.iloc[i]) and pd.notna(df.iloc[i, j+1]):
                            obra_nome = str(df.iloc[i, j+1]).strip()
                            break
                if obra_nome: break

        # ── Processar vendas ──────────────────────────────────────────
        vendas = []
        for i in range(header_row + 1, len(df)):
            row = df.iloc[i]
            unidade = str(row.iloc[col_unidade]).strip()
            
            if not unidade or unidade.lower() in ("nan", "none", "total", "subtotal", ""):
                continue
            
            # Filtro de garagens
            if not _e_unidade_vendas(unidade):
                continue
                
            # Data do contrato
            dt_raw = row.iloc[col_data]
            dt = None
            if isinstance(dt_raw, datetime):
                dt = dt_raw
            else:
                try:
                    # Tenta converter string DD/MM/AAAA
                    dt = pd.to_datetime(str(dt_raw), dayfirst=True)
                except:
                    continue
            
            if pd.isna(dt) or dt is None:
                continue
            
            # Valor do contrato
            valor = 0.0
            try:
                v_raw = row.iloc[col_valor]
                if isinstance(v_raw, (int, float)):
                    valor = float(v_raw)
                else:
                    # Trata "R$ 1.234,56"
                    s_val = str(v_raw).replace("R$", "").replace(".", "").replace(",", ".").strip()
                    valor = float(s_val)
            except:
                valor = 0.0
                
            mes_ano = dt.strftime("%Y-%m")
            vendas.append({
                "unidade":  unidade,
                "data":     dt.strftime("%Y-%m-%d"),
                "mes_ano":  mes_ano,
                "valor":    valor,
            })
            
        if not vendas:
            return {"erro": "Nenhuma venda válida de unidade principal encontrada no arquivo."}
            
        # Agrupar por mês
        vendas_por_mes = {}
        for v in vendas:
            m = v["mes_ano"]
            if m not in vendas_por_mes:
                vendas_por_mes[m] = {"unidades": 0, "vgv": 0.0}
            vendas_por_mes[m]["unidades"] += 1
            vendas_por_mes[m]["vgv"]      += v["valor"]

        unidades_vendidas = len(vendas)
        vgv_vendido       = sum(v["valor"] for v in vendas)
        preco_medio       = vgv_vendido / unidades_vendidas if unidades_vendidas > 0 else 0.0
        data_ultima_venda = max(v["data"] for v in vendas) if vendas else ""

        return {
            "obra_nome":          obra_nome,
            "unidades_vendidas":  unidades_vendidas,
            "vgv_vendido":        vgv_vendido,
            "preco_medio":        preco_medio,
            "vendas_por_mes":     vendas_por_mes,
            "data_ultima_venda":  data_ultima_venda,
            "arquivo_nome":       arquivo_nome,
            "data_upload":        datetime.now().isoformat(),
        }
    except Exception as e:
        return {"erro": f"Erro no processamento das vendas: {str(e)}"}
