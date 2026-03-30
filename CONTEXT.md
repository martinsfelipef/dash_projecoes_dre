# CONTEXT.md — Dashboard Financeiro Brocks / Align
> Leia este arquivo inteiro antes de qualquer ação.
> Atualizado em: março 2026

---

## ⚙️ REGRA OBRIGATÓRIA — GIT

**Após cada tarefa concluída e testada, você DEVE sempre:**

```bash
cd /mnt/c/Users/flore/dashboard-financeiro
git add -A
git commit -m "descrição curta do que foi feito"
git push origin main
```

Não pergunte se deve fazer push. Faça sempre, automaticamente, ao final de cada tarefa.
Se houver erro no push, informe o usuário com a mensagem de erro exata.

---

## 🧭 O que é este projeto

Dashboard financeiro web construído em **Python + Streamlit**, hospedado no
**Streamlit Cloud**, conectado ao repositório GitHub
`martinsfelipef/dash_projecoes_dre` (branch `main`, entry point `app.py`).

O usuário principal (**Felipe**) **não é desenvolvedor**. Isso significa:
- Explique sempre o que você vai fazer antes de fazer
- Prefira soluções simples e diretas
- Quando houver dúvida, pergunte antes de agir
- Não quebre o que já está funcionando
- Faça uma tarefa por vez e mostre o resultado

---

## 🏢 Contexto de negócio

**Cliente:** Brocks Empreendimentos Ltda (holding)
**Subsidiária:** SPE Tereza Cristina (executa a obra)
**Parceiro:** Align Gestão de Negócios (gestão financeira)

**Regra de negócio central:**
A Brocks (Matriz) não executa obra. Sua receita vem de um percentual de
**BDI** aplicado sobre o CPV (custo da obra) da SPE Tereza Cristina.
O BDI varia mês a mês na projeção futura.

**Lógica de dados:**
- DRE mensal uploadada = o que **já aconteceu** (passado real)
- Projeção = o que **ainda vai acontecer** até o fim da obra
- Custos futuros vêm do **Cronograma Físico-Financeiro** (Excel SIENGE)
- Custos e parcelas futuras se atualizam pelo índice **CUB** mensal

---

## 📁 Estrutura de arquivos

```
dashboard-financeiro/
├── app.py                          ← arquivo principal (~2000 linhas)
├── requirements.txt
├── .gitignore
├── CONTEXT.md                      ← este arquivo
├── data/
│   └── dados_dre.json              ← persistência de dados DRE
├── .streamlit/
│   ├── secrets.toml                ← NÃO está no git (credenciais)
│   └── assets/
│       └── logo_brocks.jpg
└── utils/
    ├── parser_sienge.py            ← parser de DRE anual do SIENGE
    ├── parser_template.py          ← parser do template Align
    ├── parser_cronograma_sienge.py ← parser do Cronograma Físico-Financeiro
    ├── rolling_forecast.py         ← cálculos dos 3 métodos de receita
    └── github_storage.py           ← persistência via GitHub API
```

---

## ⚙️ Dependências (`requirements.txt`)

```
streamlit>=1.32.0
pandas>=2.0.0
plotly>=5.18.0
numpy>=1.24.0
openpyxl>=3.1.0
PyGithub>=2.3.0
```

---

## 🔐 Autenticação e segredos

O arquivo `.streamlit/secrets.toml` (fora do git) contém:

```toml
[users]
admin_username = "senha_admin"
outro_usuario  = "senha_viewer"

[github]
token = "ghp_..."
repo  = "martinsfelipef/dash_projecoes_dre"
```

- O **primeiro usuário** em `[users]` é automaticamente o Admin
- Os demais são Viewers
- Token GitHub: permissões Contents (read/write) + Metadata (read)

---

## 🗃️ Persistência de dados

| Dado | Arquivo no GitHub |
|------|-------------------|
| DRE (dados das empresas) | `data/dados_dre.json` |
| Simulações por usuário | `data/sims_{username}.json` |
| Configuração padrão (Admin) | `data/config_padrao.json` |

---

## 📊 As 5 abas do dashboard

### Tab 1 — DRE Analítica ✅ Funcionando
- DRE completa (Receita Bruta → Lucro Líquido)
- Upload Excel SIENGE ou Template Align
- Consolidado ou por empresa
- 3 visões de receita: Caixa, Competência, POC
- Exportação Excel, KPIs com popovers

### Tab 2 — Rolling Forecast ✅ Implementado (1 bug pendente)
Separado em 2 sub-abas internas:

**⚙️ Configurações:**
- Período da obra: seletores de mês/ano início e fim (N calculado automaticamente)
- Parâmetros: BDI base, Entrada %, Parcela/Un, Mês entrega, Δ Custos
- BDI mensal variável por tabela (só meses futuros)
- CUB mensal (atualiza custos e parcelas futuros)
- Upload Cronograma Físico-Financeiro (Excel SIENGE) — define custos futuros
- Upload mensal SIENGE (dados reais) — usa parse_cronograma_sienge()
- Tabela VGV editável (unidades + preço por mês)
- POC acumulado + Curva S

