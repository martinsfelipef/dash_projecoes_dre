# CONTEXT.md — Dashboard Financeiro Brocks / Align
> Leia este arquivo inteiro antes de qualquer ação.
> Atualizado em: abril 2026

---

## ⚙️ REGRA OBRIGATÓRIA — GIT

Após cada tarefa concluída e testada, SEMPRE executar:

```bash
cd /mnt/c/Users/flore/dashboard-financeiro
git add -A
git commit -m "descrição curta do que foi feito"
git push origin main
```

Não pergunte se deve fazer push. Faça sempre, automaticamente.

---

## 🧭 O que é este projeto

Dashboard financeiro web em Python + Streamlit, hospedado no Streamlit Cloud.
Repositório: martinsfelipef/dash_projecoes_dre (branch main, entry point app.py)
URL: dash-brocks.streamlit.app

O usuário (Felipe) NÃO é desenvolvedor. Explique antes de agir. Uma coisa por vez.

---

## 🏢 Contexto de negócio

Cliente: Brocks Empreendimentos Ltda (holding)
SPEs ativas: SPE Tereza Cristina, SPE Marta Darela (e futuras)
Parceiro: Align Gestão de Negócios

Regra central: Brocks Matriz recebe BDI (%) sobre o CPV das SPEs.

Lógica de dados:
  PASSADO REAL                      PROJEÇÃO FUTURA
  DRE anual 2025 (já carregada)     Receita: Competência / Caixa / POC
  DRE mensal 2026+ (upload mensal)  CPV futuro: CFF mês a mês + CUB
  Relatório de Vendas SIENGE        Outros custos: média histórica + 0,5%/mês
  CPL (Custo por Nível) mensal
  CFF (Cronograma Físico/Financeiro)

Visão de Receita (sidebar):
  Competência: VGV reconhecido na data da venda (relatório de vendas)
  Caixa: entrada + parcelas mensais (parâmetros por SPE)
  POC: VGV total × delta de avanço físico mensal

---

## 📁 Estrutura de arquivos

```
dashboard-financeiro/
├── app.py                           (~2827 linhas)
├── requirements.txt
├── .gitignore
├── CONTEXT.md
├── data/
│   ├── dados_dre.json
│   ├── rolling_{nome_spe}.json      um por SPE
│   ├── sims_{username}.json
│   └── config_padrao.json
├── .streamlit/
│   └── secrets.toml                 NÃO está no git
└── utils/
    ├── parser_sienge.py
    ├── parser_template.py
    ├── parser_cronograma_sienge.py  CFF
    ├── parser_custo_nivel.py        CPL
    ├── parser_vendas_sienge.py      Relatório de Vendas
    └── github_storage.py
```

---

## 🗃️ Persistência

CRÍTICO — imports SEM prefixo "utils.":
  CORRETO: from github_storage import save_rolling_state
  ERRADO:  from utils.github_storage import save_rolling_state
O sys.path já inclui utils/ (linha 56 do app.py).

---

## 📊 As 7 abas

TABS = [
    "⚙️ Configurações",      → render_configuracoes()   L2035
    "📊 DRE Analítica",       → render_dre()             L929
    "🏗️ Resumo de Obras",     → render_resumo_obras()    L1031
    "📅 Rolling Forecast",    → render_rolling_forecast() L2553
    "📐 Indicadores",         → render_indicadores()     L1577
    "🎯 Sensibilidade",       → render_sensibilidade()   L1469  ← NÃO MEXER
    "💰 FCFF & DCF",          → render_fcff_dcf()        L1816  ← NÃO MEXER
]

---

## ⚙️ Aba Configurações — estrutura dos blocos

Bloco 1 — Dados da Obra (por SPE)
  CFF: upload único, substitui em reprogramações
  CPL: upload mensal, histórico de snapshots
  DRE Mensal: upload mensal 2026+
  Relatório de Vendas: upload mensal

Bloco 2 — Parâmetros de Receita (por SPE)
  VGV: tabela READ-ONLY, preenchida automaticamente pelo relatório de vendas
  POC: inputs manuais por mês (% acumulado)
  Parâmetros Caixa: Entrada %, Parcela/Un, Mês de entrega (seleção mês/ano)

Bloco 3 — Parâmetros Gerais (por SPE)
  BDI base, CUB mensal, BDI mensal por mês

---

## 🔄 Estado do Rolling Forecast por SPE

dict salvo em data/rolling_{nome}.json:

