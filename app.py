import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import numpy as np
import io, sys, os, re

_LOCAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "dados_dre.json")

def _json_default(obj):
    import numpy as np, pandas as pd
    if isinstance(obj, np.ndarray):    return obj.tolist()
    if isinstance(obj, pd.DataFrame):  return obj.to_dict("records")
    if isinstance(obj, np.integer):    return int(obj)
    if isinstance(obj, np.floating):   return float(obj)
    return str(obj)

def _load_state():
    # 1. Tenta GitHub
    try:
        from github_storage import load_state_github
        result = load_state_github()
        if result is not None:
            return result
    except Exception:
        pass
    # 2. Fallback: arquivo local
    try:
        if os.path.exists(_LOCAL_FILE):
            with open(_LOCAL_FILE, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if raw and raw != "{}":
                import json
                return json.loads(raw)
    except Exception:
        pass
    return None

def save_state():
    # 1. Salva localmente (sempre, independente do GitHub)
    try:
        import json
        os.makedirs(os.path.dirname(_LOCAL_FILE), exist_ok=True)
        with open(_LOCAL_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(st.session_state.clientes), f,
                      ensure_ascii=False, indent=2, default=_json_default)
    except Exception as e:
        st.warning(f"Aviso: não foi possível salvar localmente: {e}")
    # 2. Tenta GitHub também (silencioso se não configurado)
    try:
        from github_storage import save_state_github
        save_state_github(st.session_state.clientes)
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
from parser_sienge   import parse_sienge

# ── Parser completo SIENGE (extrai todos os itens com hierarquia) ───────────
def _parse_sienge_full(data: bytes) -> list:
    """Retorna lista de dicts {codigo, conta, nivel, valores:[12 floats]}."""
    import io
    df = pd.read_excel(io.BytesIO(data), header=None)
    _mmap = {'janeiro':0,'fevereiro':1,'março':2,'marco':2,'abril':3,'maio':4,
             'junho':5,'julho':6,'agosto':7,'setembro':8,'outubro':9,
             'novembro':10,'dezembro':11}
    header_rows = [i for i,r in df.iterrows()
                   if str(r.iloc[0]).strip().lower() in ('código','codigo')]
    blocos = []
    for h in header_rows:
        col_map = {}
        for c in range(2, len(df.columns)):
            cell = str(df.iloc[h,c]).strip().lower()
            for mes, idx in _mmap.items():
                if mes in cell:
                    col_map[idx] = c; break
        nxt = header_rows[header_rows.index(h)+1] if h != header_rows[-1] else len(df)
        blocos.append((col_map, list(range(h+1, nxt))))
    itens, ordem = {}, []
    for col_map, rows in blocos:
        for i in rows:
            row = df.iloc[i]
            cod   = str(row.iloc[0]).strip()
            conta = str(row.iloc[1]).strip() if len(row) > 1 else ''
            if not cod or cod == 'nan' or conta in ('nan','') or not conta:
                continue
            try:
                float(cod.replace(',','.'))
            except:
                continue
            nivel = cod.count('.') + 1
            if cod not in itens:
                itens[cod] = {"conta": conta, "nivel": nivel,
                              "valores": [0.0]*12}
                ordem.append(cod)
            for mes_idx, col_idx in col_map.items():
                try:
                    v = float(str(row.iloc[col_idx]).replace(',','.')
                              .replace('nan','0'))
                    itens[cod]["valores"][mes_idx] = v
                except:
                    pass
    return [{"codigo": c, **itens[c]} for c in ordem]


from rolling_forecast import (calc_competencia,calc_caixa,calc_poc,
                               build_dre_rolling,bdi_matriz_mensal)
from parser_cronograma_sienge import parse_cronograma_sienge
try:
    from parser_custo_nivel import parse_custo_nivel
except ImportError:
    def parse_custo_nivel(data, arquivo_nome=""):
        return {"erro": "Parser não encontrado. Verifique utils/parser_custo_nivel.py"}

try:
    from parser_vendas_sienge import parse_vendas_sienge
except ImportError:
    def parse_vendas_sienge(data, arquivo_nome=""):
        return {"erro": "Parser não encontrado. Verifique utils/parser_vendas_sienge.py"}

try:
    from parser_unidades_sienge import parse_unidades_sienge
except ImportError:
    def parse_unidades_sienge(data, arquivo_nome=""):
        return {"erro": "Parser não encontrado. Verifique utils/parser_unidades_sienge.py"}

try:
    from parser_recebiveis_sienge import parse_recebiveis_sienge
except ImportError:
    def parse_recebiveis_sienge(data, arquivo_nome=""):
        return {"erro": "Parser não encontrado. Verifique utils/parser_recebiveis_sienge.py"}

st.set_page_config(page_title="Dashboard Financeiro | Brocks",
                   page_icon="🏗️", layout="wide",
                   initial_sidebar_state="expanded")

# ── Autenticação ───────────────────────────────────────────────────────────────

def _users_configured():
    """Retorna True se [users] estiver configurado em st.secrets."""
    try:
        return "users" in st.secrets and len(st.secrets["users"]) > 0
    except Exception:
        return False

def _admin_username():
    """Retorna o username do admin (primeiro da lista)."""
    try:
        return list(st.secrets["users"].keys())[0]
    except Exception:
        return None

def _check_password(username, password):
    """Verifica username e senha contra st.secrets['users']."""
    try:
        stored = st.secrets["users"].get(username)
        return stored is not None and stored == password
    except Exception:
        return False

# ── Helpers de Simulação ───────────────────────────────────────────────────────
_POC_DEFS  = [3, 6, 10, 16, 22, 30, 40, 52, 62, 74, 86, 100]
_COMP_DEFS = [0, 0, 0, 0, 180000, 250000, 320000, 400000, 250000, 280000, 220000, 350000]

def _get_sim_params():
    """Captura o estado atual dos parâmetros do sidebar para salvar em simulação."""
    return {
        "visao":    st.session_state.get("visao_sel", "💰 Caixa"),
        "vgv_poc":  st.session_state.get("vgv_poc",  5_000_000.0),
        "poc":      [st.session_state.get(f"poc{i}",  _POC_DEFS[i])  for i in range(12)],
        "vgv_comp": [st.session_state.get(f"comp{i}", float(_COMP_DEFS[i])) for i in range(12)],
    }

def _apply_sim_params(params):
    """Pré-popula as chaves de session_state para que os widgets carreguem os valores da simulação."""
    if not params:
        return
    if "visao" in params:
        st.session_state["visao_sel"] = params["visao"]
    if "vgv_poc" in params:
        st.session_state["vgv_poc"] = float(params["vgv_poc"])
    if "poc" in params:
        for i, v in enumerate(params["poc"][:12]):
            st.session_state[f"poc{i}"] = int(v)
    if "vgv_comp" in params:
        for i, v in enumerate(params["vgv_comp"][:12]):
            st.session_state[f"comp{i}"] = float(v)

# ── Persistência de simulações (local + GitHub) ────────────────────────────────
def _sims_local_path(username):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".streamlit", f"sims_{username}.json")

def _load_sims_local(username):
    import json as _j
    path = _sims_local_path(username)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = _j.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []

def _save_sims_local(username, sims):
    import json as _j
    path = _sims_local_path(username)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            _j.dump(sims, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _load_sims(username):
    """Carrega simulações: tenta GitHub primeiro, fallback local."""
    try:
        from github_storage import load_simulacoes
        sims = load_simulacoes(username)
        if sims:
            return sims
    except Exception:
        pass
    return _load_sims_local(username)

def _save_sims(username, sims):
    """Salva simulações local e no GitHub."""
    _save_sims_local(username, sims)
    try:
        from github_storage import save_simulacoes
        save_simulacoes(username, sims)
    except Exception:
        pass

def _load_config_padrao():
    """Carrega config padrão do admin."""
    try:
        from github_storage import load_config_padrao
        return load_config_padrao()
    except Exception:
        return None

def _save_config_padrao(params):
    """Salva config padrão do admin."""
    try:
        from github_storage import save_config_padrao
        save_config_padrao(params)
    except Exception:
        pass
    # Também salva localmente como fallback
    import json as _j
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".streamlit", "config_padrao.json")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            _j.dump(params, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _load_config_padrao_local():
    """Fallback local para config padrão."""
    import json as _j
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".streamlit", "config_padrao.json")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return _j.load(f)
    except Exception:
        pass
    return None

# ── Tela de Login ─────────────────────────────────────────────────────────────
def _show_login():
    """Exibe tela de login centralizada e limpa."""
    st.markdown("""
    <style>
    /* Background for the entire page */
    [data-testid="stAppViewContainer"] > .main {
        background: #0A1118;
    }
    
    
    
    /* Typography inside the card */
    .login-sub { color: #8F9BA8; font-size: 0.95rem; text-align: center; margin-bottom: 2rem; }
    
    /* Inputs Styling specifically for login */
    .stTextInput label {
        color: #E2E8F0 !important;
        font-weight: 500 !important;
    }
    .stTextInput input {
        background-color: #F8FAFC !important;
        color: #0F172A !important;
        border-radius: 6px; 
        border: none !important; 
        padding: 0.8rem 1rem;
    }
    .stTextInput input:focus {
        box-shadow: 0 0 0 2px #F25C38 !important;
    }
    
    /* The primary login button */
    .stButton > button[kind="primary"] {
        background-color: #F25C38 !important;
        color: white !important;
        border-radius: 8px !important;
        padding: 0.6rem 2rem !important;
        font-weight: 600 !important;
        font-size: 1.05rem !important;
        border: none !important;
        transition: background-color 0.2s ease;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #E04825 !important;
    }
    
    /* Hide header, sidebar and top padding on login */
    header[data-testid="stHeader"] {display: none !important; height: 0 !important; min-height: 0 !important;}
    [data-testid="stSidebar"] {display: none !important;}
    .block-container {padding-top: 0 !important;}
    </style>
    """, unsafe_allow_html=True)

    col_l, col_m, col_r = st.columns([1, 2, 1])
    with col_m:
        st.markdown("""
        <p style="color:#F25C38; font-size: 2.2rem; font-weight:800; text-align:center; letter-spacing:2px; margin:2rem 0 0;">BROCKS</p>
        <p style="color:#64748B; font-size: 0.75rem; text-align:center; margin:0 0 0.5rem; letter-spacing:1px;">EMPREENDIMENTOS</p>
        <p style="color:#8F9BA8; font-size: 0.95rem; text-align:center; margin-bottom: 1.5rem;">Faça login para continuar</p>
        """, unsafe_allow_html=True)

        with st.form(key="_login_form"):
            username = st.text_input("Usuário", placeholder="seu usuário", key="_li_user")
            password = st.text_input("Senha", type="password", placeholder="sua senha", key="_li_pass")
            submitted = st.form_submit_button("Entrar", type="primary", use_container_width=True)

        if submitted:
            if _check_password(username, password):
                admin_u = _admin_username()
                role = "admin" if username == admin_u else "viewer"
                st.session_state["_logged_in"] = True
                st.session_state["_username"]   = username
                st.session_state["_role"]       = role
                st.session_state["_sims"] = _load_sims(username)
                if role == "viewer":
                    cfg = _load_config_padrao() or _load_config_padrao_local()
                    if cfg:
                        _apply_sim_params(cfg)
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")

# ── Verificação de acesso ──────────────────────────────────────────────────────
if _users_configured():
    if not st.session_state.get("_logged_in"):
        _show_login()
        st.stop()

# ── Aliases de sessão (atalhos de leitura) ────────────────────────────────────
_USERNAME = st.session_state.get("_username", "dev")
_ROLE     = st.session_state.get("_role", "admin")  # dev local = admin
_IS_ADMIN = (_ROLE == "admin")

# Garante que simulações estejam carregadas na sessão
if "_sims" not in st.session_state:
    st.session_state["_sims"] = _load_sims(_USERNAME)

# ── Paleta ────────────────────────────────────────────────────────────────────
NAVY="#0A2540"; BLUE="#2063A0"; BLIGHT="#EDF4FC"; GOLD="#C8941F"
WHITE="#FFFFFF"; TEXT="#1B2432"; GRAY="#6F7E8C"; BORDER="#DDE4ED"
SOFT_RED="#D9534F"; CHART_BLUE="#4A90D9"; CHART_NAVY="#163456"
CHART_TEAL="#2D9B8A"; CHART_AMBER="#E8A838"

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif;}
.block-container {
    padding: 3rem 2rem !important;
    max-width: 100% !important;
}

/* Modernize metrics into premium cards */
[data-testid="stMetric"] {
    background-color: #ffffff;
    border-radius: 12px;
    padding: 1rem 1.2rem;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
    border: 1px solid #f0f4f8;
    transition: transform 0.2s ease-in-out, box-shadow 0.2s ease;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.08), 0 4px 6px -2px rgba(0, 0, 0, 0.04);
}
[data-testid="stMetricLabel"] {
    font-size: 0.9rem !important;
    color: #6F7E8C !important;
    font-weight: 500;
    margin-bottom: 0.2rem;
}
[data-testid="stMetricValue"] {
    font-size: 1.6rem !important;
    color: #0A2540 !important;
    font-weight: 700 !important;
    letter-spacing: -0.5px;
}
[data-testid="stMetricDelta"] {
    font-size: 0.85rem !important;
    font-weight: 500;
}

