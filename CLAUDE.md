# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
# Activate virtual environment
source .venv/bin/activate

# Run the dashboard
streamlit run app.py
```

Default URL: `http://localhost:8501`

## Tech Stack

- **Python 3** + **Streamlit** (UI framework)
- **Pandas / NumPy** (data processing)
- **Plotly** (interactive charts)
- **openpyxl** (Excel I/O)

## Architecture

### Entry Point
`app.py` (~1,281 lines) is the monolithic Streamlit app. All state lives in `st.session_state` — no database or file persistence.

### Utility Modules (`utils/`)
| File | Purpose |
|------|---------|
| `parser_sienge.py` | Parses SIENGE ERP Excel exports into a standardized dict |
| `parser_template.py` | Parses internal "Template DRE Align" Excel files (sheet: `DRE_2025`) |
| `parser_sienge_mensal.py` | Parses monthly SIENGE chronogram files for rolling forecast real data |
| `rolling_forecast.py` | Core rolling forecast calculations (seasonality, cash/accrual/POC methods, BDI matrix) |

### Data Format
All parsers return a standardized dict:
```python
{
  "nome": str,         # Company name
  "periodo": str,      # Report period
  "dados": {           # Account code → list of 12 monthly floats
    "01": [...],       # Receita Bruta
    "02": [...],       # Impostos s/ Receita
    "03": [...],       # Receita Líquida
    "04": [...],       # CPV
    "05": [...],       # Lucro Bruto
    "06": [...],       # Despesas Operacionais
    "07": [...],       # EBITDA
    "11": [...],       # Resultado Financeiro
    "12": [...],       # Lucro antes IR
    "13": [...],       # IR
    "14": [...],       # Lucro Líquido
  },
  "avisos": [...],     # Validation warnings
  "preview": DataFrame
}
```

### Session State Structure
```python
st.session_state = {
  "clientes": {
    "<client_name>": {
      "<company_name>": {
        "dres": [<parsed_dict>, ...],   # Uploaded DRE files
        "rolling": {...}                 # Rolling forecast state
      }
    }
  },
  "cliente_atual": str,
  "empresa_atual": str,
}
```

### Revenue Methods (Rolling Forecast)
Three revenue projection methods in `rolling_forecast.py`:
- **Competência** — Accrual: revenue recognized at point of sale
- **Caixa** — Cash: entrance payment + monthly installments + balloon at delivery
- **POC** — Percentage of completion: cumulative % applied to VGV

### Key Functions in `app.py`
- `calc_dre(dados)` — Computes all 11 P&L line items from raw account data
- `dre(empresa)` — Aggregates DREs across uploaded files for a company
- `projeta(base, anos, crescimento)` — Projects financials with growth parameters
- `fmt(val)` — Formats currency (R$, K, M notation)
- `excel_dre()` — Exports current DRE view to `.xlsx`

### Tab Layout
1. **DRE 2025** — KPIs, charts, detailed P&L table, Excel export
2. **Rolling Forecast** — Horizon/date config, real data uploads, projected DRE, BDI matrix
3. **Sensitivity Analysis** — 3-scenario comparison, NPV curve, risk matrix
4. **Financial Indicators** — Margins, leverage, breakeven, GAO
5. **FCFF & DCF Valuation** — WACC/CAPM, FCFF projections, terminal value, sensitivity matrix

## Corporate Color Palette
```python
NAVY      = "#0A2540"
BLUE      = "#2063A0"
LIGHT_BLU = "#EDF4FC"
GOLD      = "#C8941F"
SOFT_RED  = "#D9534F"   # negative values
```

## Known Issue
`parser_cronograma_sienge` is imported in `parser_sienge_mensal.py` but the file does not exist. `app.py` includes an inline fallback (lines ~63–76). If implementing monthly real-data uploads fully, this module needs to be created returning `{cpv_real, dop_real, rf_real, ir_real}`.

## Contexto do Negócio

**Cliente:** Brocks Empreendimentos (incorporadora imobiliária)
**Desenvolvedor:** Align Gestão de Negócios (consultoria)

### Empresas cadastradas
- **Brocks Empreendimentos Ltda (Matriz)** — dados hardcoded; receita vem do BDI (%) aplicado sobre o CPV das SPEs
- **Brocks Res. Tereza Cristina SPE Ltda** — SPE ativa com dados 2025

### Regras de negócio críticas
- Receita da Matriz é calculada como BDI (%) × CPV de cada SPE — não é receita operacional direta
- Rolling Forecast suporta 3 métodos de receita: Competência, Caixa e POC (ver seção Revenue Methods acima)
- Dados financeiros são sigilosos — nunca commitar em repositório público

### Pendências prioritárias
1. **`utils/parser_cronograma_sienge.py`** — arquivo faltante que quebra o Rolling Forecast (ver Known Issue acima)
2. **Persistência de dados** — `st.session_state` se perde no refresh; solução futura requer externalizar dados (arquivo local, banco ou cloud storage)
3. **Cronograma físico-financeiro** — alimenta o campo `cron_orc` do rolling forecast; ainda não implementado
4. **Deploy Streamlit Cloud** — requer externalizar os dados hardcoded da Matriz e das SPEs