{
    "cronograma":      {...},   # parser_cronograma_sienge
    "historico_cpl":   [...],   # snapshots CPL, ordenados por periodo_final
    "vendas":          {...},   # parser_vendas_sienge mais recente
    "total_unidades":  0,       # informado pelo usuário
    "vgv":             {1: {"unidades": 0, "preco": 350000.0}, ...},
    "poc_acum":        [0]*24,
    "pct_entrada":     7.0,
    "parcela_un":      1500.0,
    "mes_entrega":     12,      # 1-based, relativo ao início da OBRA
    "bdi_rate":        14.0,
    "bdi_mensal":      [14.0]*24,
    "cub_mensal":      0.5,
    "g_custos":        10.0,
    "data_inicio":     {"ano": 2024, "mes": 1},  # automático do CFF
    "data_fim":        {"ano": 2026, "mes": 12},  # automático do CFF
    "meses_reais":     {},  # legado
    "cron_orc":        {},  # legado
}

ATENÇÃO chaves numéricas:
  vgv e meses_reais: chaves são INTEIROS
  JSON salva como string → converter ao carregar:
    {int(k): v for k, v in loaded["vgv"].items()}

---

## 📐 Lógica do Rolling Forecast (build_dre_projetada)

HORIZONTE: desde a primeira venda até o fim da obra
  Ex: Marta Darela → abr/2023 a set/2026 (~42 meses)
  Não confundir com período da obra (jul/24 a set/26)

3 ZONAS no loop de N meses:

  ZONA A: i < _idx_inicio_dre  (antes da DRE histórica, ex: abr/23 a dez/24)
    Receita: VGV real do relatório OU projeção (conforme visão)
    CPV: CFF
    Outros: média histórica × drift

  ZONA B: _idx_inicio_dre <= i < _idx_fim_dre  (DRE histórica, jan/25 a dez/25)
    CPV, despesas, RF, IR: DRE real
    Receita Caixa: DRE real
    Receita Competência/POC: VGV do relatório de vendas

  ZONA C: i >= _idx_fim_dre  (futuro, jan/26 em diante)
    Receita: projeção por visão
    CPV: CFF × fator CUB acumulado
    Outros: média histórica × drift

ÍNDICE DO VGV — CRÍTICO:
  vgv_cfg usa chaves 1-based relativas ao início da OBRA (não do horizonte)
  Converter: m_obra = i - _offset_obra + 1
  _offset_obra = meses entre início do horizonte e início da obra

---

## 📂 Parsers — colunas dos arquivos reais

parser_cronograma_sienge (CFF):
  Múltiplos blocos de 4 meses (25 colunas por bloco)
  Retorna: obra_nome, data_inicio, data_fim, total_obra,
           meses, custos_por_mes, contas, n_meses, arquivo_nome, data_upload

parser_custo_nivel (CPL):
  Tabela plana, 16 colunas
  Col 0=Código EAP, Col 3=Orçado, Col 4=Medido, Col 5=Realizado
  Col 10=Comprometido, Col 12=Verba Disp, Col 13=Saldo CTP
  Linha Total da obra: última linha com valor numérico > 1.000.000 na col 0
  Retorna: orcado_total, medido_acum, realizado_acum, comprometido,
           verba_disponivel, saldo_ctp, cpi, eac, etapas_nivel2,
           periodo_final, arquivo_nome, data_upload

parser_vendas_sienge (Vendas):
  Col 0=unidade (ex: "MD APTO 703"), Col 2=data (str DD/MM/AAAA), Col 14=valor (int)
  Retorna: unidades_vendidas, vgv_vendido, preco_medio,
           vendas_por_mes ({"AAAA-MM": {"unidades": int, "vgv": float}})
           data_ultima_venda, arquivo_nome, data_upload

---

## 🐛 Tarefas pendentes (abril 2026)

Em ordem de prioridade:

1. TAREFA_PARSER_VENDAS.md
   Criar utils/parser_vendas_sienge.py + upload na aba Configurações
   + função _calcula_vgv_projetado() + fix duplicata tabela VGV

2. TAREFA_FIX_VGV_READONLY.md
   Tabela VGV vira read-only (fix duplicata)

3. TAREFA_FIX_INDICE_VGV_CAIXA.md
   Corrigir índice m_obra no vgv_cfg + parâmetros Caixa por SPE

4. TAREFA_FIX_HORIZONTE_VENDAS.md (já aplicada — verificar resultado)
   Horizonte começa na primeira venda

5. TAREFA_FIX_RECEITA_VISAO.md (já aplicada — verificar resultado)
   Receita correta por visão no bloco histórico

---

## 📌 Regras para o agente

1. Leia este arquivo inteiro antes de qualquer ação
2. Explique o que vai fazer antes de fazer
3. Não apague código sem confirmar com o usuário
4. Uma tarefa por vez — commit e push ao final
5. Edições cirúrgicas — não refatore sem pedido
6. Sempre na branch main
7. imports SEM prefixo "utils."
8. Não mexer em Sensibilidade e FCFF & DCF
9. build_dre_projetada() é função pura (não é @st.fragment)
10. vgv_cfg usa índice relativo à OBRA, não ao horizonte completo