/* Buttons and Expanders */
.stButton > button {
    border-radius: 8px;
    font-weight: 500;
    transition: all 0.2s ease;
}
[data-testid="stExpander"] {
    border-radius: 12px;
    border: 1px solid #e2e8f0;
    box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
}
</style>""", unsafe_allow_html=True)

MESES = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]

# ── Dados default ─────────────────────────────────────────────────────────────
CLIENTES_DEFAULT = {
    "Brocks Empreendimentos": {"empresas": {
        "Matriz": {
            "nome":"Brocks Empreendimentos Ltda","fonte":"Fixo",
            "rec_bruta":[0,0,2146.86,0,0,0,0,0,5850,5850,5850,0],
            "imp_rec":  [0,-12801.57,-110,0,-3746.16,-20803.48,-246,0,0,0,0,0],
            "cpv":      [-46896.47,-12964.95,-37923.72,-20184.77,-18716.91,-11945.51,-12610.03,-16177.31,-8428.23,-9503.88,-17980.06,-39258.48],
            "desp_op":  [-18417.88,-16897.10,-18966.78,-37725.97,-30958.67,-40560.56,-48685.34,-41380.36,-49897.99,-57554.57,-55151.69,-67003.17],
            "res_fin":  [888.03,898.59,1688.75,2690.05,-50.17,246.01,-53.25,67.51,-76.02,208.02,109.74,170.91],
            "ir":       [0,0,0,0,0,0,-283.62,0,0,0,0,0],
            "rec_bdi":  [0.0]*12,
            "desp_bdi": [0.0]*12,
        },
        "SPE Tereza Cristina": {
            "nome":"Brocks Res. Tereza Cristina SPE Ltda","fonte":"Fixo",
            "rec_bruta":[0,0,0,0,116502.27,177669.37,229361.72,287888.54,183157.74,197793.34,170247.63,279232.61],
            "imp_rec":  [-240.62,0,-239.85,0,0,-1264.39,-3173.65,-8310.19,0,-8972.49,-33994.01,-11461.68],
            "cpv":      [-23330.12,-3933.26,-12216.97,-38241.82,-9032.10,-33687.04,-16785.65,-56428.19,-237231.30,-388351.72,-168196.80,-170975.55],
            "desp_op":  [-7939.74,-3222.06,-1267.00,-8833.36,-17957.56,-106891.81,-103047.10,-189069.48,-97506.05,-131413.70,-109800.02,-113582.24],
            "res_fin":  [640.20,-0.01,0,155.70,20.24,0.87,55.31,6500.80,-87.60,-59.41,33.59,239.14],
            "ir":       [0,0,0,0,0,0,-1301.37,0,0,-11327.40,0,0],
            "rec_bdi":  [0.0]*12,
            "desp_bdi": [0.0]*12,
        }
    }}
}

if "clientes" not in st.session_state:
    _saved = _load_state()
    st.session_state.clientes = _saved if _saved is not None else CLIENTES_DEFAULT.copy()

# ── Funções com cache ─────────────────────────────────────────────────────────
@st.cache_data
def calc_dre(rb, imp, cpv, dop, rf, ir):
    rb=np.array(rb,dtype=float); imp=np.array(imp,dtype=float)
    cpv=np.array(cpv,dtype=float); dop=np.array(dop,dtype=float)
    rf=np.array(rf,dtype=float); ir=np.array(ir,dtype=float)
    rl=rb+imp; lb=rl+cpv; ebt=lb+dop; lai=ebt+rf; ll=lai+ir
    return dict(rec_bruta=rb,imp_rec=imp,rec_liq=rl,cpv=cpv,
                lucro_bruto=lb,desp_op=dop,ebitda=ebt,
                res_fin=rf,lucro_antes_ir=lai,ir=ir,lucro_liq=ll)

def dre(emp, rec_override=None):
    rb = rec_override or emp["rec_bruta"]
    return calc_dre(tuple(rb),tuple(emp["imp_rec"]),tuple(emp["cpv"]),
                    tuple(emp["desp_op"]),tuple(emp["res_fin"]),tuple(emp["ir"]))

@st.cache_data
def projeta(rb,imp,cpv_b,dop_b,rf_b,ir_b, g_rec,g_cpv,g_dop,rf_mult,ir_mult):
    rb=np.array(rb,dtype=float); total=rb.sum()
    sazon=rb/total if total!=0 else np.ones(12)/12
    rb_m=total*(1+g_rec/100)*sazon
    irp=np.array(imp).sum()/total if total!=0 else 0
    ir_m=rb_m*irp; rl_m=rb_m+ir_m
    cpv_m=np.array(cpv_b,dtype=float)*(1+g_cpv/100)
    dop_m=np.array(dop_b,dtype=float)*(1+g_dop/100)
    ebt_m=rl_m+cpv_m+dop_m
    rf_m=np.full(12,np.array(rf_b).sum()*rf_mult/12)
    lai_m=ebt_m+rf_m
    ir_m2=np.full(12,np.array(ir_b).sum()*ir_mult/12)
    ll_m=lai_m+ir_m2
    return dict(rec_bruta=rb_m,imp_rec=ir_m,rec_liq=rl_m,cpv=cpv_m,
                lucro_bruto=rl_m+cpv_m,desp_op=dop_m,ebitda=ebt_m,
                res_fin=rf_m,lucro_antes_ir=lai_m,ir=ir_m2,lucro_liq=ll_m)


def get_rolling_state(nome: str) -> dict:
    """
    Retorna o estado do Rolling Forecast para uma empresa.
    Tenta carregar do GitHub se não estiver na memória.
    """
    if "rolling" not in st.session_state:
        st.session_state.rolling = {}

    if nome not in st.session_state.rolling:
        # Tenta carregar do GitHub primeiro
        _loaded = None
        try:
            from github_storage import load_rolling_state
            _loaded = load_rolling_state(nome)
        except Exception:
            pass

        if _loaded is not None:
            # Garante que chaves obrigatórias existam (migração de versões antigas)
            _defaults = {
                "meses_reais":  {},
                "cron_orc":     {},
                "vgv":          {m+1: {"unidades": 0, "preco": 350000.0} for m in range(24)},
                "poc_acum":     [0] * 24,
                "bdi_rate":     14.0,
                "bdi_mensal":   [14.0] * 24,
                "cub_mensal":   0.5,
                "pct_entrada":  7.0,
                "parcela_un":   1500.0,
                "mes_entrega":  12,
                "g_custos":     10.0,
                "data_inicio":  {"ano": 2026, "mes": 1},
                "data_fim":     {"ano": 2027, "mes": 12},
                "historico_cpl": [],
                "vendas":          None,
                "recebiveis":      None,
                "unidades_report": None,
                "total_unidades":  0,
            }
            for _k, _v in _defaults.items():
                if _k not in _loaded:
                    _loaded[_k] = _v
            # Converte chaves numéricas do VGV (JSON salva como string)
            if "vgv" in _loaded and isinstance(_loaded["vgv"], dict):
                _loaded["vgv"] = {int(k): v for k, v in _loaded["vgv"].items()}
            # Converte chaves numéricas dos meses_reais
            if "meses_reais" in _loaded and isinstance(_loaded["meses_reais"], dict):
                _loaded["meses_reais"] = {int(k): v for k, v in _loaded["meses_reais"].items()}
            st.session_state.rolling[nome] = _loaded
        else:
            # Estado inicial padrão
            st.session_state.rolling[nome] = {
                "meses_reais":  {},
                "cron_orc":     {},
                "vgv":          {m+1: {"unidades": 0, "preco": 350000.0} for m in range(24)},
                "poc_acum":     [0] * 24,
                "bdi_rate":     14.0,
                "bdi_mensal":   [14.0] * 24,
                "cub_mensal":   0.5,
                "pct_entrada":  7.0,
                "parcela_un":   1500.0,
                "mes_entrega":  12,
                "g_custos":     10.0,
                "data_inicio":  {"ano": 2026, "mes": 1},
                "data_fim":     {"ano": 2027, "mes": 12},
                "historico_cpl": [],
                "vendas":          None,
                "recebiveis":      None,
                "unidades_report": None,
                "total_unidades":  0,
            }

    return st.session_state.rolling[nome]


def mark_rolling_dirty(nome: str):
    """Marca o estado do rolling como modificado — será salvo no próximo ciclo."""
    st.session_state[f"_rolling_dirty_{nome}"] = True


def save_rolling(nome: str, force: bool = False):
    """
    Salva o estado do Rolling no GitHub APENAS se houver mudança pendente.
    Use force=True para salvar imediatamente (ex: após upload de arquivo).
    """
    _flag = f"_rolling_dirty_{nome}"
    if not force and not st.session_state.get(_flag, False):
        return  # Nada mudou, não salva
    try:
        from github_storage import save_rolling_state
        if "rolling" in st.session_state and nome in st.session_state.rolling:
            save_rolling_state(nome, st.session_state.rolling[nome])
            st.session_state[_flag] = False  # limpa o flag após salvar
    except Exception as e:
        safe_toast(f"Aviso: não foi possível salvar: {e}", "⚠️")


def gen_labels(N, di):
    lbs=[]; m=di["mes"]-1; a=di["ano"]
    for _ in range(N):
        lbs.append(f"{MESES[m%12]}/{str(a)[-2:]}"); m+=1
        if m%12==0: a+=1
    return lbs

def fmt(v):
    s="-" if v<0 else ""; av=abs(v)
    if av>=1_000_000: return f"{s}R$ {av/1_000_000:.2f}M"
    if av>=1_000:     return f"{s}R$ {av/1_000:.1f}K"
    return f"{s}R$ {av:.2f}"

def safe_toast(msg, icon="✅"):
    try: st.toast(msg, icon=icon)
    except: st.success(msg)

def excel_dre(df, sheet="DRE"):
    buf=io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, sheet_name=sheet)
    return buf.getvalue()

def kpi_popover(col, label, valor, delta=None, help_text=None):
    with col:
        if delta: st.metric(label, valor, delta, delta_color="normal")
        else:     st.metric(label, valor)
        if help_text:
            try:
                with st.popover("ℹ️", use_container_width=False):
                    st.caption(help_text)
            except: pass

def estilo_dre(df, totais):
    def hl(row):
        return ([f"background-color:{BLIGHT};font-weight:700;color:{NAVY}"]*len(row)
                if row.name in totais else [""]*len(row))
    def cn(v):
        try: return f"color:{SOFT_RED}" if float(v)<0 else ""
        except: return ""
    try:
        return df.style.format("R$ {:,.2f}").apply(hl,axis=1).map(cn)
    except AttributeError:
        return df.style.format("R$ {:,.2f}").apply(hl,axis=1).applymap(cn)

PL_BASE = dict(template="plotly_white",
               font=dict(family="Inter,sans-serif",size=11,color=TEXT),
               paper_bgcolor=WHITE,plot_bgcolor=WHITE,
               legend=dict(orientation="h",y=-0.25),
               margin=dict(t=40,b=10,l=5,r=5))

def PL(h=340): return {**PL_BASE,"height":h}

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("**Brocks Empreendimentos | Finanças**")

    # ── Info do usuário logado ─────────────────────────────────────────
    if _users_configured():
        _role_label = "🔑 Admin" if _IS_ADMIN else "👁️ Visualizador"
        _u1, _u2 = st.columns([3, 1])
        _u1.markdown(f"**{_USERNAME}** · {_role_label}")
        if _u2.button("Sair", key="_logout_btn", help="Encerrar sessão"):
            for k in ["_logged_in", "_username", "_role", "_sims"]:
                st.session_state.pop(k, None)
            st.rerun()
        st.divider()

    cliente_sel = "Brocks Empreendimentos"

    empresas_cliente = st.session_state.clientes[cliente_sel]["empresas"]

    # Inicializa estado ativo/inativo de cada empresa
    if "empresas_ativas" not in st.session_state:
        st.session_state["empresas_ativas"] = {k: True for k in empresas_cliente}
    # Garante que novas empresas apareçam como ativas
    for k in empresas_cliente:
        if k not in st.session_state["empresas_ativas"]:
            st.session_state["empresas_ativas"][k] = True

    st.markdown("**🏢 Empresa**")
    for k, emp in empresas_cliente.items():
        c_tog, c_nome = st.columns([1, 5])
        ativa = st.session_state["empresas_ativas"].get(k, True)
        with c_tog:
            novo = st.checkbox("a", value=ativa, key=f"_tog_{k}", label_visibility="collapsed")
            if novo != ativa:
                st.session_state["empresas_ativas"][k] = novo
                st.rerun()
        with c_nome:
            if ativa:
                st.caption(f"{'🟢' if emp.get('fonte','Fixo')!='Fixo' else '⚪'} {k}")
            else:
                st.caption(f"⬜ ~~{k}~~")

    # Filtra apenas empresas ativas para cálculos e seletor
    empresas_cliente = {k: v for k, v in empresas_cliente.items()
                        if st.session_state["empresas_ativas"].get(k, True)}
    # Empresa selecionada = a única ativa, ou "Consolidado" se mais de uma
    _todas_empresas = list(st.session_state.clientes[cliente_sel]["empresas"].keys())
    _ativas_check = [
        k for k in _todas_empresas
        if st.session_state.get("empresas_ativas", {}).get(k, True)
    ]
    if len(_ativas_check) == 1:
        empresa_sel = _ativas_check[0]
    else:
        empresa_sel = "Consolidado"

    st.divider()
    st.markdown("**📊 Visão de Receita**")
    try:
        visao = st.pills("Visão de Receita",
                         ["💰 Caixa","📋 Competência","🏗️ POC"],
                         default="💰 Caixa",
                         key="visao_sel",
                         label_visibility="collapsed")
        visao = visao or "💰 Caixa"
    except:
        visao = st.radio("Visão",[
            "💰 Caixa (realizado)","📋 Competência (vendas)","🏗️ POC (% avanço físico)"
        ], key="visao_sel", label_visibility="collapsed")

    rec_override_map = {}  # Receita configurada na aba Configurações

    st.divider()
    # ── DREs já no sistema ─────────────────────────────────────────────
    st.markdown("**📋 DREs no sistema:**")
    for _k, _emp in list(empresas_cliente.items()):
        _fonte = _emp.get("fonte","Fixo")
        _ic    = "☁️" if _fonte=="Upload" else "📊"
        _cn1, _cn2 = st.columns([5,1])
        _cn1.caption(f"{_ic} {_emp.get('nome',_k)}")
        if _fonte=="Upload":
            if _cn2.button("🗑️", key=f"rm_dre_{_k}",
                           help="Remover esta DRE do sistema"):
                del st.session_state.clientes[cliente_sel]["empresas"][_k]
                save_state(); safe_toast(f"{_k} removida.", "🗑️"); st.rerun()
        else:
            _cn2.caption("🔒")  # DREs padrão, não removíveis
    st.divider()
    # ── Adicionar / atualizar DRE ──────────────────────────────────────
    with st.expander("➕ Adicionar / atualizar DRE", expanded=False):
        st.caption("Arquivo Excel exportado do SIENGE — Demonstrativo de Resultado.")
        uploaded=st.file_uploader("",type=["xlsx","xls"],
                                  label_visibility="collapsed",
                                  key="uploader_dre")
        if uploaded:
            bdata=uploaded.read()
            res = parse_sienge(bdata)
            if "erro" in res:
                safe_toast(res["erro"],"❌")
            else:
                nome_in=st.text_input("Nome no sistema:",
                                      value=res["nome"][:40])
                dest_op=list(st.session_state.clientes.keys())+\
                        ["+ Novo cliente"]
                dest = "Brocks Empreendimentos"  # destino fixo — sem selectbox

                @st.dialog("👁️ Preview — DRE", width="large")
                def _modal_preview(df):
                    st.dataframe(df.style.format("R$ {:,.2f}"),
                                 use_container_width=True,height=500)
                if st.button("🔍 Ampliar Preview",
                             use_container_width=True):
                    _modal_preview(res["preview"])
                st.info(
                    "O **✕** ao lado do arquivo acima apenas limpa o "
                    "seletor — não remove o que já foi **Aplicado**.\n\n"
                    "Ao aplicar, a DRE fica salva na lista acima.",
                    icon="ℹ️")
                cc,cx=st.columns(2)
                with cc:
                    if st.button("✅ Aplicar",type="primary",
                                 use_container_width=True):
                        d=res["dados"]
                        nova={"nome":nome_in,"fonte":"Upload",
                              "rec_bruta":d["rec_bruta"],
                              "imp_rec":d["imp_rec"],
                              "cpv":d["cpv"],"desp_op":d["desp_op"],
                              "res_fin":d["res_fin"],"ir":d["ir"],
                              "rec_bdi": d.get("rec_bdi",  [0.0]*12),
                              "desp_bdi":d.get("desp_bdi", [0.0]*12),
                              "raw_lines": _parse_sienge_full(bdata)}
                        if dest=="+ Novo cliente":
                            st.session_state.clientes[nome_in]=\
                                {"empresas":{nome_in:nova}}
                        else:
                            st.session_state.clientes[dest]\
                                ["empresas"][nome_in]=nova
                        save_state()
                        safe_toast(
                            f"✅ {nome_in} salva no sistema!","✅")
                        st.rerun()
                with cx:
                    if st.button("❌ Cancelar",
                                 use_container_width=True): st.rerun()

    st.divider()
    st.divider()
    with st.expander("🏗️ Nova SPE"):
        st.caption(
            "Adicione um novo empreendimento ao sistema. "
            "Após criar, acesse a aba ⚙️ Configurações para subir o CFF, CPL e DRE."
        )
        _novo_spe_nome = st.text_input(
            "Nome da SPE:",
            key="novo_spe_nome",
            placeholder="Ex: SPE Residencial João XXIII"
        )
        _novo_spe_cnpj = st.text_input(
            "CNPJ (opcional):",
            key="novo_spe_cnpj",
            placeholder="00.000.000/0001-00"
        )
        if st.button("➕ Criar SPE", use_container_width=True, type="primary"):
            _nome = _novo_spe_nome.strip()
            if _nome:
                if _nome not in st.session_state.clientes[cliente_sel]["empresas"]:
                    # Estrutura padrão de uma nova SPE
                    st.session_state.clientes[cliente_sel]["empresas"][_nome] = {
                        "nome":     _nome,
                        "cnpj":     _novo_spe_cnpj.strip(),
                        "fonte":    "Upload",
                        "rec_bruta": [0.0]*12,
                        "imp_rec":   [0.0]*12,
                        "cpv":       [0.0]*12,
                        "desp_op":   [0.0]*12,
                        "res_fin":   [0.0]*12,
                        "ir":        [0.0]*12,
                        "rec_bdi":   [0.0]*12,
                        "desp_bdi":  [0.0]*12,
                        "raw_lines": [],
                    }
                    save_state()
                    safe_toast(f"SPE '{_nome}' criada! Acesse ⚙️ Configurações para carregar os dados.", "✅")
                    st.rerun()
                else:
                    safe_toast(f"Já existe uma SPE com o nome '{_nome}'.", "⚠️")
            else:
                safe_toast("Digite o nome da SPE.", "⚠️")




    # ── Simulações ────────────────────────────────────────────────────────
    st.markdown("**📋 Simulações**")
    _MAX_SIMS = 5 if _IS_ADMIN else 3
    _sims_atual = st.session_state.get("_sims", [])
    _nomes_sims = [s["nome"] for s in _sims_atual]

    if _sims_atual:
        _sel_sim = st.selectbox("Selecionar simulação", _nomes_sims,
                                key="_sel_sim", label_visibility="collapsed")
        _c_load, _c_del = st.columns(2)
        if _c_load.button("📂 Carregar", use_container_width=True, key="_btn_load_sim"):
            _sim_data = next((s for s in _sims_atual if s["nome"] == _sel_sim), None)
            if _sim_data:
                _apply_sim_params(_sim_data.get("params", {}))
                safe_toast(f"Simulação '{_sel_sim}' carregada!", "📂")
                st.rerun()
        if _c_del.button("🗑️ Deletar", use_container_width=True, key="_btn_del_sim"):
            st.session_state["_sims"] = [s for s in _sims_atual if s["nome"] != _sel_sim]
            _save_sims(_USERNAME, st.session_state["_sims"])
            safe_toast(f"Simulação '{_sel_sim}' deletada.", "🗑️")
            st.rerun()
    else:
        st.caption("Nenhuma simulação salva.")

    _nome_nova = st.text_input("Nome da simulação:", key="_nome_sim",
                                placeholder="Ex: Cenário Conservador",
                                label_visibility="collapsed")
    if st.button("💾 Salvar Simulação", use_container_width=True, key="_btn_save_sim"):
        if not _nome_nova.strip():
            st.warning("Digite um nome para a simulação.")
        else:
            _params_agora = _get_sim_params()
            _nova_sim = {
                "nome":   _nome_nova.strip(),
                "data":   __import__("datetime").date.today().isoformat(),
                "params": _params_agora,
            }
            _lista = list(st.session_state.get("_sims", []))
            # Remove simulação com mesmo nome, se existir
            _lista = [s for s in _lista if s["nome"] != _nova_sim["nome"]]
            # Viewer: máx 3, substitui mais antiga ao exceder
            if not _IS_ADMIN and len(_lista) >= _MAX_SIMS:
                _lista = sorted(_lista, key=lambda s: s.get("data",""), reverse=True)
                _lista = _lista[:_MAX_SIMS - 1]
            # Admin: máx 5
            if _IS_ADMIN and len(_lista) >= _MAX_SIMS:
                st.warning(f"Limite de {_MAX_SIMS} simulações atingido. Delete uma antes de salvar.")
            else:
                _lista.append(_nova_sim)
                st.session_state["_sims"] = _lista
                _save_sims(_USERNAME, _lista)
                safe_toast(f"Simulação '{_nova_sim['nome']}' salva!", "💾")
                st.rerun()

    # Admin: Salvar como Padrão
    if _IS_ADMIN:
        if st.button("💾 Salvar como Padrão", use_container_width=True, key="_btn_save_padrao",
                     help="Salva a configuração atual como padrão para todos os Visualizadores"):
            _save_config_padrao(_get_sim_params())
            safe_toast("Configuração salva como padrão!", "✅")

    # Viewer: Restaurar Padrão
    if not _IS_ADMIN and _users_configured():
        if st.button("🔄 Restaurar Padrão", use_container_width=True, key="_btn_restaurar",
                     help="Volta para a configuração padrão definida pelo Admin"):
            _cfg = _load_config_padrao() or _load_config_padrao_local()
            if _cfg:
                _apply_sim_params(_cfg)
                safe_toast("Padrão restaurado!", "🔄")
                st.rerun()
            else:
                st.info("Nenhum padrão definido pelo Admin ainda.")

    st.divider()
    st.caption("Brocks Empreendimentos Ltda © 2026")

# ── Cálculos base ─────────────────────────────────────────────────────────────
dres={k:dre(emp,rec_override_map.get(k)) for k,emp in empresas_cliente.items()}
if not dres:
    st.info("Nenhuma empresa. Adicione via sidebar."); st.stop()

if empresa_sel=="Consolidado":
    chaves=list(dres.keys())
    final={key:sum(dres[k][key] for k in chaves) for key in dres[chaves[0]]}
    titulo=f"{cliente_sel} — Consolidado"; emp_base=list(empresas_cliente.values())[0]
    final["rec_bdi"]  = sum(np.array(emp.get("rec_bdi",  [0.0]*12)) for emp in empresas_cliente.values())
    final["desp_bdi"] = sum(np.array(emp.get("desp_bdi", [0.0]*12)) for emp in empresas_cliente.values())
else:
    final=dres[empresa_sel]; titulo=empresas_cliente[empresa_sel]["nome"]
    emp_base=empresas_cliente[empresa_sel]
    final["rec_bdi"]  = np.array(emp_base.get("rec_bdi",  [0.0]*12))
    final["desp_bdi"] = np.array(emp_base.get("desp_bdi", [0.0]*12))

rb_t=float(final["rec_bruta"].sum()); rl_t=float(final["rec_liq"].sum())
lb_t=float(final["lucro_bruto"].sum()); ebt_t=float(final["ebitda"].sum())
ll_t=float(final["lucro_liq"].sum());  cpv_t=float(final["cpv"].sum())
mg_b=(lb_t/rl_t*100) if rl_t!=0 else 0
mg_e=(ebt_t/rb_t*100) if rb_t!=0 else 0
mg_l=(ll_t/rb_t*100)  if rb_t!=0 else 0

# ── Navegação ─────────────────────────────────────────────────────────────────
TABS=[
    "⚙️ Configurações",
    "📊 DRE Analítica",
    "🏗️ Resumo de Obras",
    "📅 Rolling Forecast",
    "📐 Indicadores",
    "🎯 Sensibilidade",
    "💰 FCFF & DCF",
]
if "tab_ativo" not in st.session_state or st.session_state.tab_ativo not in TABS:
    st.session_state.tab_ativo=TABS[0]

_abas_cols = st.columns(len(TABS))
for col,nome in zip(_abas_cols,TABS):
    tipo="primary" if st.session_state.tab_ativo==nome else "secondary"
    if col.button(nome,use_container_width=True,type=tipo,key=f"btn_{nome}"):
        st.session_state.tab_ativo=nome; st.rerun()
st.divider()
_tab=st.session_state.tab_ativo

# ══════════════════════════════════════════════════════════════════════ TAB 1
@st.fragment
def render_dre():
    c_title, c_year = st.columns([3, 1])
    with c_title:
        st.markdown(f"## 📊 {titulo}")
    with c_year:
        ano_analise = st.selectbox("Ano de Análise:", [2024, 2025, 2026, 2027], index=1)
    
    st.caption(f"Demonstrativo de Resultado Analítico · Jan–Dez {ano_analise} · Visão: {visao.split('(')[0].strip()}")
    st.divider()
    k1, k2, k3 = st.columns(3)
    kpi_popover(k1, "Receita Líquida", fmt(rl_t),
                help_text="Receita Bruta − Impostos sobre receita (PIS, COFINS, ISS, etc.)")
    _desp_op_t = float(final["desp_op"].sum())
    kpi_popover(k2, "Despesas Operacionais", fmt(_desp_op_t),
                help_text="Despesas administrativas, comerciais e gerais do período.")
    kpi_popover(k3, "Lucro Líquido", fmt(ll_t), f"{mg_l:+.1f}% Mg Líquida",
                help_text="Resultado final após todas as deduções, incluindo IR/CSLL.")
    st.divider()

    c1,c2=st.columns(2)
    with c1:
        f1=go.Figure()
        f1.add_bar(x=MESES,y=final["rec_bruta"],name="Rec. Bruta", marker_color=CHART_BLUE)
        f1.add_bar(x=MESES,y=final["rec_liq"],  name="Rec. Líquida",marker_color=CHART_NAVY)
        f1.update_layout(title="Receita Mensal",barmode="group",**PL())
        f1.update_xaxes(showgrid=False); f1.update_yaxes(gridcolor=BORDER,tickprefix="R$ ",tickformat=",.0f")
        st.plotly_chart(f1,use_container_width=True)
    with c2:
        cores=[CHART_BLUE if v>=0 else SOFT_RED for v in final["ebitda"]]
        f2=go.Figure()
        f2.add_bar(x=MESES,y=final["ebitda"],name="EBITDA",marker_color=cores,opacity=0.85)
        f2.add_scatter(x=MESES,y=final["lucro_liq"],name="Lucro Líquido",
                       mode="lines+markers",line=dict(color=GOLD,width=2.5),marker=dict(size=7))
        f2.update_layout(title="EBITDA e Lucro Líquido",**PL())
        f2.update_xaxes(showgrid=False); f2.update_yaxes(gridcolor=BORDER,tickprefix="R$ ",tickformat=",.0f")
        st.plotly_chart(f2,use_container_width=True)

    c3,c4=st.columns([3,2])
    with c3:
        f3=go.Figure()
        f3.add_bar(x=MESES,y=final["rec_liq"],name="Rec. Líquida",marker_color=CHART_NAVY,opacity=0.85)
        f3.add_bar(x=MESES,y=final["desp_op"],name="Desp. Op.",marker_color=CHART_BLUE,opacity=0.75)
        f3.add_scatter(x=MESES,y=final["ebitda"],name="EBITDA",mode="lines+markers",
                       line=dict(color=GOLD,width=2.5),marker=dict(size=7,color=GOLD))
        f3.add_hline(y=0,line_dash="dash",line_color=GRAY,line_width=1)
        f3.update_layout(title="Composição Mensal",barmode="relative",**PL(420))
        f3.update_xaxes(showgrid=False)
        f3.update_yaxes(gridcolor=BORDER,tickprefix="R$ ",tickformat=",.0f")
        st.plotly_chart(f3,use_container_width=True)
    with c4:
        me=np.clip(np.where(final["rec_bruta"]!=0,final["ebitda"]/final["rec_bruta"]*100,np.nan),-150,150)
        ml=np.clip(np.where(final["rec_bruta"]!=0,final["lucro_liq"]/final["rec_bruta"]*100,np.nan),-150,150)
        f4=go.Figure()
        f4.add_scatter(x=MESES,y=me,name="Mg EBITDA",mode="lines+markers",
                       line=dict(color=CHART_BLUE,width=2.5),marker=dict(size=6))
        f4.add_scatter(x=MESES,y=ml,name="Mg Líquida",mode="lines+markers",
                       line=dict(color=GOLD,width=2.5),marker=dict(size=6))
        f4.add_hline(y=0,line_dash="dash",line_color=GRAY,line_width=1)
        f4.update_layout(title="Margens Mensais (%)",**PL(420))
        f4.update_yaxes(ticksuffix="%",gridcolor=BORDER,range=[-160,60])
        f4.update_xaxes(showgrid=False)
        st.plotly_chart(f4,use_container_width=True)

    st.divider()
    st.markdown(f"#### 📋 DRE Detalhada — {titulo}")
    linhas=[("(=) Receita Bruta","rec_bruta",True),("   ↳ Receita de BDI","rec_bdi",False),
            ("(-) Impostos s/ Receita","imp_rec",False),
            ("(=) Receita Líquida","rec_liq",True),("(-) CPV / CSP","cpv",False),
            ("(=) Lucro Bruto","lucro_bruto",True),("(-) Despesas Operacionais","desp_op",False),
            ("   ↳ Despesa de BDI","desp_bdi",False),
            ("(=) EBITDA","ebitda",True),("(+/-) Resultado Financeiro","res_fin",False),
            ("(=) Lucro antes IR","lucro_antes_ir",True),("(-) IR / CSLL","ir",False),
            ("(=) Lucro Líquido","lucro_liq",True)]
    tots={l for l,_,t in linhas if t}
    rows=[]
    for label,key,_ in linhas:
        row={"Linha DRE":label}
        for i,m in enumerate(MESES): row[m]=final[key][i]
        row["TOTAL"]=float(final[key].sum()); rows.append(row)
    df_t1=pd.DataFrame(rows).set_index("Linha DRE")

    # data_editor (editável) com fallback para dataframe
    try:
        edited=st.data_editor(estilo_dre(df_t1,tots),use_container_width=True,height=510,
                              disabled=True)
    except:
        st.dataframe(estilo_dre(df_t1,tots),use_container_width=True,height=510)

    # Download
    st.download_button("📥 Exportar DRE em Excel",
                       data=excel_dre(df_t1,"DRE 2025"),
                       file_name=f"DRE_2025_{titulo.replace(' ','_')}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ══════════════════════════════════════════════════════════════════════ TAB 2
@st.fragment
def render_resumo_obras():
    import datetime
    MESES_NOME_MAP = {i+1:m for i,m in enumerate(MESES)}

    # ── Lista de SPEs disponíveis ─────────────────────────────────────
    _todas_empresas = list(st.session_state.clientes[cliente_sel]["empresas"].keys())
    _spes_ativas = [
        k for k in _todas_empresas
        if st.session_state.get("empresas_ativas", {}).get(k, True)
        and "matriz" not in k.lower()
    ]
    if not _spes_ativas:
        st.warning("Nenhuma SPE ativa. Ative ao menos uma empresa na sidebar.")
        return

    _roll_emp_key = "_rolling_empresa_sel"

    # Sincroniza com sidebar se SPE específica selecionada
    if empresa_sel in _spes_ativas:
        st.session_state[_roll_emp_key] = empresa_sel
    elif st.session_state.get(_roll_emp_key) not in _spes_ativas:
        st.session_state[_roll_emp_key] = _spes_ativas[0]

    _empresa_roll = st.session_state.get(_roll_emp_key, _spes_ativas[0])

    # ── Botões visuais de seleção de SPE ─────────────────────────────
    st.markdown("**🏢 Selecione a obra:**")
    _col_spes = st.columns(min(len(_spes_ativas), 3))
    for _i, _spe_k in enumerate(_spes_ativas):
        with _col_spes[_i % 3]:
            _is_sel = (_spe_k == _empresa_roll)
            _emp_d  = st.session_state.clientes[cliente_sel]["empresas"][_spe_k]
            _tit_k  = _emp_d.get("nome", _spe_k)
            _est_k  = get_rolling_state(_tit_k)
            _tem_cron_spe = "cronograma" in _est_k
            _icone  = "✅" if _tem_cron_spe else "⬜"
            if st.button(
                f"{_icone} {_spe_k}",
                key=f"_roll_btn_{_spe_k}",
                type="primary" if _is_sel else "secondary",
                use_container_width=True,
                help="Cronograma carregado" if _tem_cron_spe else "Sem cronograma"
            ):
                st.session_state[_roll_emp_key] = _spe_k
                st.rerun()

    # ── Modo Consolidado ──────────────────────────────────────────────
    if empresa_sel == "Consolidado" and len(_spes_ativas) > 1:
        st.divider()
        st.markdown("### 📊 Consolidado das Obras")

        _rows_cons = []
        _tot_orc = _tot_med = _tot_real = _tot_comp = _tot_verba = 0.0

        for _sk in _spes_ativas:
            _ed  = st.session_state.clientes[cliente_sel]["empresas"][_sk]
            _tk  = _ed.get("nome", _sk)
            _ek  = get_rolling_state(_tk)
            _hk  = _ek.get("historico_cpl", [])
            _snk = _hk[-1] if _hk else {}
            _crk = _ek.get("cronograma", {})

            _orc  = _snk.get("orcado_total", 0.0)
            _med  = _snk.get("medido_acum", 0.0)
            _real = _snk.get("realizado_acum", 0.0)
            _comp = _snk.get("comprometido", 0.0)
            _verb = _snk.get("verba_disponivel", 0.0)
            _cpik = _snk.get("cpi", 0.0)
            _eack = _snk.get("eac", 0.0)

            # SPI
            _spik = 1.0
            _pf_k = _snk.get("periodo_final","")
            if _crk and _pf_k:
                try:
                    _mes_k = int(_pf_k[5:7]); _ano_k = int(_pf_k[:4])
                    _acum_k = 0.0; _tot_k = _crk.get("total_obra",1)
                    for _mi,_mv in zip(_crk.get("meses",[]),_crk.get("custos_por_mes",[])):
                        _acum_k += _mv
                        if _mi["mes"]==_mes_k and _mi["ano"]==_ano_k: break
                    _ppl_k = (_acum_k/_tot_k*100) if _tot_k>0 else 0
                    _pmd_k = _snk.get("pct_medido",0)
                    _spik  = (_pmd_k/_ppl_k) if _ppl_k>0 else 1.0
                except Exception:
                    _spik = 1.0

            # Datas
            _di_k = _crk.get("data_inicio", _ek.get("data_inicio",{"ano":2024,"mes":1}))
            _df_k = _crk.get("data_fim",    _ek.get("data_fim",   {"ano":2027,"mes":12}))
            import datetime as _dttc
            _hoje_c = _dttc.date.today()
            _fim_c  = _dttc.date(_df_k["ano"], _df_k["mes"], 1)
            _rest_k = max((_fim_c.year-_hoje_c.year)*12+(_fim_c.month-_hoje_c.month),0)

            _tot_orc  += _orc
            _tot_med  += _med
            _tot_real += _real
            _tot_comp += _comp
            _tot_verba+= _verb

            _sem_spi = "🟢" if _spik>=0.95 else "🟡" if _spik>=0.85 else "🔴"
            _sem_cpi = "🟢" if _cpik>=0.95 else "🟡" if _cpik>=0.85 else "🔴"
            _pf_fmt  = f"{MESES[int(_pf_k[5:7])-1]}/{_pf_k[:4]}" if _pf_k else "—"

            _rows_cons.append({
                "Obra":          _sk,
                "Fim":           f"{MESES[_df_k['mes']-1]}/{_df_k['ano']}",
                "Restam":        f"{_rest_k} meses",
                "Orçado":        _orc,
                "Medido":        _med,
                "% Físico":      f"{_snk.get('pct_medido',0):.1f}%",
                "Realizado":     _real,
                "Verba Disp.":   _verb,
                f"{_sem_spi} SPI": f"{_spik:.3f}",
                f"{_sem_cpi} CPI": f"{_cpik:.3f}" if _cpik else "—",
                "CPL":           _pf_fmt,
            })

        if _rows_cons:
            _df_c = pd.DataFrame(_rows_cons)
            _fmt_c = {
                "Orçado":    "R$ {:,.0f}",
                "Medido":    "R$ {:,.0f}",
                "Realizado": "R$ {:,.0f}",
                "Verba Disp.":"R$ {:,.0f}",
            }
            try:
                st.dataframe(
                    _df_c.style.format(_fmt_c),
                    use_container_width=True, hide_index=True
                )
            except Exception:
                st.dataframe(_df_c, use_container_width=True, hide_index=True)

            st.divider()
            _cc1,_cc2,_cc3,_cc4 = st.columns(4)
            _cc1.metric("Total Orçado",     fmt(_tot_orc))
            _cc2.metric("Total Medido",      fmt(_tot_med))
            _cc3.metric("Total Realizado",   fmt(_tot_real))
            _cc4.metric("Total Verba Disp.", fmt(_tot_verba))

        st.divider()
        st.info("💡 Selecione uma SPE específica na sidebar para ver o detalhe da obra.")
        return

    _empresa_roll = st.session_state.get(_roll_emp_key, _spes_ativas[0])
    titulo = st.session_state.clientes[cliente_sel]["empresas"][_empresa_roll].get(
        "nome", _empresa_roll
    )

    _tem_cff_cap = "cronograma" in get_rolling_state(titulo)
    _tem_cpl_cap = bool(get_rolling_state(titulo).get("historico_cpl"))
    if _tem_cff_cap and _tem_cpl_cap:
        _status_cap = "✅ CFF e CPL carregados"
    elif _tem_cff_cap:
        _status_cap = "✅ CFF carregado · ⬜ Sem CPL"
    elif _tem_cpl_cap:
        _status_cap = "⬜ Sem CFF · ✅ CPL carregado"
    else:
        _status_cap = "⬜ Sem dados — configure na aba ⚙️ Configurações"

    st.caption(f"Visualizando: **{_empresa_roll}** · {_status_cap}")
    st.divider()

    st.markdown(f"## 🏗️ Resumo de Obras — {_empresa_roll}")
    st.caption("Cronograma físico-financeiro, Custo por Nível e indicadores EVM da obra.")
    st.divider()

    estado = get_rolling_state(titulo)
    _tkey = re.sub(r"\W+","_",titulo)

    # Sem sub-abas — Resumo de Obras é apenas visual
    _sub_aba = "📊 Resultados"



    # ── Período automático baseado no CFF ─────────────────────────────
    _cr_tmp = estado.get("cronograma", {})
    if _cr_tmp:
        _di_auto = _cr_tmp.get("data_inicio", {"ano": 2024, "mes": 1})
        _df_auto = _cr_tmp.get("data_fim",    {"ano": 2026, "mes": 12})
        estado["data_inicio"] = _di_auto
        estado["data_fim"]    = _df_auto
    else:
        if "data_fim" not in estado:
            estado["data_fim"] = {"ano": 2027, "mes": 12}

    import datetime
    _inicio = datetime.date(estado["data_inicio"]["ano"], estado["data_inicio"]["mes"], 1)
    _fim    = datetime.date(estado["data_fim"]["ano"],    estado["data_fim"]["mes"],    1)
    N = (_fim.year - _inicio.year) * 12 + (_fim.month - _inicio.month) + 1
    if N < 1: N = 1
    if N > 120: N = 120
    LABELS = gen_labels(N, estado["data_inicio"])

    is_matriz = "matriz" in titulo.lower()

    st.caption(
        f"🗓️ Período da obra: **{MESES[estado['data_inicio']['mes']-1]}/{estado['data_inicio']['ano']}** → "
        f"**{MESES[estado['data_fim']['mes']-1]}/{estado['data_fim']['ano']}** · {N} meses"
        + (" *(automático do CFF)*" if _cr_tmp else " *(configure o CFF para atualizar)*")
    )

    # ── Prepara dados para Resultados ────────────────────────────────
    _hist_cpl  = estado.get("historico_cpl", [])
    _cpl_atual = _hist_cpl[-1] if _hist_cpl else {}   # snapshot mais recente
    _cpl_ant   = _hist_cpl[-2] if len(_hist_cpl) >= 2 else {}  # mês anterior

    _tem_cff   = "cronograma" in estado
    _tem_cpl   = bool(_cpl_atual)
    _cr        = estado.get("cronograma", {})

    # KPIs do CPL atual
    _orcado        = _cpl_atual.get("orcado_total", 0.0)
    _medido        = _cpl_atual.get("medido_acum", 0.0)
    _realizado     = _cpl_atual.get("realizado_acum", 0.0)
    _comprometido  = _cpl_atual.get("comprometido", 0.0)
    _verba_disp    = _cpl_atual.get("verba_disponivel", 0.0)
    _saldo_ctp     = _cpl_atual.get("saldo_ctp", 0.0)
    _cpi           = _cpl_atual.get("cpi", 1.0)
    _eac           = _cpl_atual.get("eac", 0.0)
    _custo_min     = _cpl_atual.get("custo_minimo", 0.0)
    _pct_medido    = _cpl_atual.get("pct_medido", 0.0)
    _pct_realizado = _cpl_atual.get("pct_realizado", 0.0)
    _tem_medicao   = _cpl_atual.get("tem_medicao", False)

    # % planejado no período do CPL (do CFF, linha Total da obra acumulado)
    _pct_planejado = 0.0
    if _tem_cff and _cpl_atual.get("periodo_final"):
        # Encontra o mês do CFF que corresponde ao período final do CPL
        try:
            _pf = _cpl_atual["periodo_final"]  # "AAAA-MM-DD"
            _mes_pf = int(_pf[5:7])
            _ano_pf = int(_pf[:4])
            _custos = _cr.get("custos_por_mes", [])
            _meses  = _cr.get("meses", [])
            _acum_plan = 0.0
            _total_cff = _cr.get("total_obra", _orcado)
            for _mi, _mv in zip(_meses, _custos):
                _acum_plan += _mv
                if _mi["mes"] == _mes_pf and _mi["ano"] == _ano_pf:
                    break
            _pct_planejado = (_acum_plan / _total_cff * 100) if _total_cff > 0 else 0.0
        except Exception:
            _pct_planejado = 0.0

    # SPI
    _spi = (_pct_medido / _pct_planejado) if _pct_planejado > 0 else 1.0

    # Variações vs mês anterior
    def _delta(campo):
        atual = _cpl_atual.get(campo, 0.0)
        ant   = _cpl_ant.get(campo, 0.0)
        return atual - ant if ant else None

    # Semáforo
    def _semaforo_spi(v):
        if v >= 0.95: return "🟢"
        if v >= 0.85: return "🟡"
        return "🔴"

    def _semaforo_cpi(v):
        if v >= 0.95: return "🟢"
        if v >= 0.85: return "🟡"
        return "🔴"

    def _semaforo_verba(v_pct):
        if v_pct >= 20: return "🟢"
        if v_pct >= 10: return "🟡"
        return "🔴"

    def _semaforo_eac(eac, orc):
        if orc == 0: return "🟢"
        pct = (eac - orc) / orc * 100
        if pct <= 3:  return "🟢"
        if pct <= 8:  return "🟡"
        return "🔴"

    if _sub_aba == "📊 Resultados":
        import datetime as _dt

        if not _tem_cff and not _tem_cpl:
            st.info(
                "ℹ️ Nenhum dado carregado para esta obra.\n\n"
                "Acesse a aba **⚙️ Configurações** para:\n"
                "- Subir o Cronograma Físico/Financeiro (CFF)\n"
                "- Subir o Custo por Nível (CPL)"
            )
            return

        # ── CABEÇALHO DA OBRA ─────────────────────────────────────────
        _obra_nome = _cr.get("obra_nome") or _cpl_atual.get("obra_nome") or _empresa_roll
        _inicio_str = f"{MESES[estado['data_inicio']['mes']-1]}/{estado['data_inicio']['ano']}"
        _fim_str    = f"{MESES[estado['data_fim']['mes']-1]}/{estado['data_fim']['ano']}"
        _hoje = _dt.date.today()
        _fim_dt = _dt.date(estado["data_fim"]["ano"], estado["data_fim"]["mes"], 1)
        _meses_rest = 0  # calculado abaixo, após _meses_pass

        st.markdown(f"### 🏗️ {_obra_nome}")
        _periodo_cpl_str = ""
        if _cpl_atual.get("periodo_final"):
            _pf = _cpl_atual["periodo_final"]
            try:
                _pf_fmt = f"{MESES[int(_pf[5:7])-1]}/{_pf[:4]}"
            except Exception:
                _pf_fmt = _pf
            _periodo_cpl_str = f" · 📊 CPL: **{_pf_fmt}**"
        st.caption(
            f"📍 {_inicio_str} → {_fim_str} · **{_meses_rest} meses restantes**"
            f"{_periodo_cpl_str}"
        )

        # Barra de progresso do cronograma
        import datetime as _dtt
        _hoje_dt   = _dtt.date.today()
        _inicio_dt = _dtt.date(estado["data_inicio"]["ano"], estado["data_inicio"]["mes"], 1)
        _fim_dt2   = _dtt.date(estado["data_fim"]["ano"],    estado["data_fim"]["mes"],    1)
        _total_dias = max((_fim_dt2 - _inicio_dt).days, 1)
        _dias_pass  = max((_hoje_dt - _inicio_dt).days, 0)
        _pct_prazo  = min(_dias_pass / _total_dias * 100, 100)
        # +1 para manter consistência com N (contagem inclusiva de meses)
        _meses_pass = max(
            (_hoje_dt.year - _inicio_dt.year) * 12 +
            (_hoje_dt.month - _inicio_dt.month) + 1, 1
        )
        _meses_rest = max(N - _meses_pass, 0)

        _pc1, _pc2 = st.columns([3, 1])
        with _pc1:
            st.caption(f"⏱️ Prazo da obra: {_pct_prazo:.0f}% decorrido")
            st.progress(min(_pct_prazo / 100, 1.0))
        with _pc2:
            st.caption(
                f"**{_meses_pass}** meses passados\n\n"
                f"**{_meses_rest}** meses restantes"
            )

        # Alerta se prazo decorrido > % físico medido
        if _tem_cpl and _pct_medido > 0:
            _diff_prazo_fisico = _pct_prazo - _pct_medido
            if _diff_prazo_fisico > 10:
                st.warning(
                    f"⚠️ **Atenção ao prazo:** {_pct_prazo:.0f}% do tempo decorrido "
                    f"vs {_pct_medido:.1f}% de avanço físico — "
                    f"diferença de **{_diff_prazo_fisico:.0f} pp**."
                )

        # Aviso de confiabilidade (< 30% de avanço)
        if _tem_cpl and _pct_medido < 30:
            st.warning(
                f"⚠️ Avanço físico atual: **{_pct_medido:.1f}%** — "
                f"Com menos de 30% de execução, EAC e projeções têm baixa confiabilidade.",
                icon="⚠️"
            )

        st.divider()

        # ══════════════════════════════════════════════════════════════
        # CURVA S — GRÁFICO PRINCIPAL
        # ══════════════════════════════════════════════════════════════
        st.markdown("### 📈 Curva S — Progresso da Obra")
        st.caption(
            "Planejado (azul tracejado) · Medido (verde) · Realizado (laranja) · "
            "% acumulado do orçamento total"
        )

        _fg_s = go.Figure()

        # Linha Planejado (do CFF)
        if _tem_cff and _cr.get("custos_por_mes"):
            _custos = _cr["custos_por_mes"]
            _meses_cff = _cr.get("meses", [])
            _total_cff = _cr.get("total_obra", 1)
            _labels_cff = [
                f"{MESES[_m['mes']-1]}/{str(_m['ano'])[-2:]}"
                for _m in _meses_cff
            ]
            _acum_plan = []
            _soma = 0.0
            for _v in _custos:
                _soma += _v
                _acum_plan.append(_soma / _total_cff * 100)
            _fg_s.add_scatter(
                x=_labels_cff, y=_acum_plan,
                name="Planejado",
                mode="lines",
                line=dict(color=CHART_BLUE, width=2, dash="dash"),
            )

        # Linhas Medido e Realizado (do histórico CPL)
        if _hist_cpl:
            def _fmt_periodo(p):
                """Converte "2026-02-28" → "Fev/26"."""
                try:
                    _ano = int(p[:4])
                    _mes = int(p[5:7])
                    return f"{MESES[_mes-1]}/{str(_ano)[-2:]}"
                except Exception:
                    return p

            _labels_cpl = [_fmt_periodo(s.get("periodo_final","")) for s in _hist_cpl]
            _y_medido   = [s.get("pct_medido", 0) for s in _hist_cpl]
            _y_realizado= [s.get("pct_realizado", 0) for s in _hist_cpl]

            if any(v > 0 for v in _y_medido):
                _fg_s.add_scatter(
                    x=_labels_cpl, y=_y_medido,
                    name="Medido (% Avanço Físico)",
                    mode="lines+markers",
                    line=dict(color=CHART_TEAL, width=2.5),
                    marker=dict(size=7),
                )

            _fg_s.add_scatter(
                x=_labels_cpl, y=_y_realizado,
                name="Realizado (% Desembolso)",
                mode="lines+markers",
                line=dict(color=CHART_AMBER, width=2.5),
                marker=dict(size=7),
            )

            # Sombreamento: período sem medição
            if not _hist_cpl[0].get("tem_medicao", True) and _tem_cff:
                _fg_s.add_vrect(
                    x0=_labels_cff[0] if _labels_cff else 0,
                    x1=_labels_cpl[0],
                    fillcolor="rgba(200,200,200,0.2)",
                    layer="below", line_width=0,
                    annotation_text="Sem medição",
                    annotation_font_size=9
                )

        _fg_s.add_hline(y=100, line_dash="dot", line_color=GRAY, line_width=1)
        _fg_s.update_layout(
            title="Curva S — Planejado × Medido × Realizado",
            **PL(380)
        )
        _fg_s.update_xaxes(showgrid=False, tickfont=dict(size=9))
        _fg_s.update_yaxes(
            ticksuffix="%", gridcolor=BORDER,
            range=[0, 110], title="% acumulado do orçamento"
        )
        st.plotly_chart(_fg_s, use_container_width=True)

        st.divider()

        # ══════════════════════════════════════════════════════════════
        # 6 KPI CARDS
        # ══════════════════════════════════════════════════════════════
        if _tem_cpl:
            st.markdown("### 📊 KPIs da Obra")
            st.caption(
                "SPI = Schedule Performance Index · CPI = Cost Performance Index · "
                "EAC = Estimate at Completion · CTP = Contratos de Terceiros/Prestadores"
            )

            _verba_pct = (_verba_disp / _orcado * 100) if _orcado > 0 else 0
            _eac_desvio_pct = ((_eac - _orcado) / _orcado * 100) if _orcado > 0 else 0

            _k1, _k2, _k3, _k4, _k5, _k6 = st.columns(6)

            # ── SPI ──────────────────────────────────────────────────
            with _k1:
                _spi_status = (
                    "✅ No prazo" if _spi >= 0.95
                    else "⚠️ Atenção" if _spi >= 0.85
                    else "🔴 Atrasado"
                )
                st.metric(
                    label=f"{_semaforo_spi(_spi)} SPI — Índice de Prazo",
                    value=f"{_spi:.3f}",
                    delta=_spi_status,
                    delta_color="normal" if _spi >= 0.95 else "inverse",
                    help=(
                        "**SPI — Schedule Performance Index**\n\n"
                        "Mede se a obra está no ritmo planejado.\n\n"
                        f"**Fórmula:** % físico medido ÷ % físico previsto\n\n"
                        f"**Este empreendimento:**\n"
                        f"- Avanço medido: {_pct_medido:.1f}%\n"
                        f"- Avanço previsto: {_pct_planejado:.1f}%\n"
                        f"- SPI = {_pct_medido:.1f} ÷ {_pct_planejado:.1f} = **{_spi:.3f}**\n\n"
                        "**Como ler:**\n"
                        "- SPI = 1,0 → obra exatamente no prazo\n"
                        "- SPI > 1,0 → obra adiantada\n"
                        "- SPI < 1,0 → obra atrasada\n\n"
                        "**Semáforo:** 🟢 ≥ 0,95 · 🟡 0,85–0,94 · 🔴 < 0,85"
                    )
                )
                st.caption(f"Previsto: {_pct_planejado:.1f}% · Medido: {_pct_medido:.1f}%")

            # ── CPI ──────────────────────────────────────────────────
            with _k2:
                _cpi_status = (
                    "✅ Abaixo do custo" if _cpi >= 0.95
                    else "⚠️ Atenção" if _cpi >= 0.85
                    else "🔴 Acima do custo"
                )
                st.metric(
                    label=f"{_semaforo_cpi(_cpi)} CPI — Índice de Custo",
                    value=f"{_cpi:.3f}",
                    delta=_cpi_status,
                    delta_color="normal" if _cpi >= 0.95 else "inverse",
                    help=(
                        "**CPI — Cost Performance Index**\n\n"
                        "Mede a eficiência de custo da obra.\n\n"
                        "**Fórmula:** Valor medido ÷ Valor realizado (desembolsado)\n\n"
                        f"**Este empreendimento:**\n"
                        f"- Valor medido: {fmt(_medido)}\n"
                        f"- Valor realizado: {fmt(_realizado)}\n"
                        f"- CPI = {fmt(_medido)} ÷ {fmt(_realizado)} = **{_cpi:.3f}**\n\n"
                        "**Como ler:**\n"
                        "- CPI = 1,0 → gastando exatamente o previsto\n"
                        "- CPI > 1,0 → cada R$1 gasto entrega mais que R$1 de obra ✅\n"
                        "- CPI < 1,0 → cada R$1 gasto entrega menos que R$1 de obra ⚠️\n\n"
                        "**Atenção:** CPI alto no início da obra pode cair se precisar "
                        "acelerar o ritmo para recuperar atraso de prazo (SPI baixo).\n\n"
                        "**Semáforo:** 🟢 ≥ 0,95 · 🟡 0,85–0,94 · 🔴 < 0,85"
                    )
                )
                st.caption("Medido ÷ Realizado (desembolsado)")

            # ── % FÍSICO ─────────────────────────────────────────────
            with _k3:
                _diff_pp = _pct_medido - _pct_planejado
                _fis_sem = "🟢" if _diff_pp >= -2 else "🟡" if _diff_pp >= -8 else "🔴"
                st.metric(
                    label=f"{_fis_sem} % Avanço Físico",
                    value=f"{_pct_medido:.1f}%",
                    delta=f"{_diff_pp:+.1f} pp vs previsto",
                    delta_color="normal" if _diff_pp >= -2 else "inverse",
                    help=(
                        "**% Avanço Físico (Medido)**\n\n"
                        "Percentual da obra concluído, medido pelo "
                        "boletim de medição do SIENGE.\n\n"
                        f"**Este empreendimento:**\n"
                        f"- Medido: **{_pct_medido:.1f}%** da obra concluída\n"
                        f"- Previsto pelo cronograma: **{_pct_planejado:.1f}%**\n"
                        f"- Diferença: **{_diff_pp:+.1f} pp**\n\n"
                        "**Como ler:**\n"
                        "- Positivo (+) → obra adiantada em relação ao cronograma\n"
                        "- Negativo (−) → obra atrasada em relação ao cronograma\n\n"
                        "**Fonte:** Arquivo CPL (Custo por Nível) do SIENGE, "
                        "coluna Valor Medido Acumulado.\n\n"
                        "**Semáforo:** 🟢 ≤ 2pp atraso · 🟡 2–8pp · 🔴 > 8pp"
                    )
                )
                st.caption(f"Previsto: {_pct_planejado:.1f}%")

            # ── VERBA DISPONÍVEL ─────────────────────────────────────
            with _k4:
                _verba_pct = (_verba_disp / _orcado * 100) if _orcado > 0 else 0
                st.metric(
                    label=f"{_semaforo_verba(_verba_pct)} Verba Disponível",
                    value=fmt(_verba_disp),
                    delta=f"{_verba_pct:.1f}% do orçado",
                    delta_color="normal" if _verba_pct >= 20 else "inverse",
                    help=(
                        "**Verba Disponível**\n\n"
                        "Saldo orçamentário ainda não comprometido em contratos.\n\n"
                        "**Fórmula:** Orçado Total − Total Comprometido\n\n"
                        f"**Este empreendimento:**\n"
                        f"- Orçado total: {fmt(_orcado)}\n"
                        f"- Já comprometido: {fmt(_comprometido)}\n"
                        f"- Verba disponível: **{fmt(_verba_disp)}** "
                        f"({_verba_pct:.1f}% do orçado)\n\n"
                        "**Como ler:**\n"
                        "- Alta verba disponível → liberdade orçamentária para "
                        "novos contratos\n"
                        "- Verba baixa → risco de estouro se surgirem imprevistos\n\n"
                        "**Comprometido** inclui contratos assinados mas ainda "
                        "não executados/pagos.\n\n"
                        "**Semáforo:** 🟢 ≥ 20% · 🟡 10–19% · 🔴 < 10%"
                    )
                )
                st.caption("Orçado − Comprometido")

            # ── EAC ──────────────────────────────────────────────────
            with _k5:
                _eac_desvio_pct = ((_eac - _orcado) / _orcado * 100) if _orcado > 0 else 0
                _eac_sem = _semaforo_eac(_eac, _orcado)
                st.metric(
                    label=f"{_eac_sem} EAC — Custo Final Projetado",
                    value=fmt(_eac),
                    delta=f"Desvio: {_eac_desvio_pct:+.1f}%",
                    delta_color="normal" if _eac_desvio_pct <= 3 else "inverse",
                    help=(
                        "**EAC — Estimate at Completion**\n\n"
                        "Projeção do custo total da obra se o ritmo atual de "
                        "eficiência de custo se mantiver até o fim.\n\n"
                        "**Fórmula:** Realizado + (Orçado Restante ÷ CPI)\n\n"
                        f"**Este empreendimento:**\n"
                        f"- Já realizado: {fmt(_realizado)}\n"
                        f"- Orçado restante: {fmt(_orcado - _realizado)}\n"
                        f"- CPI atual: {_cpi:.3f}\n"
                        f"- EAC = {fmt(_realizado)} + "
                        f"({fmt(_orcado - _realizado)} ÷ {_cpi:.3f}) "
                        f"= **{fmt(_eac)}**\n\n"
                        "**Como ler:**\n"
                        "- EAC < Orçado → obra deve terminar abaixo do orçamento ✅\n"
                        "- EAC = Orçado → obra deve terminar no orçamento\n"
                        "- EAC > Orçado → obra deve estourar o orçamento ⚠️\n\n"
                        f"Orçado: {fmt(_orcado)} · "
                        f"Projeção: {fmt(_eac)} · "
                        f"Desvio: {_eac_desvio_pct:+.1f}%\n\n"
                        "**Semáforo:** 🟢 desvio ≤ 3% · 🟡 3–8% · 🔴 > 8%"
                    )
                )
                st.caption(f"Orçado: {fmt(_orcado)}")

            # ── CTP ──────────────────────────────────────────────────
            with _k6:
                st.metric(
                    label="💼 CTP — Contratos em Aberto",
                    value=fmt(_saldo_ctp),
                    delta=f"Custo mínimo: {fmt(_custo_min)}",
                    delta_color="off",
                    help=(
                        "**CTP — Contratos de Terceiros/Prestadores**\n\n"
                        "Saldo de contratos já assinados mas ainda não "
                        "executados ou pagos.\n\n"
                        f"**Este empreendimento:**\n"
                        f"- CTP: {fmt(_saldo_ctp)}\n"
                        f"- Já realizado (pago): {fmt(_realizado)}\n"
                        f"- Custo mínimo: **{fmt(_custo_min)}**\n\n"
                        "**Custo Mínimo** = Realizado + CTP\n"
                        "É o valor mínimo que a obra vai custar mesmo que "
                        "tudo pare agora — porque esses contratos já estão "
                        "assinados e precisam ser honrados.\n\n"
                        "**Como ler:**\n"
                        "- CTP alto → muitos contratos em andamento, "
                        "obra com boa cobertura\n"
                        "- CTP baixo → poucos contratos ativos, pode indicar "
                        "necessidade de novas contratações para avançar\n\n"
                        "**Fonte:** Arquivo CPL (Custo por Nível) do SIENGE, "
                        "coluna Saldo de Contratos."
                    )
                )
                st.caption("Contratos assinados ainda não executados")

            # Alerta específico: obra atrasada mas dentro do custo
            if _spi < 0.85 and _cpi >= 0.95:
                st.warning(
                    f"⚠️ **Atenção: obra atrasada mas com custo controlado.**\n\n"
                    f"SPI {_spi:.3f} indica atraso físico de "
                    f"**{abs(_pct_medido - _pct_planejado):.1f} pp** em relação ao previsto. "
                    f"Para recuperar o prazo, será necessário acelerar o ritmo — "
                    f"o que tende a aumentar os custos e pressionar o CPI ({_cpi:.3f}) "
                    f"atual para baixo. "
                    f"Monitore o CPI nos próximos meses."
                )

            st.divider()

            # ── TABELA DE DESVIOS POR ETAPA ──────────────────────────
            st.markdown("### 🔍 Top 5 Etapas — Eficiência de Custo")
            st.caption(
                "As 5 maiores etapas por orçamento. "
                "Eficiência = % Medido − % Realizado: "
                "positivo = gastando menos que o avanço justifica ✅ · "
                "negativo = estouro ⚠️"
            )

            _etapas = _cpl_atual.get("etapas_nivel2", [])
            if _etapas:
                # Filtra etapas sem orçamento e ordena pelas 5 maiores
                _etapas_validas = [
                    e for e in _etapas
                    if e.get("orcado", 0) > 0 and e.get("descricao", "").strip()
                ]
                _etapas_top5 = sorted(
                    _etapas_validas,
                    key=lambda x: x.get("orcado", 0),
                    reverse=True
                )[:5]

                _rows_et = []
                for _et in _etapas_top5:
                    _orc  = _et.get("orcado", 0)
                    _med  = _et.get("medido", 0)
                    _real = _et.get("realizado", 0)
                    _verb = _et.get("verba_disp", 0)

                    _pct_med  = (_med  / _orc * 100) if _orc > 0 else 0
                    _pct_real = (_real / _orc * 100) if _orc > 0 else 0
                    _efic     = _pct_med - _pct_real

                    if _efic >= 0:
                        _sem = "🟢"
                        _efic_txt = f"+{_efic:.1f}%"
                    elif _efic >= -5:
                        _sem = "🟡"
                        _efic_txt = f"{_efic:.1f}%"
                    else:
                        _sem = "🔴"
                        _efic_txt = f"{_efic:.1f}%"

                    _rows_et.append({
                        "":           _sem,
                        "Etapa":      _et["descricao"][:30],
                        "Orçado":     _orc,
                        "% Medido":   f"{_pct_med:.0f}%",
                        "% Realizado":f"{_pct_real:.0f}%",
                        "Eficiência": _efic_txt,
                        "Verba":      _verb,
                    })

                _df_et = pd.DataFrame(_rows_et)

                def _hl_et(row):
                    if "🔴" in str(row.get("", "")):
                        return ["background-color:#fff0f0"] * len(row)
                    elif "🟡" in str(row.get("", "")):
                        return ["background-color:#fffbe6"] * len(row)
                    return [""] * len(row)

                try:
                    st.dataframe(
                        _df_et.style
                            .format({"Orçado": "R$ {:,.0f}", "Verba": "R$ {:,.0f}"})
                            .apply(_hl_et, axis=1),
                        use_container_width=True,
                        hide_index=True,
                        height=220
                    )
                except Exception:
                    st.dataframe(_df_et, use_container_width=True, hide_index=True)

            else:
                st.info("Carregue o CPL na aba ⚙️ Configurações para ver os desvios.")

            st.divider()

            # ── EVOLUÇÃO DOS KPIs ─────────────────────────────────────
            if len(_hist_cpl) >= 2:
                st.markdown("### 📉 Evolução dos KPIs")
                st.caption("Tendência de SPI e CPI ao longo dos meses medidos.")

                _periodos_ev = []
                _spi_ev      = []
                _cpi_ev      = []
                _medido_ev   = []
                _realizado_ev= []

                for _snap_ev in _hist_cpl:
                    _pf_ev = _snap_ev.get("periodo_final","")
                    try:
                        _lbl_ev = f"{MESES[int(_pf_ev[5:7])-1]}/{_pf_ev[:4]}"
                    except Exception:
                        _lbl_ev = _pf_ev

                    # SPI para este snapshot
                    _spi_snap = 1.0
                    if _tem_cff and _pf_ev:
                        try:
                            _mes_ev = int(_pf_ev[5:7])
                            _ano_ev = int(_pf_ev[:4])
                            _acum_ev = 0.0
                            _tot_cff = _cr.get("total_obra", 1)
                            for _mi, _mv in zip(_cr.get("meses",[]), _cr.get("custos_por_mes",[])):
                                _acum_ev += _mv
                                if _mi["mes"] == _mes_ev and _mi["ano"] == _ano_ev:
                                    break
                            _pct_plan_ev = (_acum_ev / _tot_cff * 100) if _tot_cff > 0 else 0
                            _pct_med_ev  = _snap_ev.get("pct_medido", 0)
                            _spi_snap    = (_pct_med_ev / _pct_plan_ev) if _pct_plan_ev > 0 else 1.0
                        except Exception:
                            _spi_snap = 1.0

                    _periodos_ev.append(_lbl_ev)
                    _spi_ev.append(round(_spi_snap, 3))
                    _cpi_ev.append(round(_snap_ev.get("cpi", 1.0), 3))
                    _medido_ev.append(round(_snap_ev.get("pct_medido", 0), 1))
                    _realizado_ev.append(round(_snap_ev.get("pct_realizado", 0), 1))

                # Gráfico de linha SPI e CPI
                _fg_ev = go.Figure()
                _fg_ev.add_scatter(
                    x=_periodos_ev, y=_spi_ev,
                    name="SPI", mode="lines+markers",
                    line=dict(color=CHART_TEAL, width=2.5),
                    marker=dict(size=8)
                )
                _fg_ev.add_scatter(
                    x=_periodos_ev, y=_cpi_ev,
                    name="CPI", mode="lines+markers",
                    line=dict(color=CHART_AMBER, width=2.5),
                    marker=dict(size=8)
                )
                # Linha de referência = 1.0
                _fg_ev.add_hline(
                    y=1.0, line_dash="dash",
                    line_color=GRAY, line_width=1.5,
                    annotation_text="Meta = 1.0",
                    annotation_font_size=10
                )
                # Zona de atenção (0.85 a 0.95)
                _fg_ev.add_hrect(
                    y0=0.85, y1=0.95,
                    fillcolor="rgba(255,200,0,0.1)",
                    layer="below", line_width=0
                )
                _fg_ev.update_layout(
                    title="Evolução SPI e CPI",
                    **PL(300)
                )
                _fg_ev.update_xaxes(showgrid=False, tickfont=dict(size=10))
                _fg_ev.update_yaxes(
                    gridcolor=BORDER,
                    range=[max(0, min(_spi_ev + _cpi_ev) - 0.1),
                           max(_spi_ev + _cpi_ev) + 0.1]
                )
                st.plotly_chart(_fg_ev, use_container_width=True)

                # Tabela resumo da evolução
                _df_ev = pd.DataFrame({
                    "Período":    _periodos_ev,
                    "SPI":        [f"{v:.3f}" for v in _spi_ev],
                    "CPI":        [f"{v:.3f}" for v in _cpi_ev],
                    "% Medido":   [f"{v:.1f}%" for v in _medido_ev],
                    "% Realizado":[f"{v:.1f}%" for v in _realizado_ev],
                })
                st.dataframe(_df_ev, use_container_width=True, hide_index=True)

        else:
            # Sem CPL: mostra só o que tem do CFF
            st.info(
                "📊 Curva S planejada disponível. "
                "Para ver os KPIs (SPI, CPI, Verba, EAC), "
                "carregue o **Custo por Nível** na aba ⚙️ Configurações."
            )



# ══════════════════════════════════════════════════════════════════════ TAB 3
@st.fragment
def render_sensibilidade():
    st.markdown(f"## 🎯 Análise de Sensibilidade — {titulo}")
    st.caption("Cenários financeiros, curva VPL e matriz de risco.")
    st.divider()
    base3=dre(emp_base)
    rb3=float(base3["rec_bruta"].sum()); dop3=float(base3["desp_op"].mean())
    _cens=["🔴 Pessimista","🟡 Realista","🟢 Otimista"]
    _params=[
        ("VGV Total (R$ mil)",       "vgv_k",  [rb3/1000*0.8, rb3/1000, rb3/1000*1.2]),
        ("Custo de Obra (% VGV)",    "custo_p",[75.0, 70.0, 65.0]),
        ("WACC (%)",                 "wacc_s", [18.0, 16.0, 14.0]),
        ("Prazo da Obra (meses)",    "prazo_s",[30.0, 24.0, 18.0]),
        ("Overhead Mensal (R$ mil)", "ovhd_k", [abs(dop3)/1000]*3),
    ]
    sc_={p[1]:[] for p in _params}
    st.markdown("### 📊 Seção A — Cenários")
    cols_a=st.columns(3)
    for ci,cen in enumerate(_cens):
        with cols_a[ci]:
            st.markdown(f"**{cen}**")
            for lbl,key,defs in _params:
                v=st.number_input(lbl,value=float(defs[ci]),
                    step=max(float(defs[ci])*0.05,0.1),key=f"sc_{key}_{ci}",format="%.1f")
                sc_[key].append(v)
    def _calc(vgv_k,custo_p,wacc_s,prazo_s,ovhd_k):
        vgv=vgv_k*1000; prazo=int(max(prazo_s,1))
        custo=vgv*custo_p/100; ovhd=ovhd_k*1000*prazo
        ebt=vgv-custo-ovhd; ll=ebt*0.75; mg=ebt/vgv*100 if vgv else 0
        invest=vgv*0.25; rec_m=vgv/prazo; cst_m=(custo+ovhd)/prazo
        cf=[-invest]+[rec_m-cst_m]*prazo; wm=wacc_s/100/12
        npv=sum(cf[t]/(1+wm)**t for t in range(len(cf)))
        def _nr(r): return sum(cf[t]/(1+r)**t for t in range(len(cf)))
        try:
            lo,hi=1e-5,0.5
            for _ in range(60):
                mid=(lo+hi)/2
                if _nr(mid)>0: lo=mid
                else: hi=mid
            tir=((lo+hi)/2*12)*100
        except: tir=0.0
        cum=0; pb=prazo
        for t,v in enumerate(cf):
            cum+=v
            if cum>=0: pb=t; break
        return npv,tir,pb,mg,ll
    st.divider()
    rows_a=[]
    for ci,cen in enumerate(_cens):
        npv,tir,pb,mg,ll=_calc(sc_["vgv_k"][ci],sc_["custo_p"][ci],
                                sc_["wacc_s"][ci],sc_["prazo_s"][ci],sc_["ovhd_k"][ci])
        rows_a.append({"Cenário":cen,"VPL":fmt(npv),"TIR (%a.a.)":f"{tir:.1f}%",
                        "Payback":f"{pb} m","Mg EBITDA":f"{mg:.1f}%","Lucro Líq.":fmt(ll)})
    df_a=pd.DataFrame(rows_a).set_index("Cenário")
    def _hl_a(r):
        c={_cens[0]:"background-color:#fff0f0",_cens[1]:"background-color:#fffbe6",
           _cens[2]:"background-color:#f0fff4"}.get(r.name,"")
        return [c]*len(r)
    try:    st.dataframe(df_a.style.apply(_hl_a,axis=1),use_container_width=True)
    except: st.dataframe(df_a,use_container_width=True)
    st.divider()
    st.markdown("### 📈 Seção B — VPL × Driver")
    _drv_opts=["VGV Total (R$ mil)","Custo de Obra (% VGV)","WACC (%)","Prazo (meses)"]
    _drv_keys={"VGV Total (R$ mil)":"vgv_k","Custo de Obra (% VGV)":"custo_p",
               "WACC (%)":"wacc_s","Prazo (meses)":"prazo_s"}
    sb1,sb2=st.columns([2,3])
    with sb1:
        drv_sel=st.selectbox("Variável:",_drv_opts,key="drv_sel")
        ref_ci=st.radio("Cenário base:",[0,1,2],format_func=lambda x:_cens[x],
                        key="ref_ci",horizontal=True)
    dk=_drv_keys[drv_sel]; ref=sc_[dk][ref_ci]
    _rng=list(np.linspace(ref*0.6,ref*1.4,30)); _npvs=[]
    for v in _rng:
        kw={k:sc_[k][ref_ci] for k in sc_}; kw[dk]=v; _npvs.append(_calc(**kw)[0])
    with sb2:
        fg_b=go.Figure()
        fg_b.add_scatter(x=_rng,y=_npvs,mode="lines+markers",
                         line=dict(color=CHART_BLUE,width=2.5),marker=dict(size=5))
        fg_b.add_hline(y=0,line_dash="dash",line_color=SOFT_RED,line_width=1.5,
                       annotation_text="VPL=0",annotation_position="right")
        fg_b.add_vline(x=ref,line_dash="dot",line_color=GOLD,line_width=1.5,
                       annotation_text="Base",annotation_position="top")
        fg_b.update_layout(title=f"VPL × {drv_sel}",**PL(320))
        fg_b.update_xaxes(title=drv_sel,showgrid=False)
        fg_b.update_yaxes(tickprefix="R$ ",tickformat=",.0f",gridcolor=BORDER)
        st.plotly_chart(fg_b,use_container_width=True)
    st.divider()
    st.markdown("### 🎲 Seção C — Matriz de Risco")
    RISCOS=[("Atraso obra >6m",0.35,0.80),("Queda VGV >15%",0.25,0.90),
            ("Alta custos >20%",0.40,0.70),("WACC +3pp",0.30,0.50),
            ("Distratos >15%",0.20,0.60),("Atraso aprovação",0.20,0.40)]
    _cm={"alto":SOFT_RED,"medio":CHART_AMBER,"baixo":CHART_TEAL}
    fg_r=go.Figure()
    for risco,prob,imp in RISCOS:
        sc_r=prob*imp; niv="alto" if sc_r>0.20 else("medio" if sc_r>0.08 else"baixo")
        fg_r.add_scatter(x=[imp],y=[prob],mode="markers+text",
            marker=dict(size=max(18,sc_r*90),color=_cm[niv],opacity=0.85,
                        line=dict(color=WHITE,width=1.5)),
            text=[risco],textposition="top center",textfont=dict(size=9,color=TEXT),showlegend=False)
    fg_r.add_shape(type="rect",x0=0,y0=0,x1=0.5,y1=0.5,fillcolor="rgba(45,155,138,0.07)",line_width=0)
    fg_r.add_shape(type="rect",x0=0.5,y0=0.5,x1=1.05,y1=1.05,fillcolor="rgba(217,83,79,0.07)",line_width=0)
    fg_r.update_layout(title="Probabilidade × Impacto",
        xaxis=dict(title="Impacto",range=[0,1.1],tickformat=".0%",gridcolor=BORDER),
        yaxis=dict(title="Probabilidade",range=[0,1.1],tickformat=".0%",gridcolor=BORDER),**PL(400))
    st.plotly_chart(fg_r,use_container_width=True)
    st.caption("🟢 ≤8% Baixo  |  🟡 8–20% Médio  |  🔴 >20% Alto")

# ══════════════════════════════════════════════════════════════════════ TAB 4
@st.fragment
def render_indicadores():
    import datetime
    st.markdown("## 📐 Indicadores")
    st.caption("Painel executivo consolidado. Todos os indicadores respondem à seleção da sidebar.")
    st.divider()

    # ── Dados da DRE Analítica (real) ────────────────────────────────
    _rb  = float(final["rec_bruta"].sum())
    _rl  = float(final["rec_liq"].sum())
    _lb  = float(final["lucro_bruto"].sum())
    _ebt = float(final["ebitda"].sum())
    _ll  = float(final["lucro_liq"].sum())
    _cpv_real = float(final["cpv"].sum())

    _mg_bruta_real = (_lb  / _rl  * 100) if _rl  != 0 else 0
    _mg_ebt_real   = (_ebt / _rl  * 100) if _rl  != 0 else 0
    _mg_liq_real   = (_ll  / _rl  * 100) if _rl  != 0 else 0

    # ── Dados das Obras (CPL mais recente de cada SPE ativa) ──────────
    _todas = list(st.session_state.clientes[cliente_sel]["empresas"].keys())
    _spes  = [k for k in _todas
              if st.session_state.get("empresas_ativas", {}).get(k, True)
              and "matriz" not in k.lower()]

    _spi_list   = []
    _cpi_list   = []
    _verba_list = []
    _eac_list   = []
    _orc_total  = 0.0
    _real_total = 0.0
    _med_total  = 0.0

    for _sk in _spes:
        _emp_d = st.session_state.clientes[cliente_sel]["empresas"][_sk]
        _tit_k = _emp_d.get("nome", _sk)
        _est_k = get_rolling_state(_tit_k)
        _hist_k = _est_k.get("historico_cpl", [])
        if not _hist_k:
            continue
        _snap = _hist_k[-1]
        _orc  = _snap.get("orcado_total", 0)
        _med  = _snap.get("medido_acum", 0)
        _real = _snap.get("realizado_acum", 0)
        _cpi  = _snap.get("cpi", 1.0)
        _verba = _snap.get("verba_disponivel", 0)
        _eac   = _snap.get("eac", _orc)

        # SPI: precisa do CFF para % planejado
        _cr_k = _est_k.get("cronograma", {})
        _spi  = 1.0
        if _cr_k and _snap.get("periodo_final"):
            try:
                _pf = _snap["periodo_final"]
                _mes_pf, _ano_pf = int(_pf[5:7]), int(_pf[:4])
                _custos_k = _cr_k.get("custos_por_mes", [])
                _meses_k  = _cr_k.get("meses", [])
                _total_k  = _cr_k.get("total_obra", _orc)
                _acum_p   = 0.0
                for _mi, _mv in zip(_meses_k, _custos_k):
                    _acum_p += _mv
                    if _mi["mes"] == _mes_pf and _mi["ano"] == _ano_pf:
                        break
                _pct_plan = (_acum_p / _total_k * 100) if _total_k else 0
                _pct_med  = (_med / _orc * 100) if _orc else 0
                _spi = (_pct_med / _pct_plan) if _pct_plan > 0 else 1.0
            except Exception:
                _spi = 1.0

        _spi_list.append(_spi)
        _cpi_list.append(_cpi)
        _verba_list.append(_verba)
        _eac_list.append(_eac)
        _orc_total  += _orc
        _real_total += _real
        _med_total  += _med

    _spi_med  = sum(_spi_list) / len(_spi_list)  if _spi_list  else None
    _cpi_med  = sum(_cpi_list) / len(_cpi_list)  if _cpi_list  else None
    _verba_tot = sum(_verba_list)
    _eac_tot   = sum(_eac_list)
    _verba_pct = (_verba_tot / _orc_total * 100) if _orc_total else 0
    _eac_desvio = ((_eac_tot - _orc_total) / _orc_total * 100) if _orc_total else 0

    # ══════════════════════════════════════════════════════════════════
    # BLOCO 1 — RESULTADOS FINANCEIROS (DRE real)
    # ══════════════════════════════════════════════════════════════════
    st.markdown("### 💰 Resultado Financeiro (DRE Real)")
    _f1, _f2, _f3, _f4 = st.columns(4)
    _f1.metric("Receita Líquida",  fmt(_rl),  help="Receita Bruta − Impostos")
    _f2.metric("Margem Bruta",     f"{_mg_bruta_real:.1f}%")
    _f3.metric("EBITDA",           fmt(_ebt),  f"{_mg_ebt_real:.1f}%")
    _f4.metric("Lucro Líquido",    fmt(_ll),   f"{_mg_liq_real:.1f}%")

    st.divider()

    # ══════════════════════════════════════════════════════════════════
    # BLOCO 2 — INDICADORES DE OBRA (EVM)
    # ══════════════════════════════════════════════════════════════════
    if _spi_list:
        st.markdown("### 🏗️ Indicadores de Obra (EVM)")
        st.caption(
            "SPI = Schedule Performance Index · CPI = Cost Performance Index · "
            "EAC = Estimate at Completion · Baseado no CPL mais recente de cada SPE."
        )

        def _sem(v, limites=(0.95, 0.85)):
            if v is None: return "—"
            if v >= limites[0]: return "🟢"
            if v >= limites[1]: return "🟡"
            return "🔴"

        _e1, _e2, _e3, _e4, _e5, _e6 = st.columns(6)
        _e1.metric(
            f"{_sem(_spi_med)} SPI",
            f"{_spi_med:.3f}" if _spi_med else "—",
            help="Média das SPEs ativas"
        )
        _e2.metric(
            f"{_sem(_cpi_med)} CPI",
            f"{_cpi_med:.3f}" if _cpi_med else "—",
            help="Média das SPEs ativas"
        )
        _e3.metric(
            "% Medido",
            f"{(_med_total/_orc_total*100):.1f}%" if _orc_total else "—"
        )
        _e4.metric(
            "% Realizado",
            f"{(_real_total/_orc_total*100):.1f}%" if _orc_total else "—"
        )
        _e5.metric(
            f"{_sem(_verba_pct, (20, 10))} Verba Disp.",
            fmt(_verba_tot),
            f"{_verba_pct:.1f}% do orçado"
        )
        _e6.metric(
            f"{_sem(100-_eac_desvio, (97, 92))} EAC",
            fmt(_eac_tot),
            f"Desvio {_eac_desvio:+.1f}%"
        )

        # Tabela por SPE
        with st.expander("📋 Detalhe por SPE", expanded=False):
            _rows_spe = []
            for _sk, _spi_k, _cpi_k, _vk, _ek in zip(
                _spes, _spi_list, _cpi_list, _verba_list, _eac_list
            ):
                _emp_d2 = st.session_state.clientes[cliente_sel]["empresas"][_sk]
                _snap_k = get_rolling_state(_emp_d2.get("nome", _sk)).get("historico_cpl", [{}])[-1]
                _rows_spe.append({
                    "SPE":       _sk,
                    "SPI":       f"{_spi_k:.3f}",
                    "CPI":       f"{_cpi_k:.3f}",
                    "% Medido":  f"{_snap_k.get('pct_medido',0):.1f}%",
                    "% Realizado": f"{_snap_k.get('pct_realizado',0):.1f}%",
                    "Verba Disp.": fmt(_vk),
                    "EAC":       fmt(_ek),
                    "CPL":       _snap_k.get("periodo_final","—"),
                })
            if _rows_spe:
                st.dataframe(pd.DataFrame(_rows_spe),
                             use_container_width=True, hide_index=True)
    else:
        st.info(
            "ℹ️ Sem dados de obra para indicadores EVM. "
            "Carregue os arquivos CPL na aba **⚙️ Configurações**."
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════
    # BLOCO 3 — PROJEÇÃO (do Rolling Forecast)
    # ══════════════════════════════════════════════════════════════════
    st.markdown("### 📅 Projeção até o Fim da Obra")
    st.caption(f"Baseada na visão **{visao}** selecionada na sidebar.")

    # Calcula projeção rápida para a empresa/consolidado atual
    _ativas_ind = [k for k in _todas
                   if st.session_state.get("empresas_ativas", {}).get(k, True)]
    _dres_p2 = {}
    for _k2 in _ativas_ind:
        _emp2 = st.session_state.clientes[cliente_sel]["empresas"][_k2]
        _tit2 = _emp2.get("nome", _k2)
        _est2 = get_rolling_state(_tit2)
        _cr2  = _est2.get("cronograma", {})
        _di2  = _cr2.get("data_inicio", _est2.get("data_inicio", {"ano":2024,"mes":1}))
        _df2  = _cr2.get("data_fim",    _est2.get("data_fim",    {"ano":2026,"mes":12}))
        _N2   = max(1, min((_df2["ano"]-_di2["ano"])*12+(_df2["mes"]-_di2["mes"])+1, 120))
        _L2   = gen_labels(_N2, _di2)
        _dp   = build_dre_projetada(_emp2, _est2, visao, _N2, _L2, _di2)
        _dres_p2[_k2] = _dp

    # Consolida
    if len(_dres_p2) == 1:
        _dp_final = list(_dres_p2.values())[0]
    else:
        _N2_max = max(len(v["rec_bruta"]) for v in _dres_p2.values())
        def _soma2(c):
            r = [0.0]*_N2_max
            for _vv2 in _dres_p2.values():
                for _ii2, _v2 in enumerate(_vv2.get(c,[])):
                    if _ii2 < _N2_max: r[_ii2] += float(_v2)
            return r
        _dp_final = {c: _soma2(c) for c in
                     ["rec_bruta","lucro_bruto","ebitda","lucro_liq","cpv","desp_op"]}

    _rb_p  = sum(_dp_final.get("rec_bruta", []))
    _lb_p  = sum(_dp_final.get("lucro_bruto", []))
    _ebt_p = sum(_dp_final.get("ebitda", []))
    _ll_p  = sum(_dp_final.get("lucro_liq", []))
    _mg_b_p = (_lb_p/_rb_p*100) if _rb_p else 0
    _mg_e_p = (_ebt_p/_rb_p*100) if _rb_p else 0
    _mg_l_p = (_ll_p/_rb_p*100) if _rb_p else 0

    _p1, _p2, _p3, _p4 = st.columns(4)
    _p1.metric("VGV Projetado",       fmt(_rb_p))
    _p2.metric("Margem Bruta Proj.",  f"{_mg_b_p:.1f}%")
    _p3.metric("EBITDA Projetado",    fmt(_ebt_p), f"{_mg_e_p:.1f}%")
    _p4.metric("Lucro Líq. Projetado",fmt(_ll_p),  f"{_mg_l_p:.1f}%")

    st.divider()

    # Comparativo real vs projetado
    st.markdown("#### 📊 Real vs Projetado")
    _rows_comp = [
        {"Métrica": "Receita / VGV",   "Real": fmt(_rl),  "Projetado": fmt(_rb_p)},
        {"Métrica": "Margem Bruta",    "Real": f"{_mg_bruta_real:.1f}%", "Projetado": f"{_mg_b_p:.1f}%"},
        {"Métrica": "EBITDA",          "Real": fmt(_ebt), "Projetado": fmt(_ebt_p)},
        {"Métrica": "Margem EBITDA",   "Real": f"{_mg_ebt_real:.1f}%",  "Projetado": f"{_mg_e_p:.1f}%"},
        {"Métrica": "Lucro Líquido",   "Real": fmt(_ll),  "Projetado": fmt(_ll_p)},
        {"Métrica": "Margem Líquida",  "Real": f"{_mg_liq_real:.1f}%",  "Projetado": f"{_mg_l_p:.1f}%"},
    ]
    st.dataframe(
        pd.DataFrame(_rows_comp),
        use_container_width=True,
        hide_index=True
    )


def render_fcff_dcf():
    st.markdown(f"## 💰 FCFF & Valuation DCF — {titulo}")
    st.caption("Fluxo de Caixa Livre para a Firma + Valuation por Desconto de Fluxo de Caixa.")
    st.divider()

    # ── WACC ──────────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Premissas de WACC e FCFF")
    w1, w2 = st.columns(2)

    with w1:
        st.markdown("**📐 Custo de Capital Próprio (Ke) — CAPM**")
        wc1,wc2,wc3,wc4 = st.columns(4)
        rf    = wc1.number_input("Rf — Selic (%)",    value=13.75, step=0.25, format="%.2f",
                                  help="Taxa livre de risco (Selic atual)")
        beta  = wc2.number_input("β — Beta setor",    value=0.90,  step=0.05, format="%.2f",
                                  help="Beta desalavancado do setor imobiliário br (~0.8-1.1)")
        prm   = wc3.number_input("Prêmio Mercado (%)",value=5.50,  step=0.25, format="%.2f",
                                  help="Equity Risk Premium histórico Brasil")
        rb    = wc4.number_input("Risco Brasil (%)",  value=2.50,  step=0.25, format="%.2f",
                                  help="CDS spread soberano")
        ke = rf + beta*prm + rb
        st.metric("**Ke — Custo do Equity**", f"{ke:.2f}%",
                  help="Ke = Rf + β × Prêmio + Risco Brasil")

    with w2:
        st.markdown("**🏦 Custo de Dívida e Estrutura de Capital**")
        wk1,wk2,wk3,wk4 = st.columns(4)
        kd_bruto = wk1.number_input("Kd bruto (%)", value=16.50, step=0.25, format="%.2f",
                                     help="CDI + spread bancário")
        aliq_ir  = wk2.number_input("IR efetivo (%)",value=15.0, step=0.5,  format="%.2f",
                                     help="Alíquota efetiva IR+CSLL")
        pct_d    = wk3.number_input("% Dívida",      value=30.0, step=5.0,  format="%.0f",
                                     help="Participação da dívida na estrutura de capital")
        divida_liq = wk4.number_input("Dívida Líq. (R$)", value=0.0, step=10000.0, format="%.0f",
                                       help="Dívida total − Caixa disponível")
        pct_e = 100 - pct_d
        kd_liq = kd_bruto * (1 - aliq_ir/100)
        wacc = (ke * pct_e/100) + (kd_liq * pct_d/100)
        st.metric("**WACC**", f"{wacc:.2f}%",
                  help="WACC = Ke×%E + Kd×(1-IR)×%D")

    st.divider()
    # ── Premissas FCF ──────────────────────────────────────────────────────
    st.markdown("### 📋 Premissas de FCFF")
    fp1,fp2,fp3,fp4,fp5 = st.columns(5)
    capex    = fp1.number_input("CapEx anual (R$)",    value=50000.0, step=5000.0, format="%.0f",
                                 help="Investimentos em ativos fixos/obra no ano base")
    d_a      = fp2.number_input("Depr./Amort. (R$)",  value=10000.0, step=1000.0, format="%.0f",
                                 help="Depreciação e amortização (não-caixa)")
    delta_cg = fp3.number_input("Δ Cap. de Giro (R$)",value=0.0,     step=5000.0, format="%.0f",
                                 help="Variação do capital de giro (positivo = uso de caixa)")
    anos_proj= fp4.number_input("Anos de projeção",   value=5,       min_value=3, max_value=10, step=1,
                                 help="Horizonte de projeção em anos")
    g_perp   = fp5.number_input("g — Perpetuidade (%)",value=3.0,   step=0.25, format="%.2f",
                                 help="Taxa de crescimento na perpetuidade (valor terminal)")

    st.divider()
    # ── Premissas de crescimento por ano ──────────────────────────────────
    st.markdown("### 📈 Crescimento da Receita por Ano (%)")
    gcols = st.columns(int(anos_proj))
    g_anos = []
    defs_g = [20,15,12,10,8,7,6,5,4,3]
    for i, col in enumerate(gcols):
        g_anos.append(col.number_input(f"Ano {i+1}", value=float(defs_g[i]),
                                        min_value=-50.0, max_value=200.0, step=1.0,
                                        key=f"g_ano_{i}"))

    # ── Cálculo FCFF ──────────────────────────────────────────────────────
    base_dre = calc_dre(tuple(emp_base["rec_bruta"]),tuple(emp_base["imp_rec"]),
                         tuple(emp_base["cpv"]),tuple(emp_base["desp_op"]),
                         tuple(emp_base["res_fin"]),tuple(emp_base["ir"]))

    ebitda0 = float(base_dre["ebitda"].sum())
    rb0     = float(base_dre["rec_bruta"].sum())
    mg_ebt0 = ebitda0/rb0 if rb0!=0 else 0

    tabela_fcff = []
    ev_fcff = 0.0
    fator_rec = 1.0

    for ano in range(1, int(anos_proj)+1):
        fator_rec *= (1 + g_anos[ano-1]/100)
        rb_a   = rb0 * fator_rec
        ebt_a  = rb_a * mg_ebt0           # mantém margem EBITDA
        ebit_a = ebt_a - d_a
        nopat  = ebit_a * (1 - aliq_ir/100)
        fcff_a = nopat + d_a - capex - delta_cg
        pv_fcff= fcff_a / (1 + wacc/100)**ano
        ev_fcff += pv_fcff
        tabela_fcff.append({
            "Ano": f"Ano {ano}",
            "Receita Bruta": rb_a,
            "EBITDA":        ebt_a,
            "EBIT":          ebit_a,
            "NOPAT":         nopat,
            "CapEx":        -capex,
            "Δ Cap.Giro":  -delta_cg,
            "FCFF":          fcff_a,
            "PV do FCFF":    pv_fcff,
        })

    # Valor Terminal (Gordon)
    fcff_n  = tabela_fcff[-1]["FCFF"]
    vt      = fcff_n*(1+g_perp/100)/(wacc/100 - g_perp/100) if wacc > g_perp else 0.0
    pv_vt   = vt / (1+wacc/100)**int(anos_proj)
    ev      = ev_fcff + pv_vt
    equity  = ev - divida_liq

    st.divider()
    # ── KPIs Valuation ────────────────────────────────────────────────────
    st.markdown("### 🎯 Resultado do Valuation")
    vk1,vk2,vk3,vk4,vk5,vk6 = st.columns(6)
    vk1.metric("WACC",              f"{wacc:.2f}%")
    vk2.metric("PV dos FCFFs",      fmt(ev_fcff))
    vk3.metric("Valor Terminal",    fmt(vt))
    vk4.metric("PV Valor Terminal", fmt(pv_vt))
    vk5.metric("Enterprise Value",  fmt(ev))
    vk6.metric("Equity Value",      fmt(equity),
               f"−Dív.Líq {fmt(divida_liq)}", delta_color="off")

    # ── Gráficos ──────────────────────────────────────────────────────────
    gc1, gc2 = st.columns(2)

    with gc1:
        # Waterfall EV Bridge
        anos_lbl = [r["Ano"] for r in tabela_fcff] + ["Val.Terminal","Ent.Value","−Dív.Líq.","Equity"]
        anos_val = [r["PV do FCFF"] for r in tabela_fcff] + [pv_vt, ev, -divida_liq, equity]
        anos_med = ["relative"]*int(anos_proj) + ["relative","total","relative","total"]
        fg1 = go.Figure(go.Waterfall(
            orientation="v", measure=anos_med, x=anos_lbl, y=anos_val,
            connector={"line":{"color":BORDER}},
            increasing={"marker":{"color":CHART_TEAL}},
            decreasing={"marker":{"color":SOFT_RED}},
            totals={"marker":{"color":CHART_NAVY}},
            text=[fmt(v) for v in anos_val], textposition="outside", textfont=dict(size=8)
        ))
        fg1.update_layout(title="EV Bridge — DCF Waterfall", **PL(400))
        st.plotly_chart(fg1, use_container_width=True)

    with gc2:
        # Composição EV: PV FCFFs vs Valor Terminal
        pv_total_fcff = sum(r["PV do FCFF"] for r in tabela_fcff)
        fg2 = go.Figure(go.Pie(
            labels=[f"PV FCFFs ({int(anos_proj)} anos)","PV Valor Terminal"],
            values=[max(pv_total_fcff,0), max(pv_vt,0)],
            hole=0.45,
            marker=dict(colors=[CHART_BLUE, CHART_NAVY]),
            textinfo="label+percent", textfont=dict(size=11)
        ))
        fg2.update_layout(title="Composição do Enterprise Value", **PL(400))
        st.plotly_chart(fg2, use_container_width=True)

    # ── FCFF por Ano — tabela ──────────────────────────────────────────────
    st.divider()
    st.markdown("### 📋 Projeção FCFF Detalhada")
    df_fcff = pd.DataFrame(tabela_fcff).set_index("Ano")
    tots_fcff = {"FCFF","PV do FCFF"}
    def hl_fcff(row):
        return ([f"background-color:{BLIGHT};font-weight:700;color:{NAVY}"]*len(row)
                if row.name in tots_fcff else [""]*len(row))
    def cn_fcff(v):
        try: return f"color:{SOFT_RED}" if float(v)<0 else ""
        except: return ""
    fmt_fcff = {c:"R$ {:,.0f}" for c in df_fcff.columns}
    try:
        styled_fcff = df_fcff.style.format(fmt_fcff).apply(hl_fcff,axis=1).map(cn_fcff)
    except AttributeError:
        styled_fcff = df_fcff.style.format(fmt_fcff).apply(hl_fcff,axis=1).applymap(cn_fcff)
    st.dataframe(styled_fcff, use_container_width=True, height=280)

    # ── Sensibilidade WACC × g ────────────────────────────────────────────
    st.divider()
    st.markdown("### 🎯 Análise de Sensibilidade — Equity Value (R$)")
    st.caption("Variação do Equity Value conforme WACC e taxa de crescimento na perpetuidade.")

    wacc_range = [wacc-2, wacc-1, wacc, wacc+1, wacc+2]
    g_range    = [g_perp-1, g_perp-0.5, g_perp, g_perp+0.5, g_perp+1]

    sens_rows = []
    for g_s in g_range:
        row = {"g \\ WACC": f"g={g_s:.1f}%"}
        for w_s in wacc_range:
            ev_s = ev_fcff  # mantém PV FCFFs
            vt_s = fcff_n*(1+g_s/100)/(w_s/100-g_s/100) if w_s>g_s else 0.0
            pv_vt_s = vt_s/(1+w_s/100)**int(anos_proj)
            eq_s = ev_s + pv_vt_s - divida_liq
            row[f"W={w_s:.1f}%"] = eq_s
        sens_rows.append(row)

    df_sens = pd.DataFrame(sens_rows).set_index("g \\ WACC")

    def color_sens(v):
        try:
            f = float(v)
            if f >= equity*1.1:   return f"background-color:#d4edda;color:#155724"
            if f <= equity*0.9:   return f"background-color:#f8d7da;color:#721c24"
            return f"background-color:{BLIGHT}"
        except: return ""

    try:
        styled_sens = df_sens.style.format("R$ {:,.0f}").map(color_sens)
    except AttributeError:
        styled_sens = df_sens.style.format("R$ {:,.0f}").applymap(color_sens)

    st.dataframe(styled_sens, use_container_width=True)
    st.caption(f"🟦 = próximo ao valor base | 🟢 = +10% | 🔴 = −10%")

    # Download
    df_dl = df_fcff.copy()
    st.download_button("📥 Exportar FCFF & Valuation em Excel",
                       data=excel_dre(df_dl,"FCFF Valuation"),
                       file_name=f"FCFF_DCF_{titulo.replace(' ','_')}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key="dl_fcff")
    st.caption("🔄 Próximos: Cronograma Físico-Financeiro · Deploy Streamlit Cloud")


def _calcula_vgv_projetado(vendas: dict, total_unidades: int,
                            cronograma: dict, data_inicio: dict,
                            unidades_report: dict = None) -> dict:
    """
    Calcula o dict VGV para o estado do rolling.

    Retorna:
      {1: {"unidades": 0, "preco": 447000}, 2: {...}, ...}

    Lógica:
      - Meses passados: preenche com as vendas reais do relatório
      - Meses futuros: distribui o VGV restante uniformemente
        (máximo 8 meses, até o fim da obra)
    """
    import datetime

    vendas_por_mes    = vendas.get("vendas_por_mes", {}) if vendas else {}
    unidades_vendidas = vendas.get("unidades_vendidas", 0) if vendas else 0
    preco_medio_vendas = vendas.get("preco_medio", 0.0) if vendas else 0.0

    # Usa Relatório de Unidades se disponível (mais preciso)
    if unidades_report and unidades_report.get("total_unidades", 0) > 0:
        _total    = unidades_report["total_unidades"]
        _disp     = unidades_report.get("disponiveis", 0)
        _vgv_vend = unidades_report.get("vgv_vendido", 0.0)
        _vendidas = unidades_report.get("vendidas", 0)
        preco_medio = _vgv_vend / _vendidas if _vendidas > 0 else preco_medio_vendas
        restantes   = _disp  # usa disponíveis reais do relatório
    else:
        preco_medio = preco_medio_vendas
        restantes   = max(total_unidades - unidades_vendidas, 0)

    vgv_restante = restantes * preco_medio

    # Período da obra
    if cronograma:
        _di = cronograma.get("data_inicio", data_inicio)
        _df = cronograma.get("data_fim",    {"ano": 2027, "mes": 12})
    else:
        _di = data_inicio
        _df = {"ano": 2027, "mes": 12}

    N = (_df["ano"] - _di["ano"]) * 12 + (_df["mes"] - _di["mes"]) + 1
    N = max(1, min(N, 120))

    # Mês atual (índice 1-based no horizonte da obra)
    hoje = datetime.date.today()
    _inicio_dt = datetime.date(_di["ano"], _di["mes"], 1)
    mes_atual_idx = (hoje.year - _inicio_dt.year) * 12 + (hoje.month - _inicio_dt.month)
    mes_atual_idx = max(0, min(mes_atual_idx, N - 1))

    # Meses disponíveis para projeção futura
    meses_futuros = list(range(mes_atual_idx, N))
    meses_proj = meses_futuros[:8]  # máximo 8
    n_proj = len(meses_proj)

    vgv_por_mes_proj = (vgv_restante / n_proj) if n_proj > 0 else 0.0

    # Monta dict VGV por índice
    vgv = {}
    for i in range(N):
        # Calcula mês/ano deste índice
        _m = (_inicio_dt.month + i - 1) % 12 + 1
        _a = _inicio_dt.year + (_inicio_dt.month + i - 1) // 12
        _chave_mes = f"{_a}-{_m:02d}"

        if _chave_mes in vendas_por_mes:
            # Mês com venda real
            _un  = vendas_por_mes[_chave_mes]["unidades"]
            _vgv = vendas_por_mes[_chave_mes]["vgv"]
            _preco = _vgv / _un if _un > 0 else preco_medio
            vgv[i + 1] = {"unidades": _un, "preco": _preco}
        elif i in meses_proj and vgv_por_mes_proj > 0:
            # Mês futuro com projeção
            vgv[i + 1] = {
                "unidades": 1,
                "preco": round(vgv_por_mes_proj, 2)
            }
        else:
            vgv[i + 1] = {"unidades": 0, "preco": round(preco_medio, 2)}

    return vgv


# ── Roteamento ────────────────────────────────────────────────────────────────
@st.fragment
def render_configuracoes():
    st.markdown("## ⚙️ Configurações")
    st.caption("Central de configuração do sistema. Organize os dados antes de ver os resultados.")
    st.divider()

    # ── Seletor de SPE (mesmo padrão do Resumo de Obras) ──────────────
    _todas = list(st.session_state.clientes[cliente_sel]["empresas"].keys())
    _spes  = [k for k in _todas
              if st.session_state.get("empresas_ativas", {}).get(k, True)
              and "matriz" not in k.lower()]
    if not _spes:
        st.warning("Nenhuma SPE ativa. Ative ao menos uma empresa na sidebar.")
        return

    _cfg_emp_key = "_cfg_empresa_sel"
    if empresa_sel in _spes:
        st.session_state[_cfg_emp_key] = empresa_sel
    elif st.session_state.get(_cfg_emp_key) not in _spes:
        st.session_state[_cfg_emp_key] = _spes[0]

    st.markdown("**🏢 Configurando dados para:**")
    _cols_spe = st.columns(min(len(_spes), 3))
    for _i, _sk in enumerate(_spes):
        with _cols_spe[_i % 3]:
            _emp_d = st.session_state.clientes[cliente_sel]["empresas"][_sk]
            _tit_k = _emp_d.get("nome", _sk)
            _est_k = get_rolling_state(_tit_k)
            _tem_c = "cronograma" in _est_k
            _tem_p = bool(_est_k.get("historico_cpl", []))
            _icone = "✅" if (_tem_c and _tem_p) else "⚠️" if (_tem_c or _tem_p) else "⬜"
            if st.button(
                f"{_icone} {_sk}",
                key=f"_cfg_btn_{_sk}",
                type="primary" if _sk == st.session_state.get(_cfg_emp_key) else "secondary",
                use_container_width=True
            ):
                st.session_state[_cfg_emp_key] = _sk
                st.rerun()

    _spe_sel   = st.session_state.get(_cfg_emp_key, _spes[0])
    _emp_cfg   = st.session_state.clientes[cliente_sel]["empresas"][_spe_sel]
    _titulo_cfg = _emp_cfg.get("nome", _spe_sel)
    _estado_cfg = get_rolling_state(_titulo_cfg)
    _tkey_cfg  = re.sub(r"\W+", "_", _titulo_cfg)

    st.caption(f"Editando: **{_spe_sel}**")
    st.divider()

    # ══════════════════════════════════════════════════════════════════
    # BLOCO 1 — DADOS DA OBRA (CFF + CPL)
    # ══════════════════════════════════════════════════════════════════
    st.markdown("### 📁 Bloco 1 — Dados da Obra")

    # CFF
    with st.expander("📐 Cronograma Físico/Financeiro (CFF)", expanded=True):
        st.caption("Suba o relatório CFF exportado do SIENGE. Re-exportar apenas em reprogramações.")
        if "cronograma" in _estado_cfg:
            _cr = _estado_cfg["cronograma"]
            _arq_cff = _cr.get("arquivo_nome", "arquivo não identificado")
            _data_upload_cff = _cr.get("data_upload", "")
            _dt_fmt = ""
            if _data_upload_cff:
                try:
                    import datetime as _dtu
                    _dt_fmt = _dtu.datetime.fromisoformat(_data_upload_cff).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    pass
            st.success(
                f"✅ **{_arq_cff}** · {_dt_fmt}\n\n"
                f"📅 {MESES[_cr['data_inicio']['mes']-1]}/{_cr['data_inicio']['ano']} → "
                f"{MESES[_cr['data_fim']['mes']-1]}/{_cr['data_fim']['ano']} · "
                f"{_cr['n_meses']} meses · Total: R$ {sum(_cr['custos_por_mes']):,.0f}"
            )
            if st.button("🔄 Substituir CFF", key=f"sub_cff_{_tkey_cfg}"):
                del _estado_cfg["cronograma"]
                save_rolling(_titulo_cfg, force=True)
                safe_toast("CFF removido. Suba a nova versão.", "🔄")
                st.rerun()
        else:
            _arq_cff_up = st.file_uploader(
                "Selecione o CFF (.xlsx)",
                type=["xlsx","xls"],
                key=f"up_cff_cfg_{_tkey_cfg}",
                label_visibility="collapsed"
            )
            if _arq_cff_up:
                _cron_raw = parse_cronograma_sienge(_arq_cff_up.read())
                if "erro" in _cron_raw:
                    st.error(f"❌ {_cron_raw['erro']}")
                else:
                    _estado_cfg["cronograma"] = _cron_raw
                    _estado_cfg["cronograma"]["arquivo_nome"] = _arq_cff_up.name
                    _estado_cfg["data_inicio"] = _cron_raw["data_inicio"]
                    _estado_cfg["data_fim"]    = _cron_raw["data_fim"]
                    save_rolling(_titulo_cfg, force=True)
                    safe_toast(f"✅ CFF carregado: {_arq_cff_up.name}", "✅")
                    st.rerun()

    # CPL
    with st.expander("📊 Custo por Nível (CPL) — Histórico mensal", expanded=True):
        st.caption(
            "Suba o CPL após cada boletim de medição (~dia 10 do mês seguinte). "
            "Pode também subir versão diária a qualquer momento."
        )
        _hist = _estado_cfg.get("historico_cpl", [])
        if _hist:
            _cols_h = st.columns([3,1,1,1,1,1])
            _cols_h[0].caption("Arquivo")
            _cols_h[1].caption("Período")
            _cols_h[2].caption("Medido")
            _cols_h[3].caption("CPI")
            _cols_h[4].caption("Realizado")
            _cols_h[5].caption("Ação")
            for _snap in reversed(_hist):
                _c0,_c1,_c2,_c3,_c4,_c5 = st.columns([3,1,1,1,1,1])
                _c0.caption(_snap.get("arquivo_nome","?")[:30])
                _c1.caption(_snap.get("periodo_final","?"))
                _c2.caption(f"{_snap.get('pct_medido',0):.1f}%")
                _c3.caption(f"{_snap.get('cpi',0):.3f}")
                _c4.caption(f"R$ {_snap.get('realizado_acum',0):,.0f}")
                if _c5.button("🗑️", key=f"del_cpl_cfg_{_snap.get('periodo_final','')}_{_tkey_cfg}"):
                    _estado_cfg["historico_cpl"] = [
                        s for s in _hist if s.get("periodo_final") != _snap.get("periodo_final")
                    ]
                    save_rolling(_titulo_cfg, force=True)
                    safe_toast("Snapshot removido.", "🗑️")
                    st.rerun()

        _arq_cpl_up = st.file_uploader(
            "Adicionar novo CPL (.xlsx)",
            type=["xlsx","xls"],
            key=f"up_cpl_cfg_{_tkey_cfg}",
            label_visibility="collapsed"
        )
        if _arq_cpl_up:
            _cpl_raw = parse_custo_nivel(_arq_cpl_up.read(), _arq_cpl_up.name)
            if "erro" in _cpl_raw:
                st.error(f"❌ {_cpl_raw['erro']}")
            else:
                _periodo = _cpl_raw.get("periodo_final","")
                _hist_upd = [s for s in _hist if s.get("periodo_final") != _periodo]
                _hist_upd.append(_cpl_raw)
                _hist_upd.sort(key=lambda s: s.get("periodo_final",""))
                _estado_cfg["historico_cpl"] = _hist_upd

                # Atualiza poc_acum automaticamente com pct_medido do CPL
                _pct_med_cpl = _cpl_raw.get("pct_medido", 0.0)
                _per_fin_cpl = _cpl_raw.get("periodo_final", "")
                if _pct_med_cpl > 0 and _per_fin_cpl:
                    try:
                        _ano_cpl2 = int(_per_fin_cpl[:4])
                        _mes_cpl2 = int(_per_fin_cpl[5:7])
                        _cr_poc2  = _estado_cfg.get("cronograma", {})
                        _di_poc2  = _cr_poc2.get("data_inicio",
                                        _estado_cfg.get("data_inicio", {"ano": 2024, "mes": 1}))
                        _idx_poc2 = (_ano_cpl2 - _di_poc2["ano"]) * 12 + (_mes_cpl2 - _di_poc2["mes"])
                        _poc_arr2 = list(_estado_cfg.get("poc_acum", [0] * 24))
                        if 0 <= _idx_poc2 < len(_poc_arr2):
                            _poc_arr2[_idx_poc2] = round(_pct_med_cpl, 1)
                            _estado_cfg["poc_acum"] = _poc_arr2
                            mark_rolling_dirty(_titulo_cfg)
                    except Exception:
                        pass  # Não bloquear o upload do CPL por erro no POC

                save_rolling(_titulo_cfg, force=True)
                safe_toast(
                    f"✅ CPL carregado: {_cpl_raw.get('periodo_final','?')} · "
                    f"Medido {_cpl_raw.get('pct_medido',0):.1f}% · "
                    f"CPI {_cpl_raw.get('cpi',0):.3f}", "✅"
                )
                st.rerun()

    # ── UNIDADES ──────────────────────────────────────────────────────
    with st.expander("🏢 Relatório de Unidades (Estoque)", expanded=True):
        st.caption(
            "Suba o relatório 'Unidades por Empreendimento (Sintético)' "
            "do SIENGE. Este relatório define o total de unidades e o VGV."
        )

        # ── Relatório de Unidades ─────────────────────────────────
        _un_report = _estado_cfg.get("unidades_report")

        if _un_report:
            _un_arq = _un_report.get("arquivo_nome","?")
            _un_dt  = ""
            try:
                from datetime import datetime as _dtt
                _un_dt = _dtt.fromisoformat(
                    _un_report.get("data_upload","")
                ).strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

            st.success(f"✅ **{_un_arq}** · {_un_dt}")
            st.caption("Estoque de unidades carregado. Resumo disponível na aba 📅 Rolling Forecast.")

            # Atualiza total_unidades automaticamente (lógica de negócio — manter)
            if _estado_cfg.get("total_unidades",0) != _un_report["total_unidades"]:
                _estado_cfg["total_unidades"] = _un_report["total_unidades"]
                mark_rolling_dirty(_titulo_cfg)

            if st.button("🔄 Substituir relatório de unidades",
                         key=f"sub_un_{_tkey_cfg}"):
                _estado_cfg["unidades_report"] = None
                save_rolling(_titulo_cfg, force=True)
                safe_toast("Relatório removido. Suba a nova versão.", "🔄")
                st.rerun()
        else:
            # Campo manual como fallback
            _total_un = int(_estado_cfg.get("total_unidades", 0))
            _vendas_st = _estado_cfg.get("vendas",{})
            _min_un = _vendas_st.get("unidades_vendidas", 0) if _vendas_st else 0

            _total_un_input = st.number_input(
                "Total de unidades do empreendimento",
                min_value=0, step=1,
                value=_total_un,
                key=f"total_un_{_tkey_cfg}",
                help=(
                    "Número total de apartamentos + salas (sem garagens). "
                    f"Mínimo: {_min_un} (unidades já vendidas)."
                    if _min_un else
                    "Número total de apartamentos + salas (sem garagens)."
                )
            )
            if _min_un > 0 and _total_un_input < _min_un:
                st.warning(
                    f"⚠️ Total informado ({_total_un_input}) é menor que "
                    f"as unidades já vendidas ({_min_un})."
                )
            if _total_un_input != _total_un:
                _estado_cfg["total_unidades"] = _total_un_input
                save_rolling(_titulo_cfg, force=True)

            # Uploader do relatório de unidades
            st.caption("Ou suba o Relatório de Unidades para preenchimento automático:")
            _arq_un_up = st.file_uploader(
                "Relatório de Unidades (.xlsx)",
                type=["xlsx","xls"],
                key=f"up_un_{_tkey_cfg}",
                label_visibility="collapsed"
            )
            if _arq_un_up:
                _un_raw = parse_unidades_sienge(_arq_un_up.read(), _arq_un_up.name)
                if "erro" in _un_raw:
                    st.error(f"❌ {_un_raw['erro']}")
                else:
                    _estado_cfg["unidades_report"] = _un_raw
                    _estado_cfg["total_unidades"]  = _un_raw["total_unidades"]
                    # Recalcula VGV automaticamente com dados do relatório de unidades
                    _vendas_atual = _estado_cfg.get("vendas")
                    if _vendas_atual:
                        _vgv_recalc = _calcula_vgv_projetado(
                            _vendas_atual,
                            _un_raw["total_unidades"],
                            _estado_cfg.get("cronograma", {}),
                            _estado_cfg.get("data_inicio", {"ano": 2024, "mes": 1}),
                            _un_raw,
                        )
                        _estado_cfg["vgv"] = _vgv_recalc
                    save_rolling(_titulo_cfg, force=True)
                    safe_toast(
                        f"✅ {_un_raw['total_unidades']} unidades · "
                        f"{_un_raw['vendidas']} vendidas · "
                        f"{_un_raw['disponiveis']} disponíveis · "
                        f"{_un_raw['permuta']} permuta(s)",
                        "✅"
                    )
                    st.rerun()

    # ── VENDAS ────────────────────────────────────────────────────────
    with st.expander("🏠 Relatório de Vendas", expanded=True):
        st.caption(
            "Suba o relatório 'Vendas por Empreendimento — Simplificado' "
            "exportado do SIENGE. Atualizar mensalmente."
        )

        # Mostra resumo se já tem vendas carregadas
        _vendas = _estado_cfg.get("vendas")
        if _vendas:
            _v_un  = _vendas.get("unidades_vendidas", 0)
            _v_vgv = _vendas.get("vgv_vendido", 0.0)
            _v_pm  = _vendas.get("preco_medio", 0.0)
            _v_arq = _vendas.get("arquivo_nome", "?")
            _v_dt  = ""
            try:
                from datetime import datetime as _dtt
                _v_dt = _dtt.fromisoformat(
                    _vendas.get("data_upload","")
                ).strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

            st.success(
                f"✅ **{_v_arq}** · {_v_dt}\n\n"
                f"🏠 **{_v_un}** unidades vendidas · "
                f"VGV: **R$ {_v_vgv:,.0f}** · "
                f"Preço médio: **R$ {_v_pm:,.0f}**"
            )

            _tot = _estado_cfg.get("total_unidades", 0)
            if _tot > 0:
                _rest = max(_tot - _v_un, 0)
                _vgv_rest = _rest * _v_pm
                st.info(
                    f"📊 Restam **{_rest} unidades** a vender · "
                    f"VGV estimado: **R$ {_vgv_rest:,.0f}** "
                    f"(@ R$ {_v_pm:,.0f}/un)"
                )

        # Uploader
        _arq_vendas = st.file_uploader(
            "Selecione o relatório de vendas (.xlsx)",
            type=["xlsx", "xls"],
            key=f"up_vendas_{_tkey_cfg}",
            label_visibility="collapsed"
        )
        if _arq_vendas:
            _v_raw = parse_vendas_sienge(_arq_vendas.read(), _arq_vendas.name)
            if "erro" in _v_raw:
                st.error(f"❌ {_v_raw['erro']}")
            else:
                _estado_cfg["vendas"] = _v_raw
                # Atualiza VGV automaticamente
                _vgv_auto = _calcula_vgv_projetado(
                    _v_raw,
                    _estado_cfg.get("total_unidades", 0),
                    _estado_cfg.get("cronograma", {}),
                    _estado_cfg.get("data_inicio", {"ano": 2024, "mes": 1}),
                    _estado_cfg.get("unidades_report"),
                )
                _estado_cfg["vgv"] = _vgv_auto
                save_rolling(_titulo_cfg, force=True)
                safe_toast(
                    f"✅ Vendas carregadas: {_v_raw['unidades_vendidas']} un · "
                    f"VGV R$ {_v_raw['vgv_vendido']:,.0f} · "
                    f"Preço médio R$ {_v_raw['preco_medio']:,.0f}",
                    "✅"
                )
                st.rerun()

        # Botão para recalcular projeção
        if _vendas and _estado_cfg.get("total_unidades", 0) > 0:
            if st.button(
                "🔄 Recalcular projeção de vendas",
                key=f"recalc_vgv_{_tkey_cfg}",
                help="Redistribui o VGV restante nos próximos meses"
            ):
                _vgv_auto = _calcula_vgv_projetado(
                    _vendas,
                    _estado_cfg.get("total_unidades", 0),
                    _estado_cfg.get("cronograma", {}),
                    _estado_cfg.get("data_inicio", {"ano": 2024, "mes": 1}),
                    _estado_cfg.get("unidades_report"),
                )
                _estado_cfg["vgv"] = _vgv_auto
                save_rolling(_titulo_cfg, force=True)
                safe_toast("VGV recalculado!", "🔄")
                st.rerun()

    with st.expander("💰 Recebíveis — Contas a Receber", expanded=True):
        st.caption(
            "Suba o relatório 'Contas a Receber — Recebíveis' exportado do SIENGE. "
            "Exporte sempre com data inicial = hoje para ter apenas recebimentos futuros. "
            "O painel detalhado aparece na aba 📅 Rolling Forecast → visão Caixa."
        )

        _rec = _estado_cfg.get("recebiveis")
        if _rec:
            _arq_rec_nome = _rec.get("arquivo_nome","?")
            _dt_rec = ""
            try:
                from datetime import datetime as _dtt
                _dt_rec = _dtt.fromisoformat(
                    _rec.get("data_upload","")
                ).strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass
            _n_pe = len(_rec.get("unidades_permuta",[]))
            st.success(
                f"✅ **{_arq_rec_nome}** · {_dt_rec}\n\n"
                f"Total recebível: **{fmt(_rec.get('total_recebiveis',0))}** · "
                f"FI: **{fmt(_rec.get('total_fi',0))}** · "
                f"PM: **{fmt(_rec.get('total_pm',0))}**"
                + (f" · ⚠️ {_n_pe} permuta(s) excluída(s)" if _n_pe else "")
            )
            if st.button("🔄 Substituir recebíveis",
                         key=f"sub_rec_{_tkey_cfg}"):
                _estado_cfg["recebiveis"] = None
                save_rolling(_titulo_cfg, force=True)
                safe_toast("Recebíveis removidos. Suba a nova versão.", "🔄")
                st.rerun()
        else:
            _arq_rec_up = st.file_uploader(
                "Selecione o relatório de recebíveis (.xlsx)",
                type=["xlsx","xls"],
                key=f"up_rec_{_tkey_cfg}",
                label_visibility="collapsed"
            )
            if _arq_rec_up:
                _rec_raw = parse_recebiveis_sienge(
                    _arq_rec_up.read(), _arq_rec_up.name
                )
                if "erro" in _rec_raw:
                    st.error(f"❌ {_rec_raw['erro']}")
                else:
                    _estado_cfg["recebiveis"] = _rec_raw
                    save_rolling(_titulo_cfg, force=True)
                    safe_toast(
                        f"✅ Recebíveis carregados: "
                        f"R$ {_rec_raw['total_recebiveis']:,.0f} · "
                        f"FI R$ {_rec_raw['total_fi']:,.0f} · "
                        f"{len(_rec_raw.get('unidades_permuta',[]))} permuta(s) excluída(s)",
                        "✅"
                    )
                    st.rerun()

    # DRE mensal
    with st.expander("📋 DRE Mensal (histórico real)", expanded=False):
        st.caption(
            "Suba a DRE exportada do SIENGE mês a mês a partir de 2026. "
            "A DRE anual de 2025 já está carregada na aba DRE Analítica."
        )
        # Mostra o que já está carregado
        _emp_data_dre = st.session_state.clientes[cliente_sel]["empresas"].get(_spe_sel, {})
        _fonte_dre = _emp_data_dre.get("fonte", "Fixo")
        _nome_dre  = _emp_data_dre.get("nome", _spe_sel)
        st.caption(
            f"DRE atual: **{_nome_dre}** · "
            f"Fonte: {'Upload' if _fonte_dre == 'Upload' else 'Dados padrão'}"
        )
        # Uploader para nova DRE mensal
        _tipo_up = "SIENGE"
        st.caption("Arquivo Excel exportado do SIENGE — Demonstrativo de Resultado.")
        _arq_dre_up = st.file_uploader(
            "Selecione o arquivo DRE (.xlsx)",
            type=["xlsx","xls"],
            key=f"up_dre_cfg_{_tkey_cfg}",
            label_visibility="collapsed"
        )
        if _arq_dre_up:
            _bdata = _arq_dre_up.read()
            _res_dre = parse_sienge(_bdata)
            if "erro" in _res_dre:
                st.error(f"❌ {_res_dre['erro']}")
            else:
                st.success(f"✅ Arquivo lido: {_arq_dre_up.name}")
                if st.button("✅ Aplicar DRE", type="primary", key=f"apply_dre_cfg_{_tkey_cfg}"):
                    _d = _res_dre["dados"]
                    _nova = {
                        "nome": _spe_sel, "fonte": "Upload",
                        "rec_bruta": _d["rec_bruta"], "imp_rec": _d["imp_rec"],
                        "cpv": _d["cpv"], "desp_op": _d["desp_op"],
                        "res_fin": _d["res_fin"], "ir": _d["ir"],
                        "rec_bdi":  _d.get("rec_bdi",  [0.0]*12),
                        "desp_bdi": _d.get("desp_bdi", [0.0]*12),
                        "raw_lines": _parse_sienge_full(_bdata)
                    }
                    st.session_state.clientes[cliente_sel]["empresas"][_spe_sel] = _nova
                    save_state()
                    safe_toast(f"✅ DRE atualizada!", "✅")
                    st.rerun()

    st.divider()

    # ══════════════════════════════════════════════════════════════════
    # BLOCO 2 — PARÂMETROS DE RECEITA (para Rolling Forecast)
    # ══════════════════════════════════════════════════════════════════
    st.markdown("### 💰 Bloco 2 — Parâmetros de Receita")
    st.caption("Usados na aba Rolling Forecast para projetar receita futura.")

    # Deriva horizonte da SPE selecionada (necessário para POC e BDI mensal)
    _cr_cfg     = _estado_cfg.get("cronograma", {})
    _di_cfg     = _cr_cfg.get("data_inicio", _estado_cfg.get("data_inicio", {"ano": 2024, "mes": 1}))
    _df_cfg     = _cr_cfg.get("data_fim",    _estado_cfg.get("data_fim",    {"ano": 2026, "mes": 12}))
    _N_cfg      = max(1, min((_df_cfg["ano"] - _di_cfg["ano"]) * 12 + (_df_cfg["mes"] - _di_cfg["mes"]) + 1, 120))
    _LABELS_cfg = gen_labels(_N_cfg, _di_cfg)


    st.divider()

    # ══════════════════════════════════════════════════════════════════
    # BLOCO 3 — PARÂMETROS GERAIS
    # ══════════════════════════════════════════════════════════════════
    st.markdown("### ⚙️ Bloco 3 — Parâmetros Gerais")

    with st.expander("📐 BDI e CUB", expanded=False):
        _b1, _b2, _b3 = st.columns(3)
        _bdi_base = _b1.number_input("BDI base (%)", value=_estado_cfg.get("bdi_rate",14.0),
                                      step=0.5, format="%.1f", key=f"bdi_cfg_{_tkey_cfg}")
        _cub_m    = _b2.number_input("CUB mensal (%)", value=_estado_cfg.get("cub_mensal",0.5),
                                      min_value=0.0, max_value=5.0, step=0.1, format="%.2f",
                                      key=f"cub_cfg_{_tkey_cfg}")
        _estado_cfg.update({"bdi_rate":_bdi_base, "cub_mensal":_cub_m})
        st.caption(f"BDI base: {_bdi_base:.1f}% · CUB: {_cub_m:.2f}%/mês (~{_cub_m*12:.1f}%/ano)")

        # BDI mensal por tabela
        st.markdown("**BDI por mês (futuros):**")
        _meses_fut = [m+1 for m in range(_N_cfg) if (m+1) not in _estado_cfg.get("meses_reais",{})]
        if _meses_fut:
            _bdi_m_cfg = _estado_cfg.get("bdi_mensal",[_bdi_base]*_N_cfg)
            _bdi_df = pd.DataFrame({
                "Mês": [_LABELS_cfg[m-1] for m in _meses_fut],
                "BDI (%)": [_bdi_m_cfg[m-1] if m-1 < len(_bdi_m_cfg) else _bdi_base for m in _meses_fut],
            })
            try:
                _bdi_ed = st.data_editor(
                    _bdi_df,
                    column_config={
                        "Mês": st.column_config.TextColumn("Mês", disabled=True),
                        "BDI (%)": st.column_config.NumberColumn("BDI (%)", min_value=0.0,
                                                                   max_value=100.0, step=0.5, format="%.1f%%"),
                    },
                    hide_index=True, use_container_width=True,
                    key=f"bdi_ed_cfg_{_tkey_cfg}"
                )
                for _ii, _m in enumerate(_meses_fut):
                    if _m-1 < len(_estado_cfg["bdi_mensal"]):
                        _estado_cfg["bdi_mensal"][_m-1] = float(_bdi_ed.iloc[_ii]["BDI (%)"])
            except Exception:
                st.dataframe(_bdi_df, use_container_width=True)

    st.divider()

    # Botão salvar
    _col_sv, _ = st.columns([1,3])
    with _col_sv:
        if st.button("💾 Salvar todas as configurações", type="primary",
                     use_container_width=True, key=f"save_all_cfg_{_tkey_cfg}"):
            save_rolling(_titulo_cfg, force=True)
            safe_toast("Configurações salvas!", "💾")


def build_dre_projetada(emp_base, estado, visao, N, LABELS, data_inicio):
    """
    Monta DRE projetada com N meses.
    Combina dados reais (DRE histórica) com projeção futura.

    Parâmetros:
      emp_base    : dict com rec_bruta, imp_rec, cpv, desp_op, res_fin, ir (12 valores reais)
      estado      : dict do rolling state da SPE
      visao       : "💰 Caixa" | "📋 Competência" | "🏗️ POC"
      N           : número de meses da obra
      LABELS      : lista de strings ["Jul/24", "Ago/24", ...]
      data_inicio : {"ano": int, "mes": int}

    Retorna dict com listas de N floats para cada linha da DRE.
    """
    import datetime

    # ── Dados históricos (12 meses anuais) ───────────────────────────
    rb_hist   = list(emp_base.get("rec_bruta", [0.0]*12))
    imp_hist  = list(emp_base.get("imp_rec",   [0.0]*12))
    cpv_hist  = list(emp_base.get("cpv",       [0.0]*12))
    dop_hist  = list(emp_base.get("desp_op",   [0.0]*12))
    rf_hist   = list(emp_base.get("res_fin",   [0.0]*12))
    ir_hist   = list(emp_base.get("ir",        [0.0]*12))

    # n_hist_len = quantos meses tem a DRE histórica (normalmente 12)
    n_hist_len = len(rb_hist)

    # Descobre em qual índice do horizonte (0-based) começa a DRE histórica.
    # A DRE histórica representa o ano de 2025 (jan a dez).
    # O horizonte pode começar antes (ex: abr/2023).
    import datetime as _dt_mod
    _inicio_dre_hist = {"ano": 2025, "mes": 1}  # jan/2025 — início fixo da DRE anual

    _idx_inicio_dre = (
        (_inicio_dre_hist["ano"] - data_inicio["ano"]) * 12 +
        (_inicio_dre_hist["mes"] - data_inicio["mes"])
    )
    _idx_inicio_dre = max(0, _idx_inicio_dre)
    _idx_fim_dre    = _idx_inicio_dre + n_hist_len  # exclusive

    # n_hist = índice até onde temos dados históricos (fim da DRE)
    n_hist = _idx_fim_dre

    # Offset: quantos meses o horizonte completo começa ANTES da obra
    # Ex: horizonte começa abr/23, obra começa jul/24 → offset = 15
    _cr = estado.get("cronograma", {})
    _cr_di = _cr.get("data_inicio", data_inicio) if _cr else data_inicio
    _offset_obra = (
        (_cr_di["ano"] - data_inicio["ano"]) * 12 +
        (_cr_di["mes"] - data_inicio["mes"])
    )
    _offset_obra = max(0, _offset_obra)

    # ── Médias históricas (para projeção de custos não-obra) ──────────
    def _media(lst):
        vals = [v for v in lst if v != 0]
        return sum(vals)/len(vals) if vals else 0.0

    dop_media  = _media(dop_hist)   # negativo
    rf_media   = _media(rf_hist)
    ir_media   = _media(ir_hist)    # negativo
    imp_pct_hist = (sum(imp_hist) / sum(rb_hist)) if sum(rb_hist) != 0 else 0.0
    imp_pct_proj = 0.04  # RET: 4% fixo para meses projetados
    ir_pct     = (sum(ir_hist)  / sum(rb_hist)) if sum(rb_hist) != 0 else 0.0
    cub_m      = estado.get("cub_mensal", 0.5) / 100  # ex: 0.005

    # ── CFF: custos planejados por mês ───────────────────────────────
    _cr       = estado.get("cronograma", {})
    _custos_m = _cr.get("custos_por_mes", [])
    _meses_m  = _cr.get("meses", [])

    def _cpv_cff(mes_idx):
        """CPV do mês pelo CFF, com CUB acumulado."""
        _base = datetime.date(data_inicio["ano"], data_inicio["mes"], 1)
        _atual = datetime.date(
            _base.year + (_base.month + mes_idx - 1) // 12,
            (_base.month + mes_idx - 1) % 12 + 1, 1
        )
        _cr_ini = _cr.get("data_inicio", data_inicio)
        _offset = (
            (_atual.year - _cr_ini["ano"]) * 12 +
            (_atual.month - _cr_ini["mes"])
        )
        if 0 <= _offset < len(_custos_m):
            _custo = _custos_m[_offset]
            _fator_cub = (1 + cub_m) ** mes_idx
            return -abs(_custo * _fator_cub)
        return 0.0

    # ── Receita por visão ─────────────────────────────────────────────
    vgv_cfg   = estado.get("vgv", {})

    # Recebíveis reais (se disponível)
    _recebiveis = estado.get("recebiveis", {})
    _rec_por_mes = _recebiveis.get("por_mes", {}) if _recebiveis else {}
    _rec_pm_mes  = _recebiveis.get("pm_por_mes", {}) if _recebiveis else {}
    _rec_fi_mes  = _recebiveis.get("fi_por_mes", {}) if _recebiveis else {}
    _tem_recebiveis = bool(_rec_por_mes)

    # Fator CUB para ajuste de valores futuros
    # Aplica a partir da data de exportação do relatório
    _data_exp_str = _recebiveis.get("data_exportacao", "") if _recebiveis else ""
    try:
        _data_exp = datetime.date(
            int(_data_exp_str[6:10]),
            int(_data_exp_str[3:5]),
            1
        )
    except Exception:
        _data_exp = datetime.date.today()

    # ── Mapa de receita real por mês da obra (Competência/POC) ───────
    # Vendas podem ter acontecido ANTES do início da obra (ex: 2023).
    # Precisamos mapear cada venda para o índice correto no horizonte.
    _vendas_state = estado.get("vendas", {})
    _vendas_por_mes = _vendas_state.get("vendas_por_mes", {}) if _vendas_state else {}

    # Constrói dict: índice_mes (0-based) → receita real vendida
    _rec_real_por_idx = {}
    _inicio_dt_v = datetime.date(data_inicio["ano"], data_inicio["mes"], 1)
    for _mes_str, _dados_mes in _vendas_por_mes.items():
        try:
            _ano_v  = int(_mes_str[:4])
            _mes_v  = int(_mes_str[5:7])
            _idx_v  = (_ano_v - _inicio_dt_v.year) * 12 + (_mes_v - _inicio_dt_v.month)
            if _idx_v < 0:
                # Venda anterior ao início da obra: acumula no mês 0
                _idx_v = 0
            if _idx_v < N:
                _rec_real_por_idx[_idx_v] = (
                    _rec_real_por_idx.get(_idx_v, 0.0) + _dados_mes.get("vgv", 0.0)
                )
        except Exception:
            pass

    # VGV total vendido (para POC)
    _vgv_total_vendido = _vendas_state.get("vgv_vendido", 0.0) if _vendas_state else 0.0
    # VGV total projetado (vendido + futuro)
    _vgv_total_proj = sum(
        vgv_cfg.get(mm, {}).get("unidades", 0) *
        vgv_cfg.get(mm, {}).get("preco", 350000.0)
        for mm in range(1, N + 1)
    )
    # VGV total completo = real vendido + futuro projetado
    _vgv_total_completo = _vgv_total_vendido + max(_vgv_total_proj - _vgv_total_vendido, 0)

    poc_cfg   = estado.get("poc_acum", [0]*N)
    pct_ent   = estado.get("pct_entrada", 7.0) / 100
    parc_un   = estado.get("parcela_un", 1500.0)
    mes_ent   = estado.get("mes_entrega", N)
    bdi_lista = estado.get("bdi_mensal", [estado.get("bdi_rate", 14.0)]*N)

    def _receita_mes(i):
        """Receita do mês i (0-based, relativo ao início do horizonte completo)."""
        # m_obra: índice 1-based relativo ao início da obra (para vgv_cfg)
        m_obra = i - _offset_obra + 1

        if "Competência" in visao or "Compet" in visao:
            # Meses com venda real: usa o valor real do relatório de vendas
            if i in _rec_real_por_idx:
                return _rec_real_por_idx[i]
            # Meses futuros: usa vgv_cfg com índice correto
            if m_obra < 1:
                return 0.0
            un  = vgv_cfg.get(m_obra, {}).get("unidades", 0)
            prc = vgv_cfg.get(m_obra, {}).get("preco", 350000.0)
            return float(un) * float(prc)

        elif "POC" in visao:
            poc_atual = poc_cfg[i] if i < len(poc_cfg) else 0
            poc_ant   = poc_cfg[i-1] if i > 0 and i-1 < len(poc_cfg) else 0
            delta_poc = max(poc_atual - poc_ant, 0) / 100
            return _vgv_total_completo * delta_poc

        else:  # Caixa
            if m_obra < 1:
                return 0.0

            # Se temos recebíveis reais, usa direto com ajuste CUB
            if _tem_recebiveis:
                # Calcula mês/ano deste índice
                _inicio_dt_c = datetime.date(data_inicio["ano"], data_inicio["mes"], 1)
                _mes_idx_c   = (_inicio_dt_c.month + i - 1) % 12 + 1
                _ano_idx_c   = _inicio_dt_c.year + (_inicio_dt_c.month + i - 1) // 12
                _chave_c     = f"{_ano_idx_c}-{_mes_idx_c:02d}"

                _val_rec = _rec_por_mes.get(_chave_c, 0.0)

                # Ajuste CUB: meses após a data de exportação
                _meses_apos_exp = (
                    (_ano_idx_c - _data_exp.year) * 12 +
                    (_mes_idx_c - _data_exp.month)
                )
                if _meses_apos_exp > 0:
                    _fator_cub = (1 + cub_m) ** _meses_apos_exp
                    _val_rec *= _fator_cub

                return _val_rec

            # Fallback: fórmula se não tem recebíveis
            un      = vgv_cfg.get(m_obra, {}).get("unidades", 0)
            prc     = vgv_cfg.get(m_obra, {}).get("preco", 350000.0)
            entrada = float(un) * float(prc) * pct_ent
            parcelas = 0.0
            for mm_obra in range(1, m_obra + 1):
                _un_mm = vgv_cfg.get(mm_obra, {}).get("unidades", 0)
                if _un_mm > 0 and mm_obra <= mes_ent:
                    parcelas += float(_un_mm) * parc_un
            saldo_ent = 0.0
            if m_obra == mes_ent:
                _total_ent  = _vgv_total_completo * pct_ent
                _total_parc = sum(
                    vgv_cfg.get(mm, {}).get("unidades", 0) * parc_un *
                    max(mes_ent - mm, 0)
                    for mm in range(1, mes_ent + 1)
                )
                saldo_ent = max(_vgv_total_completo - _total_ent - _total_parc, 0)
            return entrada + parcelas + saldo_ent

    # ── Receita BDI da Matriz (só para Matriz) ────────────────────────
    def _rec_bdi_mes(i, cpv_spe):
        bdi = bdi_lista[i] if i < len(bdi_lista) else estado.get("bdi_rate", 14.0)
        return abs(cpv_spe) * bdi / 100

    # ── Monta arrays de N meses ───────────────────────────────────────
    rec_bruta  = []
    imp_rec    = []
    cpv        = []
    desp_op    = []
    res_fin    = []
    ir         = []

    is_matriz = "matriz" in emp_base.get("nome", "").lower()

    for i in range(N):
        _drift = (1 + 0.005) ** i  # 0,5%/mês ≈ 6%/ano

        # Índice relativo dentro da DRE histórica (pode ser negativo se i < _idx_inicio_dre)
        _i_dre = i - _idx_inicio_dre

        if 0 <= _i_dre < n_hist_len:
            # ── Mês coberto pela DRE histórica ──────────────────────
            # CPV e custos: sempre da DRE histórica real
            cpv.append(float(cpv_hist[_i_dre]))
            desp_op.append(float(dop_hist[_i_dre]))
            res_fin.append(float(rf_hist[_i_dre]))
            ir.append(float(ir_hist[_i_dre]))

            # Receita: depende da visão
            if "Caixa" in visao:
                rec_bruta.append(float(rb_hist[_i_dre]))
                imp_rec.append(float(imp_hist[_i_dre]))
            else:
                # Competência ou POC: usa VGV real do relatório de vendas
                _rec_h = _receita_mes(i)
                _imp_h = float(imp_hist[_i_dre])
                rec_bruta.append(_rec_h)
                imp_rec.append(_imp_h)

        elif i < _idx_inicio_dre:
            # ── Mês anterior à DRE histórica (ex: 2023, 2024) ───────
            # Não temos dados reais — mas podemos ter vendas reais (Competência)
            _rec_h = _receita_mes(i)
            _imp_h = -abs(_rec_h * imp_pct_proj) if _rec_h != 0 else 0.0

            rec_bruta.append(_rec_h)
            imp_rec.append(_imp_h)
            # Antes do início da obra: apenas receita, sem custos
            if i < _offset_obra:
                cpv.append(0.0)
                desp_op.append(0.0)
                res_fin.append(0.0)
                ir.append(0.0)
            else:
                cpv.append(_cpv_cff(i) if not is_matriz else 0.0)
                desp_op.append(dop_media * _drift if dop_media != 0 else 0.0)
                res_fin.append(rf_media  * _drift if rf_media  != 0 else 0.0)
                ir.append(ir_media       * _drift if ir_media  != 0 else 0.0)

        else:
            # ── Mês futuro (após DRE histórica) ─────────────────────
            _rec     = _receita_mes(i)
            _cpv_fut = _cpv_cff(i) if not is_matriz else 0.0
            if is_matriz:
                _rec = _rec_bdi_mes(i, abs(_cpv_cff(i)))

            _dop  = dop_media * _drift if dop_media != 0 else 0.0
            _rf   = rf_media  * _drift if rf_media  != 0 else 0.0
            _ir_v = ir_media  * _drift if ir_media  != 0 else 0.0
            _imp  = -abs(_rec * imp_pct_proj) if _rec != 0 else 0.0

            rec_bruta.append(_rec)
            imp_rec.append(_imp)
            cpv.append(_cpv_fut)
            desp_op.append(_dop)
            res_fin.append(_rf)
            ir.append(_ir_v)

    # Calcula linhas derivadas
    rec_liq      = [rb + imp for rb, imp in zip(rec_bruta, imp_rec)]
    lucro_bruto  = [rl + c   for rl, c   in zip(rec_liq,   cpv)]
    ebitda       = [lb + dop for lb, dop in zip(lucro_bruto, desp_op)]
    lai          = [eb + rf  for eb, rf  in zip(ebitda,     res_fin)]
    lucro_liq    = [la + ir_v for la, ir_v in zip(lai, ir)]

    return {
        "labels":        LABELS,
        "rec_bruta":     rec_bruta,
        "imp_rec":       imp_rec,
        "rec_liq":       rec_liq,
        "cpv":           cpv,
        "lucro_bruto":   lucro_bruto,
        "desp_op":       desp_op,
        "ebitda":        ebitda,
        "res_fin":       res_fin,
        "lai":           lai,
        "ir":            ir,
        "lucro_liq":     lucro_liq,
        "n_hist":        _idx_fim_dre,   # índice até onde temos DRE histórica
        "idx_obra":      _idx_inicio_dre + _idx_inicio_dre,  # índice de início da obra
    }


def render_rolling_forecast():
    import datetime
    st.markdown("## 📅 Rolling Forecast")
    st.caption("DRE projetada até o fim da obra — passado real + futuro projetado.")

    # ── Empresas ativas ───────────────────────────────────────────────
    _todas = list(st.session_state.clientes[cliente_sel]["empresas"].keys())
    _ativas = [k for k in _todas
               if st.session_state.get("empresas_ativas", {}).get(k, True)]
    if not _ativas:
        st.warning("Nenhuma empresa ativa."); return

    # ── Monta DRE projetada por empresa ──────────────────────────────
    _dres_proj = {}
    _N_max = 0

    for _k in _ativas:
        _emp = st.session_state.clientes[cliente_sel]["empresas"][_k]
        _tit = _emp.get("nome", _k)
        _est = get_rolling_state(_tit)
        _cr  = _est.get("cronograma", {})

        # Período: começa na primeira venda ou início da obra
        # (o que for mais cedo) e termina no fim da obra
        _di  = _est.get("data_inicio", {"ano": 2024, "mes": 1})
        _df  = _est.get("data_fim",    {"ano": 2026, "mes": 12})
        if _cr:
            _di = _cr.get("data_inicio", _di)
            _df = _cr.get("data_fim",    _df)

        # Verifica se há vendas anteriores ao início da obra
        _vendas_st = _est.get("vendas", {})
        if _vendas_st and _vendas_st.get("vendas_por_mes"):
            _meses_vendas = sorted(_vendas_st["vendas_por_mes"].keys())
            if _meses_vendas:
                _primeira_venda = _meses_vendas[0]  # "AAAA-MM"
                _pv_ano = int(_primeira_venda[:4])
                _pv_mes = int(_primeira_venda[5:7])
                # Se a primeira venda é anterior ao início da obra, recua o início
                _di_dt  = (_di["ano"] - 2000) * 12 + _di["mes"]
                _pv_dt  = (_pv_ano  - 2000) * 12 + _pv_mes
                if _pv_dt < _di_dt:
                    _di = {"ano": _pv_ano, "mes": _pv_mes}

        _N = (
            (_df["ano"] - _di["ano"]) * 12 +
            (_df["mes"] - _di["mes"]) + 1
        )
        _N = max(1, min(_N, 120))
        _LABELS = gen_labels(_N, _di)
        _N_max = max(_N_max, _N)

        _dre_p = build_dre_projetada(_emp, _est, visao, _N, _LABELS, _di)
        _dres_proj[_k] = {"dre": _dre_p, "N": _N, "labels": _LABELS, "titulo": _tit}

    if not _dres_proj:
        st.info("Nenhuma empresa com dados."); return

    # ── Consolida se mais de uma empresa ativa ────────────────────────
    if len(_ativas) == 1:
        _k_unico = _ativas[0]
        _dre_final = _dres_proj[_k_unico]["dre"]
        _N_final   = _dres_proj[_k_unico]["N"]
        _LABELS_final = _dres_proj[_k_unico]["labels"]
        _titulo_final = _dres_proj[_k_unico]["titulo"]
    else:
        # Soma: alinha pelo índice (meses podem ter tamanhos diferentes)
        _N_final   = _N_max
        _LABELS_final = gen_labels(_N_final,
            list(_dres_proj.values())[0]["dre"]["labels"] and
            {"ano": 2024, "mes": 1} or {"ano": 2024, "mes": 1})

        def _soma_campo(campo):
            resultado = [0.0] * _N_final
            for _kk, _vv in _dres_proj.items():
                _vals = _vv["dre"].get(campo, [])
                for _ii, _v in enumerate(_vals):
                    if _ii < _N_final:
                        resultado[_ii] += float(_v)
            return resultado

        _dre_final = {
            campo: _soma_campo(campo)
            for campo in ["rec_bruta","imp_rec","rec_liq","cpv",
                          "lucro_bruto","desp_op","ebitda","res_fin","lai","ir","lucro_liq"]
        }
        _dre_final["labels"] = _LABELS_final
        _dre_final["n_hist"] = min(
            d["dre"]["n_hist"] for d in _dres_proj.values()
        )
        _titulo_final = "Consolidado"

    st.caption(
        f"**{_titulo_final}** · {_N_final} meses · "
        f"Visão: {visao} · "
        f"{'Consolidado (' + str(len(_ativas)) + ' empresas)' if len(_ativas) > 1 else ''}"
    )
    st.divider()

    # ── Painel de Estoque de Unidades ─────────────────────────────────
    _un_rpt = None
    if len(_ativas) == 1:
        _k_un = _ativas[0]
        _emp_un = st.session_state.clientes[cliente_sel]["empresas"][_k_un]
        _tit_un = _emp_un.get("nome", _k_un)
        _est_un = get_rolling_state(_tit_un)
        _un_rpt = _est_un.get("unidades_report")
    else:
        # Consolidado: pega o primeiro com dados
        for _k_un in _ativas:
            _emp_un = st.session_state.clientes[cliente_sel]["empresas"][_k_un]
            _tit_un = _emp_un.get("nome", _k_un)
            _est_un = get_rolling_state(_tit_un)
            if _est_un.get("unidades_report"):
                _un_rpt = _est_un.get("unidades_report")
                break

    if _un_rpt:
        with st.expander("🏢 Estoque de Unidades", expanded=False):
            _uc1, _uc2, _uc3, _uc4 = st.columns(4)
            _uc1.metric("Total",       _un_rpt["total_unidades"],
                        help="Aptos + Salas (sem garagens)")
            _uc2.metric("Vendidas",    _un_rpt["vendidas"])
            _uc3.metric("Permuta",     _un_rpt["permuta"],
                        help=", ".join(_un_rpt.get("unidades_permuta", [])))
            _uc4.metric("Disponíveis", _un_rpt["disponiveis"])
            st.caption(
                f"VGV vendido: **{fmt(_un_rpt['vgv_vendido'])}** · "
                f"Preço médio: **{fmt(_un_rpt['preco_medio'])}** · "
                f"Ainda a vender: **{_un_rpt['disponiveis']} unidades**"
            )
            _un_dt2 = ""
            try:
                from datetime import datetime as _dtt2
                _un_dt2 = _dtt2.fromisoformat(
                    _un_rpt.get("data_upload","")
                ).strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass
            if _un_dt2:
                st.caption(f"📅 Atualizado em: {_un_dt2}")

    # ── Painel de Recebíveis (só visão Caixa) ────────────────────────
    if "Caixa" in visao:
        # Coleta recebíveis de todas as empresas ativas
        _tem_rec_alguma = False
        for _k_rec in _ativas:
            _emp_r = st.session_state.clientes[cliente_sel]["empresas"][_k_rec]
            _tit_r = _emp_r.get("nome", _k_rec)
            _est_r = get_rolling_state(_tit_r)
            if _est_r.get("recebiveis"):
                _tem_rec_alguma = True
                break

        if _tem_rec_alguma:
            st.markdown("### 💰 Recebíveis — Contas a Receber")
            st.caption(
                "Fonte: relatório SIENGE. PE (permuta) excluído. "
                "Valores futuros ajustados pelo CUB configurado."
            )

            # Para cada empresa ativa com recebíveis
            for _k_rec in _ativas:
                _emp_r = st.session_state.clientes[cliente_sel]["empresas"][_k_rec]
                _tit_r = _emp_r.get("nome", _k_rec)
                _est_r = get_rolling_state(_tit_r)
                _rec_r = _est_r.get("recebiveis")
                if not _rec_r: continue

                if len(_ativas) > 1:
                    st.markdown(f"**{_k_rec}**")

                _rt_r = _rec_r.get("resumo_tipos", {})
                _nomes_tc = {
                    "PM": "Parcelas Mensais",
                    "FI": "Financiamento Bancário",
                    "CH": "À Vista / Cheque",
                    "RF": "Reforço",
                    "PC": "Parcela Complementar",
                    "PI": "Entrada / Sinal",
                    "PE": "Permuta ⚠️ (excluído)",
                }
                _rows_rt = []
                for _tc in ["PM","FI","CH","RF","PC","PI","PE"]:
                    if _tc not in _rt_r: continue
                    _d = _rt_r[_tc]
                    _rows_rt.append({
                        "Tipo":     f"{_tc} — {_nomes_tc.get(_tc,_tc)}",
                        "Unidades": _d["unidades"],
                        "Parcelas": _d["parcelas"],
                        "Total":    _d["valor"],
                        "":         "❌" if _tc == "PE" else "✅",
                    })

                if _rows_rt:
                    _df_rt = pd.DataFrame(_rows_rt)
                    try:
                        st.dataframe(
                            _df_rt.style.format({"Total": "R$ {:,.0f}"}),
                            use_container_width=True,
                            hide_index=True,
                            height=min(250, 38 + len(_rows_rt)*35)
                        )
                    except Exception:
                        st.dataframe(_df_rt, use_container_width=True, hide_index=True)


                _pe_list = _rec_r.get("unidades_permuta",[])
                if _pe_list:
                    st.warning(
                        f"⚠️ **{len(_pe_list)} unidade(s) em permuta excluídas:** "
                        f"{', '.join(_pe_list)}"
                    )

            st.divider()
        else:
            st.info(
                "ℹ️ Carregue o relatório de Recebíveis em "
                "**⚙️ Configurações → Recebíveis** para ver o fluxo de caixa real."
            )
            st.divider()

    # ── GRÁFICO: DRE mensal projetada ─────────────────────────────────
    _n_hist = _dre_final.get("n_hist", 12)
    _labels_all = _dre_final.get("labels", _LABELS_final)

    _fg = go.Figure()

    # Área de histórico vs projeção
    if _n_hist < _N_final:
        _fg.add_vrect(
            x0=0, x1=_n_hist - 0.5,
            fillcolor="rgba(200,220,255,0.12)",
            layer="below", line_width=0,
            annotation_text="Histórico",
            annotation_font_size=10,
            annotation_position="top left"
        )
        _fg.add_vrect(
            x0=_n_hist - 0.5, x1=_N_final - 1,
            fillcolor="rgba(255,240,200,0.12)",
            layer="below", line_width=0,
            annotation_text="Projeção",
            annotation_font_size=10,
            annotation_position="top left"
        )

    # Receita Bruta
    _fg.add_bar(
        x=_labels_all,
        y=_dre_final["rec_bruta"],
        name="Receita Bruta",
        marker_color=CHART_TEAL,
        opacity=0.85
    )
    # CPV (negativo)
    _fg.add_bar(
        x=_labels_all,
        y=_dre_final["cpv"],
        name="CPV (Custo Obra)",
        marker_color=SOFT_RED,
        opacity=0.75
    )
    # Lucro Líquido (linha)
    _fg.add_scatter(
        x=_labels_all,
        y=_dre_final["lucro_liq"],
        name="Lucro Líquido",
        mode="lines+markers",
        line=dict(color=GOLD, width=2.5),
        marker=dict(size=5)
    )
    _fg.add_hline(y=0, line_dash="dash", line_color=GRAY, line_width=1)
    _fg.update_layout(
        title=f"DRE Projetada — {_titulo_final}",
        barmode="relative",
        **PL(420)
    )
    _fg.update_xaxes(showgrid=False, tickfont=dict(size=9))
    _fg.update_yaxes(gridcolor=BORDER, tickprefix="R$ ", tickformat=",.0f")
    st.plotly_chart(_fg, use_container_width=True)

    st.divider()

    # ══════════════════════════════════════════════════════════════════
    # VISÃO CAIXA: layout diferente das outras visões
    # ══════════════════════════════════════════════════════════════════
    if "Caixa" in visao:

        # ── Prepara dados de fluxo de caixa ──────────────────────────
        _entradas_fc  = [max(v, 0) for v in _dre_final["rec_bruta"]]
        _saidas_fc    = [abs(c) + abs(d)
                        for c, d in zip(_dre_final["cpv"], _dre_final["desp_op"])]
        _saldo_fc     = [e - s for e, s in zip(_entradas_fc, _saidas_fc)]
        _saldo_acum_fc= []
        _acum_fc = 0.0
        for _sv in _saldo_fc:
            _acum_fc += _sv
            _saldo_acum_fc.append(_acum_fc)

        _min_saldo_fc  = min(_saldo_acum_fc) if _saldo_acum_fc else 0
        _max_saldo_fc  = max(_saldo_acum_fc) if _saldo_acum_fc else 0
        _saldo_final_fc= _saldo_acum_fc[-1] if _saldo_acum_fc else 0

        # Pega total de recebíveis do estado (se disponível)
        _total_rec_fc = 0.0
        _total_fi_fc  = 0.0
        _total_pm_fc  = 0.0
        for _k_fc in _ativas:
            _emp_fc = st.session_state.clientes[cliente_sel]["empresas"][_k_fc]
            _tit_fc = _emp_fc.get("nome", _k_fc)
            _est_fc = get_rolling_state(_tit_fc)
            _rec_fc = _est_fc.get("recebiveis", {})
            if _rec_fc:
                _total_rec_fc += _rec_fc.get("total_recebiveis", 0.0)
                _total_fi_fc  += _rec_fc.get("total_fi", 0.0)
                _total_pm_fc  += _rec_fc.get("total_pm", 0.0)

        # ── 4 KPIs executivos ─────────────────────────────────────────
        st.markdown("### 💰 Posição de Caixa")
        _ck1, _ck2, _ck3, _ck4 = st.columns(4)

        _ck1.metric(
            "Total a Receber",
            fmt(_total_rec_fc) if _total_rec_fc else fmt(sum(_entradas_fc)),
            help=(
                "Soma de todas as parcelas (PM), financiamentos (FI) "
                "e recebimentos à vista futuros. Fonte: relatório de recebíveis SIENGE."
            )
        )
        _ck2.metric(
            "Repasse Bancário (FI)",
            fmt(_total_fi_fc) if _total_fi_fc else "—",
            help=(
                "Valor que os bancos vão transferir para a Brocks "
                "quando os compradores financiarem seus imóveis. "
                "Maior evento de caixa do empreendimento."
            )
        )
        if _min_saldo_fc < 0:
            _ck3.metric(
                "🔴 Necessidade de Capital",
                fmt(abs(_min_saldo_fc)),
                "Pico negativo do saldo",
                delta_color="inverse",
                help=(
                    "O caixa fica negativo neste valor em algum momento. "
                    "Indica necessidade de aporte ou financiamento de obra."
                )
            )
        else:
            _ck3.metric(
                "✅ Saldo Mínimo",
                fmt(_min_saldo_fc),
                "Caixa sempre positivo",
                help="O caixa nunca fica negativo. Menor saldo ao longo do período."
            )

        _ck4.metric(
            "Saldo Final",
            fmt(_saldo_final_fc),
            f"Ao fim da obra",
            delta_color="normal" if _saldo_final_fc >= 0 else "inverse",
            help=(
                "Caixa acumulado ao final do empreendimento. "
                "Representa o dinheiro efetivamente disponível após "
                "receber tudo e pagar tudo."
            )
        )

        st.divider()

        # ── Gráfico único: Entradas vs Saídas ────────────────────────
        st.markdown("### 📊 Fluxo de Caixa — Entradas vs Saídas")
        st.caption(
            "Barras verdes = entradas (recebimentos). "
            "Barras vermelhas = saídas (obra + despesas). "
            "Linha dourada = saldo acumulado."
        )

        _fg_fc = go.Figure()
        _fg_fc.add_bar(
            x=_labels_all, y=_entradas_fc,
            name="Entradas", marker_color=CHART_TEAL, opacity=0.85
        )
        _fg_fc.add_bar(
            x=_labels_all, y=[-s for s in _saidas_fc],
            name="Saídas", marker_color=SOFT_RED, opacity=0.75
        )
        _fg_fc.add_scatter(
            x=_labels_all, y=_saldo_acum_fc,
            name="Saldo acumulado",
            mode="lines+markers",
            line=dict(color=GOLD, width=2.5),
            marker=dict(size=5)
        )
        _fg_fc.add_hline(y=0, line_dash="dash", line_color=GRAY, line_width=1.5)

        # Marca o mês do repasse bancário se disponível
        if _total_fi_fc > 0:
            # Encontra o mês com maior entrada (provavelmente o FI)
            _idx_fi = _entradas_fc.index(max(_entradas_fc))
            _fg_fc.add_vline(
                x=_labels_all[_idx_fi],
                line_dash="dot", line_color=CHART_BLUE, line_width=1.5
            )
            _fg_fc.add_annotation(
                x=_labels_all[_idx_fi], y=1, yref="paper",
                text="Repasse", showarrow=False,
                font=dict(size=10, color=CHART_BLUE),
                xanchor="left", yanchor="bottom"
            )

        _fg_fc.update_layout(
            barmode="relative",
            **PL(420)
        )
        _fg_fc.update_xaxes(showgrid=False, tickfont=dict(size=9))
        _fg_fc.update_yaxes(
            gridcolor=BORDER, tickprefix="R$ ", tickformat=",.0f"
        )
        st.plotly_chart(_fg_fc, use_container_width=True)

        # Alerta de necessidade de capital
        if _min_saldo_fc < 0:
            _mes_pico = _saldo_acum_fc.index(_min_saldo_fc)
            st.error(
                f"⚠️ **Necessidade de capital:** caixa fica negativo em "
                f"**{_labels_all[_mes_pico]}** com pico de **{fmt(abs(_min_saldo_fc))}**. "
                f"Considere linha de crédito ou antecipação de recebíveis."
            )
        else:
            st.success(
                f"✅ Caixa positivo ao longo de toda a projeção. "
                f"Saldo final: **{fmt(_saldo_final_fc)}**"
            )

        st.divider()

        # ── Recebíveis detalhados (ocultável) ────────────────────────
        with st.expander("📋 Detalhe dos Recebíveis", expanded=False):
            st.caption(
                "Fonte: relatório SIENGE. "
                "PE (permuta) excluído. "
                "Valores ajustados pelo CUB configurado."
            )
            for _k_rec in _ativas:
                _emp_r = st.session_state.clientes[cliente_sel]["empresas"][_k_rec]
                _tit_r = _emp_r.get("nome", _k_rec)
                _est_r = get_rolling_state(_tit_r)
                _rec_r = _est_r.get("recebiveis")
                if not _rec_r: continue

                if len(_ativas) > 1:
                    st.markdown(f"**{_k_rec}**")

                _rt_r = _rec_r.get("resumo_tipos", {})
                _nomes_tc = {
                    "PM": "Parcelas Mensais",
                    "FI": "Financiamento Bancário",
                    "CH": "À Vista / Cheque",
                }
                _rows_rt = []
                for _tc in ["PM", "FI", "CH"]:
                    if _tc not in _rt_r: continue
                    _d = _rt_r[_tc]
                    _rows_rt.append({
                        "Tipo":     f"{_tc} — {_nomes_tc.get(_tc, _tc)}",
                        "Unidades": _d["unidades"],
                        "Parcelas": _d["parcelas"],
                        "Total":    _d["valor"],
                    })
                if _rows_rt:
                    _df_rt = pd.DataFrame(_rows_rt)
                    try:
                        st.dataframe(
                            _df_rt.style.format({"Total": "R$ {:,.0f}"}),
                            use_container_width=True,
                            hide_index=True,
                            height=min(180, 38 + len(_rows_rt) * 35)
                        )
                    except Exception:
                        st.dataframe(_df_rt, use_container_width=True, hide_index=True)

                _pe_list = _rec_r.get("unidades_permuta", [])
                if _pe_list:
                    st.caption(
                        f"⚠️ Permuta excluída: {', '.join(_pe_list)}"
                    )

        # ── DRE completa (ocultável) ──────────────────────────────────
        with st.expander("📊 DRE Projetada Mês a Mês", expanded=False):
            _linhas_dre = [
                ("Receita Bruta",       "rec_bruta",    False),
                ("(-) Impostos s/ Rec", "imp_rec",      False),
                ("Receita Líquida",     "rec_liq",      True),
                ("(-) CPV",             "cpv",          False),
                ("Lucro Bruto",         "lucro_bruto",  True),
                ("(-) Despesas Op.",    "desp_op",      False),
                ("EBITDA",              "ebitda",       True),
                ("Resultado Financeiro","res_fin",      False),
                ("LAIR",                "lai",          False),
                ("(-) IR/CSLL",         "ir",           False),
                ("Lucro Líquido",       "lucro_liq",    True),
            ]
            _df_rows = {}
            for _nome, _campo, _ in _linhas_dre:
                _df_rows[_nome] = _dre_final.get(_campo, [0]*_N_final)
            _df_dre_c = pd.DataFrame(_df_rows, index=_labels_all).T
            _df_dre_c["TOTAL"] = _df_dre_c.sum(axis=1)

            def _fmt_v(v):
                try:
                    fv = float(v)
                    return f"R$ {fv:,.0f}" if fv >= 0 else f"(R$ {abs(fv):,.0f})"
                except Exception:
                    return str(v)

            _totais_bold = [n for n, _, b in _linhas_dre if b]

            def _hl_dre_c(row):
                if row.name in _totais_bold:
                    return [f"background-color:{BLIGHT};font-weight:700"]*len(row)
                return [""]*len(row)

            try:
                st.dataframe(
                    _df_dre_c.style.format(_fmt_v).apply(_hl_dre_c, axis=1),
                    use_container_width=True,
                    height=420
                )
            except Exception:
                st.dataframe(_df_dre_c, use_container_width=True)

            _buf_rf = io.BytesIO()
            with pd.ExcelWriter(_buf_rf, engine="openpyxl") as _w:
                _df_dre_c.to_excel(_w, sheet_name="Rolling Forecast")
            st.download_button(
                "📥 Exportar DRE Projetada",
                data=_buf_rf.getvalue(),
                file_name=f"RollingForecast_{_titulo_final.replace(' ','_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_rolling_caixa"
            )

    # ══════════════════════════════════════════════════════════════════
    # VISÃO COMPETÊNCIA E POC: layout original (DRE + Fluxo)
    # ══════════════════════════════════════════════════════════════════
    else:
        _rb_total  = sum(_dre_final["rec_bruta"])
        _cpv_total = sum(_dre_final["cpv"])
        _ll_total  = sum(_dre_final["lucro_liq"])

        _cpv_pct = (abs(_cpv_total) / _rb_total * 100) if _rb_total else 0
        _ll_pct  = (_ll_total / _rb_total * 100) if _rb_total else 0

        _kp1, _kp2, _kp3 = st.columns(3)
        _kp1.metric("Receita Total",   fmt(_rb_total))
        _kp2.metric("CPV Total",       fmt(abs(_cpv_total)),
                    f"{_cpv_pct:.1f}% da receita")
        _kp3.metric("Lucro Líquido",   fmt(_ll_total),
                    f"{_ll_pct:.1f}% da receita")

        st.divider()

        with st.expander("📋 DRE Completa Mês a Mês", expanded=False):
            _linhas_dre = [
                ("Receita Bruta",       "rec_bruta",    False),
                ("(-) Impostos s/ Rec", "imp_rec",      False),
                ("Receita Líquida",     "rec_liq",      True),
                ("(-) CPV",             "cpv",          False),
                ("Lucro Bruto",         "lucro_bruto",  True),
                ("(-) Despesas Op.",    "desp_op",      False),
                ("EBITDA",              "ebitda",       True),
                ("Resultado Financeiro","res_fin",      False),
                ("LAIR",                "lai",          False),
                ("(-) IR/CSLL",         "ir",           False),
                ("Lucro Líquido",       "lucro_liq",    True),
            ]
            _df_rows = {}
            for _nome, _campo, _ in _linhas_dre:
                _df_rows[_nome] = _dre_final.get(_campo, [0]*_N_final)
            _df_dre = pd.DataFrame(_df_rows, index=_labels_all).T
            _df_dre["TOTAL"] = _df_dre.sum(axis=1)

            def _fmt_v2(v):
                try:
                    fv = float(v)
                    return f"R$ {fv:,.0f}" if fv >= 0 else f"(R$ {abs(fv):,.0f})"
                except Exception:
                    return str(v)

            _totais_bold2 = [n for n, _, b in _linhas_dre if b]

            def _hl_dre2(row):
                if row.name in _totais_bold2:
                    return [f"background-color:{BLIGHT};font-weight:700"]*len(row)
                return [""]*len(row)

            try:
                st.dataframe(
                    _df_dre.style.format(_fmt_v2).apply(_hl_dre2, axis=1),
                    use_container_width=True,
                    height=420
                )
            except Exception:
                st.dataframe(_df_dre, use_container_width=True)

            _buf_rf2 = io.BytesIO()
            with pd.ExcelWriter(_buf_rf2, engine="openpyxl") as _w:
                _df_dre.to_excel(_w, sheet_name="Rolling Forecast")
            st.download_button(
                "📥 Exportar DRE Projetada",
                data=_buf_rf2.getvalue(),
                file_name=f"RollingForecast_{_titulo_final.replace(' ','_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_rolling_dre"
            )

        # ── FLUXO DE CAIXA PROJETADO ──────────────────────────────────────
        st.divider()
        st.markdown("### 💸 Necessidade de Caixa")
        _entradas = [max(v, 0) for v in _dre_final["rec_bruta"]]
        _saidas   = [abs(c) + abs(d) for c, d in zip(_dre_final["cpv"], _dre_final["desp_op"])]
        _saldo    = [e - s for e, s in zip(_entradas, _saidas)]
        _saldo_acum = []
        _acum = 0.0
        for _sv in _saldo:
            _acum += _sv
            _saldo_acum.append(_acum)

        _fg_fc = go.Figure()
        _fg_fc.add_bar(x=_labels_all, y=_entradas,
                    name="Entradas", marker_color=CHART_TEAL, opacity=0.8)
        _fg_fc.add_bar(x=_labels_all, y=[-s for s in _saidas],
                    name="Saídas", marker_color=SOFT_RED, opacity=0.8)
        _fg_fc.add_scatter(x=_labels_all, y=_saldo_acum,
                        name="Saldo acumulado",
                        mode="lines+markers",
                        line=dict(color=GOLD, width=2.5),
                        marker=dict(size=5))
        _fg_fc.add_hline(y=0, line_dash="dash", line_color=GRAY, line_width=1)
        _fg_fc.update_layout(
            title="Fluxo de Caixa — Entradas vs Saídas",
            barmode="relative", **PL(380)
        )
        _fg_fc.update_xaxes(showgrid=False, tickfont=dict(size=9))
        _fg_fc.update_yaxes(gridcolor=BORDER, tickprefix="R$ ", tickformat=",.0f")
        st.plotly_chart(_fg_fc, use_container_width=True)

        _min_saldo = min(_saldo_acum) if _saldo_acum else 0
        if _min_saldo < 0:
            _mes_pico = _saldo_acum.index(_min_saldo)
            st.error(
                f"⚠️ **Necessidade de capital:** caixa fica negativo em "
                f"**{_labels_all[_mes_pico]}** com pico de **{fmt(abs(_min_saldo))}**."
            )
        else:
            st.success(f"✅ Caixa positivo ao longo de toda a projeção. Pico: {fmt(max(_saldo_acum))}")


# ── Roteamento ────────────────────────────────────────────────────────────────
if   _tab == TABS[0]: render_configuracoes()
elif _tab == TABS[1]: render_dre()
elif _tab == TABS[2]: render_resumo_obras()
elif _tab == TABS[3]: render_rolling_forecast()
elif _tab == TABS[4]: render_indicadores()
elif _tab == TABS[5]: render_sensibilidade()
elif _tab == TABS[6]: render_fcff_dcf()

# redeploy