**📊 Resultados:**
- KPIs: Orçamento Total, Já Gasto, Falta Gastar, % Executado
- Barra de progresso da obra
- Gráfico Planejado vs Realizado (custos mensais)
- DRE Rolling com 3 métodos: Competência, Caixa, POC, Comparativo
- Gráficos com divisória Real | Projetado
- Exportação Excel por método

**⚠️ Bug pendente:**
bdi_matriz_mensal(spes, N) — a função em utils/rolling_forecast.py
pode não aceitar o segundo parâmetro N. Verificar a assinatura da
função. Se aceitar apenas (spes), ajustar a chamada e redimensionar
o array retornado para tamanho N.

### Tab 3 — Análise de Sensibilidade ✅ Funcionando
- 3 cenários (Pessimista / Realista / Otimista)
- Curva VPL × driver
- Matriz de risco (Probabilidade × Impacto)

### Tab 4 — Indicadores (KPIs) ✅ Funcionando
- Margens (Bruta, EBITDA, Líquida)
- GAO, Ponto de Equilíbrio
- Composição de custos

### Tab 5 — FCFF & DCF ✅ Funcionando
- WACC via CAPM, projeção FCFF, EV Bridge waterfall
- Sensibilidade WACC × g

---

## 🖥️ Sidebar — estado atual

- Logo: texto "Brocks Empreendimentos | Finanças" (sem imagem)
- Cliente: fixo em "Brocks Empreendimentos" (sem selectbox)
- Empresa: lista com toggle checkbox para ativar/desativar cada empresa
  - Estado em st.session_state["empresas_ativas"]
  - Empresas desativadas saem do Consolidado e do seletor
- Seletor de empresa: Consolidado + empresas ativas
- Visão de receita: pills (Caixa / Competência / POC)
- DREs no sistema: lista com ícone de fonte e botão remover
- Gerenciar empresas: expander com delete e criar novo
- Simulações: salvar/carregar/deletar, máx 5 (admin) / 3 (viewer)
- Salvar como Padrão: só admin
- Restaurar Padrão: só viewer
- Rodapé: Align Gestão de Negócios © 2026

---

## 🔐 Tela de Login — estado atual

- Fundo escuro #0A1118
- Logo "BROCKS" em laranja (texto, sem imagem)
- Campos: Usuário (placeholder: "seu usuário") e Senha (placeholder: "sua senha")
- Botão "Entrar" laranja
- Sem abas falsas, sem link "Esqueci minha senha"
- Sidebar e header ocultos na tela de login

---

## 🐛 Bug pendente

| # | Problema | Arquivo | Linha aprox | Prioridade |
|---|----------|---------|-------------|------------|
| 1 | bdi_matriz_mensal(spes, N) — verificar se função aceita 2º parâmetro | app.py | 1423 | Média |

---

## 🚀 Deploy

- Plataforma: Streamlit Cloud
- Repositório: martinsfelipef/dash_projecoes_dre
- Branch: main
- Entry point: app.py
- Secrets: configurados no painel do Streamlit Cloud
- Para deployar: basta git push origin main. O Streamlit Cloud redeploya automaticamente.

---

## 💻 Ambiente local

- OS: Windows com WSL (Ubuntu)
- Projeto: /mnt/c/Users/flore/dashboard-financeiro/
- Rodar localmente:
  export PATH="$HOME/.local/bin:$PATH"
  cd /mnt/c/Users/flore/dashboard-financeiro
  streamlit run app.py

---

## 🔄 Como Felipe trabalha

1. Abre o Antigravity (ou Claude Code no terminal WSL)
2. O agente lê este CONTEXT.md antes de qualquer ação
3. Felipe descreve o que quer em português simples
4. O agente executa, testa, e faz commit + push automaticamente
5. Felipe volta ao Claude.ai com o app.py atualizado para planejar o próximo passo

Ferramentas disponíveis:
- Antigravity — IDE principal com interface visual
- Claude Code (claude no terminal WSL) — backup quando Antigravity esgota quota
- Claude.ai — planejamento de tarefas e geração de instruções

---

## 📌 Regras para o agente

1. Leia este arquivo inteiro antes de qualquer ação
2. Sempre explique o que vai fazer antes de fazer — Felipe não é dev
3. Não apague código sem confirmar — projeto em produção
4. Faça uma tarefa por vez — mostre resultado antes de avançar
5. Prefira mudanças cirúrgicas — edições pequenas e precisas
6. Sempre trabalhe na branch main — verificar com git branch antes
7. OBRIGATÓRIO após cada tarefa: git add -A && git commit -m "..." && git push origin main
8. Se encontrar dependência inesperada: avise antes de resolver
