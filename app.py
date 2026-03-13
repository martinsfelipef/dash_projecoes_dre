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

from parser_template import parse_template_align
from rolling_forecast import (calc_competencia,calc_caixa,calc_poc,
                               build_dre_rolling,bdi_matriz_mensal)
try:
    from parser_cronograma_sienge import parse_cronograma_sienge
except ImportError:
    import pandas as _pd_s
    def parse_cronograma_sienge(data):
        df=_pd_s.read_excel(data,header=0)
        df.columns=[str(c).strip().lower() for c in df.columns]
        _m={'jan':0,'fev':1,'mar':2,'abr':3,'mai':4,'jun':5,
            'jul':6,'ago':7,'set':8,'out':9,'nov':10,'dez':11}
        res={'cpv_real':[0.0]*12,'dop_real':[0.0]*12,'rf_real':[0.0]*12,'ir_real':[0.0]*12}
        for col in df.columns:
            for ab,ix in _m.items():
                if ab in col:
                    for row in df.itertuples():
                        try: v=float(getattr(row,col.replace(' ','_'),0) or 0)
                        except: v=0.0
                        res['cpv_real'][ix]+=v
        return res

st.set_page_config(page_title="Dashboard Financeiro | Align",
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
    """Exibe tela de login centralizada e bonita."""
    st.markdown("""
    <style>
    /* Background for the entire page */
    [data-testid="stAppViewContainer"] > .main {
        background: #0A1118;
    }
    
    /* Centered Login Card */
    .login-card {
        max-width: 450px;
        margin: 8vh auto;
        background: #141A23;
        padding: 3rem 2.5rem;
        border-radius: 12px;
        box-shadow: 0 20px 40px rgba(0,0,0,0.4);
        border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .login-logo { text-align: center; margin-bottom: 2rem; }
    
    /* Typography inside the card */
    .login-title {
        color: #FFFFFF; font-size: 1.5rem; font-family: 'Inter', sans-serif;
        font-weight: 700; margin: 0 0 0.5rem; text-align: center;
    }
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
    .login-tabs {
        display: flex; gap: 8px; margin-bottom: 1.5rem;
    }
    .login-tab-btn {
        flex: 1; text-align: center; padding: 0.6rem;
        border-radius: 6px; font-weight: 600; font-size: 0.9rem;
        cursor: pointer;
    }
    .tab-active { background: #0A1118; border: 1px solid #1A2433; color: white; }
    .tab-inactive { color: #64748B; }
    
    /* Hide top padding and header initially to make login look full-screen */
    header[data-testid="stHeader"] {display: none;}
    </style>
    """, unsafe_allow_html=True)

    col_l, col_m, col_r = st.columns([1, 2, 1])
    with col_m:
        st.markdown('<div class="login-card">', unsafe_allow_html=True)
        st.markdown('<div class="login-logo">', unsafe_allow_html=True)
        try:
            # Substitua esta URL pela logo oficial branca/laranja da Brocks
            st.image("https://raw.githubusercontent.com/martinsfelipef/dash_projecoes_dre/main/.streamlit/assets/logo_brocks.jpg", width=220)
        except Exception:
            st.markdown('<p style="color:#F25C38; font-size: 2rem; font-weight:800; text-align:center;">BROCKS</p>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Tabs mock like reference image
        st.markdown('''
        <div class="login-tabs">
            <div class="login-tab-btn tab-active">Entrar</div>
            <div class="login-tab-btn tab-inactive">Cadastrar</div>
        </div>
        ''', unsafe_allow_html=True)

        username = st.text_input("Usuário", placeholder="felipe@alignconsultoria.com.br", key="_li_user")
        password = st.text_input("Senha", type="password", placeholder="••••••••", key="_li_pass")

        if st.button("Entrar", type="primary", use_container_width=True):
            if _check_password(username, password):
                admin_u = _admin_username()
                role = "admin" if username == admin_u else "viewer"
                st.session_state["_logged_in"] = True
                st.session_state["_username"]   = username
                st.session_state["_role"]       = role
                # Carrega simulações na sessão
                st.session_state["_sims"] = _load_sims(username)
                # Viewer: aplica config padrão do admin ao fazer login
                if role == "viewer":
                    cfg = _load_config_padrao() or _load_config_padrao_local()
                    if cfg:
                        _apply_sim_params(cfg)
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")
                
        st.markdown('<p style="color:#64748B; text-align:center; font-size:0.85rem; margin-top:2rem; cursor:pointer;">Esqueci minha senha</p>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

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
    if "rolling" not in st.session_state:
        st.session_state.rolling = {}
    if nome not in st.session_state.rolling:
        st.session_state.rolling[nome] = {
            "meses_reais":  {},
            "cron_orc":     {},
            "vgv":          {m+1:{"unidades":0,"preco":350000.0} for m in range(12)},
            "poc_acum":     [8,17,26,35,44,53,62,71,80,89,95,100],
            "bdi_rate":     14.0,
            "pct_entrada":  7.0,
            "parcela_un":   1500.0,
            "mes_entrega":  12,
            "g_custos":     10.0,
            "horizonte":    24,
            "data_inicio":  {"ano":2026,"mes":1},
        }
    return st.session_state.rolling[nome]

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
    # Logo Align
    try:
        st.logo("https://alignconsultoria.com.br/wp-content/uploads/2024/01/logo-align-branca.png",
                link="https://alignconsultoria.com.br")
    except:
        st.markdown("**🏢 Align Gestão de Negócios**")

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

    st.markdown("**📁 Cliente**")
    cliente_sel = st.selectbox("Cliente", list(st.session_state.clientes.keys()),
                               label_visibility="collapsed")
    st.markdown(f"## {cliente_sel}")
    st.caption("Dashboard Financeiro")
    st.divider()

    empresas_cliente = st.session_state.clientes[cliente_sel]["empresas"]
    opcoes = ["Consolidado"] + list(empresas_cliente.keys())
    st.markdown("**🏢 Empresa**")
    empresa_sel = st.selectbox("Empresa", opcoes, label_visibility="collapsed")
    for k,emp in empresas_cliente.items():
        st.caption(f"{'🟢' if emp.get('fonte','Fixo')!='Fixo' else '⚪'} {k}")

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

    rec_override_map = {}
    if "POC" in visao:
        st.divider(); st.markdown("**⚙️ Parâmetros POC**")
        vgv=st.number_input("VGV Total (R$)",value=5_000_000.0,step=100_000.0,format="%.0f",key="vgv_poc")
        poc,defs=[],[3,6,10,16,22,30,40,52,62,74,86,100]
        c1,c2=st.columns(2)
        for i,m in enumerate(MESES):
            with(c1 if i%2==0 else c2):
                poc.append(st.number_input(m,0,100,defs[i],key=f"poc{i}"))
        delta_poc=np.diff(np.concatenate([[0],np.array(poc)/100]))
        for k in empresas_cliente: rec_override_map[k]=(vgv*delta_poc).tolist()
    elif "Competência" in visao:
        st.divider(); st.markdown("**⚙️ VGV Vendas por Mês**")
        vgv_comp,defs=[],[0,0,0,0,180000,250000,320000,400000,250000,280000,220000,350000]
        c1,c2=st.columns(2)
        for i,m in enumerate(MESES):
            with(c1 if i%2==0 else c2):
                vgv_comp.append(st.number_input(m,value=float(defs[i]),step=10000.0,key=f"comp{i}",format="%.0f"))
        for k in empresas_cliente:
            if "SPE" in k or "Tereza" in k: rec_override_map[k]=vgv_comp

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
        tipo_up=st.selectbox("Tipo",["SIENGE","Template Align"],
                             label_visibility="collapsed")
        uploaded=st.file_uploader("",type=["xlsx","xls"],
                                  label_visibility="collapsed",
                                  key="uploader_dre")
        if uploaded:
            bdata=uploaded.read()
            res=parse_sienge(bdata) if tipo_up=="SIENGE" \
                else parse_template_align(bdata)
            if "erro" in res:
                safe_toast(res["erro"],"❌")
            else:
                nome_in=st.text_input("Nome no sistema:",
                                      value=res["nome"][:40])
                dest_op=list(st.session_state.clientes.keys())+\
                        ["+ Novo cliente"]
                dest=st.selectbox("Adicionar ao cliente:",dest_op)
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
    with st.expander("⚙️ Gerenciar empresas"):
        for k in list(empresas_cliente.keys()):
            cn2,cd=st.columns([3,1]); cn2.write(k)
            if cd.button("🗑️",key=f"del_{k}"):
                del st.session_state.clientes[cliente_sel]["empresas"][k]
                save_state(); safe_toast(f"{k} removida","🗑️")
                st.rerun()
        st.divider()
        novo=st.text_input("Novo cliente:",key="novo_cli",placeholder="Ex: Loja ABC")
        if st.button("➕ Criar",use_container_width=True):
            if novo.strip():
                st.session_state.clientes[novo.strip()]={"empresas":{}}
                save_state(); safe_toast(f"Cliente {novo.strip()} criado!","✅")
                st.rerun()

    st.divider()
    st.markdown("**🛡️ Zona de Perigo**")
    if st.button("🗑️ Redefinir App Completo", use_container_width=True, type="primary",
                 help="Exclui todos os dados carregados localmente, simulações ativas e recarrega o estado inicial padrão de demonstração."):
        
        # Limpa session state
        for key in list(st.session_state.keys()):
            del st.session_state[key]
            
        # Tenta excluir o arquivo json físico
        import os
        loc_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dados_dre.json")
        try:
            if os.path.exists(loc_file):
                os.remove(loc_file)
        except Exception as e:
            pass
            
        st.toast("✅ App foi redefinido completamente.")
        st.rerun()
    st.divider()

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
    st.caption("Align Gestão de Negócios © 2026")

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
TABS=["📊 DRE Analítica","📅 Rolling Forecast","🎯 Sensibilidade","📐 Indicadores","💰 FCFF & DCF"]
if "tab_ativo" not in st.session_state or st.session_state.tab_ativo not in TABS:
    st.session_state.tab_ativo=TABS[0]

tc1,tc2,tc3,tc4,tc5=st.columns(5)
for col,nome in zip([tc1,tc2,tc3,tc4,tc5],TABS):
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
    k1,k2,k3,k4,k5,k6=st.columns(6)
    kpi_popover(k1,"Receita Bruta",fmt(rb_t),
                help_text="Total faturado antes de impostos e deduções.")
    kpi_popover(k2,"Receita Líquida",fmt(rl_t),
                help_text="Receita Bruta − Impostos sobre receita (PIS, COFINS, ISS, etc.)")
    kpi_popover(k3,"Lucro Bruto",fmt(lb_t),f"{mg_b:+.1f}% Mg Bruta",
                help_text="Receita Líquida − CPV/CSP. Mede eficiência produtiva.")
    kpi_popover(k4,"EBITDA",fmt(ebt_t),f"{mg_e:+.1f}% Mg EBITDA",
                help_text="Lucro antes de juros, IR e depreciação. Proxy do caixa operacional.")
    kpi_popover(k5,"Lucro Líquido",fmt(ll_t),f"{mg_l:+.1f}% Mg Líquida",
                help_text="Resultado final após todas as deduções, incluindo IR/CSLL.")
    kpi_popover(k6,"CPV / CSP",fmt(cpv_t),
                help_text="Custo dos produtos/serviços vendidos — custo direto da operação.")
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
        f3.add_bar(x=MESES,y=final["cpv"],name="CPV",marker_color=SOFT_RED,opacity=0.85)
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
def render_rolling():
    MESES_NOME_MAP = {i+1:m for i,m in enumerate(MESES)}
    st.markdown(f"## 📅 Rolling Forecast — {titulo}")
    st.caption("Custos reais via upload SIENGE mensal. Receita projetada por 3 métodos: Competência, Caixa e POC.")
    st.divider()

    estado = get_rolling_state(titulo)
    _tkey = re.sub(r"\W+","_",titulo)
    _hc1,_hc2 = st.columns([1,4])
    N = _hc1.selectbox("⏱️ Horizonte",[12,24,36,48],
        index=[12,24,36,48].index(estado["horizonte"]),key=f"hz_{_tkey}")
    _di = estado["data_inicio"]
    _ao = list(range(2024,2031)); _mo = list(range(1,13))
    _dc1,_dc2 = _hc2.columns(2)
    _di_ano = _dc1.selectbox("Ano início",_ao,
        index=_ao.index(_di["ano"]),key=f"di_ano_{_tkey}")
    _di_mes = _dc2.selectbox("Mês início",_mo,
        index=_mo.index(_di["mes"]),  # FIX: sem -1
        format_func=lambda x:MESES[x-1],key=f"di_mes_{_tkey}")
    estado["horizonte"]=N
    estado["data_inicio"]={"ano":_di_ano,"mes":_di_mes}
    LABELS=gen_labels(N,estado["data_inicio"])
    if len(estado["vgv"])!=N:
        estado["vgv"]={m+1:estado["vgv"].get(m+1,{"unidades":0,"preco":350000.0}) for m in range(N)}
    if len(estado["poc_acum"])!=N:
        _op=estado["poc_acum"]; estado["poc_acum"]=(_op+[100]*(N-len(_op)))[:N]
    is_matriz = "matriz" in titulo.lower()
    # defaults para Matriz (não executa VGV/POC direto)
    poc_vals = list(estado["poc_acum"])
    vgv_list = [estado["vgv"].get(m+1,{"unidades":0,"preco":350000.0})
                for m in range(N)]

    # ── SEÇÃO 1: CONFIGURAÇÕES ────────────────────────────────────────────
    with st.expander("⚙️ Configurações da Obra / SPE", expanded=False):
        cf1,cf2,cf3,cf4,cf5 = st.columns(5)
        bdi_rate   = cf1.number_input("BDI Matriz (%)",   value=estado["bdi_rate"],  step=0.5, format="%.1f",
                                       help="% cobrado sobre CPV da obra — receita da Matriz")
        pct_ent    = cf2.number_input("Entrada (%)",       value=estado["pct_entrada"],step=0.5, format="%.1f",
                                       help="% do VGV pago na assinatura/venda")
        parc_un    = cf3.number_input("Parcela/Un (R$)",   value=estado["parcela_un"], step=100.0,format="%.0f",
                                       help="Valor da parcela mensal por unidade durante a obra")
        mes_ent    = int(cf4.number_input("Mês de Entrega",value=float(estado["mes_entrega"]),
                                           min_value=1.,max_value=float(N),step=1.,format="%.0f",
                                           help="Índice do mês de entrega no horizonte (1-N)"))
        g_cust     = cf5.number_input("Δ Custos (%)",      value=estado["g_custos"],  step=1.0, format="%.1f",
                                       help="Crescimento anual dos custos sobre a base 2025")
        estado.update({"bdi_rate":bdi_rate,"pct_entrada":pct_ent,"parcela_un":parc_un,
                       "mes_entrega":mes_ent,"g_custos":g_cust})

    # ── SEÇÃO 2: UPLOAD MENSAL SIENGE ────────────────────────────────────
    st.markdown("### 📎 Dados Reais — Upload Mensal SIENGE")
    # Status dos meses
    badges = ""
    for m in range(12):
        tem = (m+1) in estado["meses_reais"]
        cor = "#16a34a" if tem else "#94a3b8"
        badges += f'<span style="background:{cor};color:#fff;padding:2px 8px;border-radius:12px;margin:2px;font-size:12px">{MESES[m]}</span>'
    st.markdown(badges, unsafe_allow_html=True)

    up_col1, up_col2 = st.columns([1,2])
    with up_col1:
        mes_up = st.selectbox("Mês do arquivo:", options=list(range(1,13)),
                              format_func=lambda x: MESES[x-1], key="mes_up_sel")
    with up_col2:
        arq_up = st.file_uploader("Selecione o arquivo SIENGE deste mês",
                                   type=["xlsx","xls"], key=f"up_mensal_{mes_up}",
                                   label_visibility="collapsed")
        if arq_up:
            _raw = parse_cronograma_sienge(arq_up.read())
            if "erro" in _raw:
                st.error(_raw["erro"])
            else:
                res_up = {
                    "ok":      True,
                    "cpv":    -abs(_raw["cpv_real"]),
                    "desp_op":-abs(_raw["dop_real"]),
                    "res_fin":-abs(_raw["rf_real"]),
                    "ir":     -abs(_raw["ir_real"]),
                    "imp_rec": 0.0,
                }
                estado["meses_reais"][mes_up] = res_up
                safe_toast(f"{MESES[mes_up-1]} carregado — CPV {fmt(abs(res_up['cpv']))}", "✅")
                st.rerun()

    if estado["meses_reais"]:
        with st.expander(f"📋 Ver custos reais carregados ({len(estado['meses_reais'])} meses)"):
            rows_r = []
            for mes_k in sorted(estado["meses_reais"]):
                rd = estado["meses_reais"][mes_k]
                rows_r.append({"Mês":MESES[mes_k-1],
                               "CPV":rd.get("cpv",0),"Desp.Op.":rd.get("desp_op",0),
                               "Res.Fin.":rd.get("res_fin",0),"IR":rd.get("ir",0)})
            df_r = pd.DataFrame(rows_r).set_index("Mês")
            st.dataframe(df_r.style.format("R$ {:,.0f}"), use_container_width=True)

    st.divider()
    # ── SEÇÃO 3: VGV + POC ───────────────────────────────────────────────
    if not is_matriz:  # LOG-03
        sv1, sv2 = st.columns([3,2])
        with sv1:
            st.markdown("### 🏠 Projeção de Vendas — VGV")
            st.caption(f"⚙️ Configurado para: **{titulo}**")
            _titulo_safe = re.sub(r"\W+","_",titulo)
            def _save_vgv():
                _ed=st.session_state.get(f"vgv_ed_{_titulo_safe}")
                if _ed is not None:
                    for _m in range(12):
                        estado["vgv"][_m+1]["unidades"]=float(_ed.iloc[_m]["Unidades"])
                        estado["vgv"][_m+1]["preco"]   =float(_ed.iloc[_m]["Preço/Un"])
            vgv_df_in = pd.DataFrame({
                "Mês":     LABELS,
                "Unidades":[int(estado["vgv"][m+1]["unidades"]) for m in range(N)],
                "Preço/Un":[float(estado["vgv"][m+1]["preco"])  for m in range(N)],
            })
            try:
                vgv_ed = st.data_editor(
                    vgv_df_in,
                    column_config={
                        "Mês":      st.column_config.TextColumn("Mês",  disabled=True),
                        "Unidades": st.column_config.NumberColumn("Unidades",min_value=0,step=1),
                        "Preço/Un": st.column_config.NumberColumn("Preço/Un (R$)",min_value=0,format="R$ %.0f"),
                    },
                    hide_index=True, use_container_width=True, height=460,
                key=f"vgv_ed_{_titulo_safe}", on_change=_save_vgv
                )
                for m in range(12):
                    estado["vgv"][m+1]["unidades"] = float(vgv_ed.iloc[m]["Unidades"])
                    estado["vgv"][m+1]["preco"]    = float(vgv_ed.iloc[m]["Preço/Un"])
            except Exception:
                st.dataframe(vgv_df_in, use_container_width=True)
            vgv_total = sum(estado["vgv"][m+1]["unidades"]*estado["vgv"][m+1]["preco"] for m in range(12))
            st.metric("VGV Total Projetado", fmt(vgv_total))

        with sv2:
            st.markdown("### 📈 Avanço Físico — POC (% acumulado)")
            st.caption("% de obra concluída ao fim de cada mês (0–100).")
            poc_vals=[]
            for _gs in range(0,N,3):
                _grp=list(range(_gs,min(_gs+3,N)))
                _pc=st.columns(len(_grp))
                for _ci,i in enumerate(_grp):
                    with _pc[_ci]:
                        poc_vals.append(st.number_input(
                            LABELS[i],0,100,estado["poc_acum"][i],
                            step=1,key=f"poc_{i}_{_titulo_safe}"))
            estado["poc_acum"]=poc_vals

            # Mini Curva S
            poc_arr = np.array(poc_vals)
            fg_poc = go.Figure()
            fg_poc.add_scatter(x=MESES, y=poc_arr, mode="lines+markers",
                               line=dict(color=CHART_BLUE,width=2),
                               fill="tozeroy", fillcolor="rgba(37,99,235,0.12)",
                               marker=dict(size=5))
            fg_poc.update_layout(
        title="Curva S", height=200, plot_bgcolor=WHITE, paper_bgcolor=WHITE,
        font=dict(family="Inter,sans-serif",color=TEXT,size=11),
        template="plotly_white", showlegend=False, margin=dict(l=0,r=0,t=35,b=20))
            fg_poc.update_yaxes(ticksuffix="%", gridcolor=BORDER, range=[0,110])
            fg_poc.update_xaxes(showgrid=False, tickfont=dict(size=9))
            st.plotly_chart(fg_poc, use_container_width=True)


    else:  # LOG-03 Matriz
        st.markdown("### 🏦 Receita BDI — Matriz")
        st.info("A **Matriz** não executa obra. Receita = BDI sobre CPV das SPEs.", icon="ℹ️")
        if "rolling" in st.session_state:
            _spes={k:v for k,v in st.session_state.rolling.items() if k!=titulo}
            if _spes:
                _rows=[{"SPE":k,"BDI (%)":f'{estado["bdi_rate"]:.1f}%'} for k,v in _spes.items()]
                st.dataframe(pd.DataFrame(_rows),use_container_width=True,hide_index=True)
            else: st.caption("Nenhuma SPE carregada ainda.")

    st.divider()
    # ── SEÇÃO 4: DRE ROLLING — 3 MÉTODOS ────────────────────────────────
    st.markdown("### 💰 DRE Rolling Forecast — 3 Métodos de Receita")
    vgv_list = [estado["vgv"].get(m+1,{"unidades":0,"preco":350000.0}) for m in range(N)]

    rec_comp  = calc_competencia(vgv_list)
    rec_caixa = calc_caixa(vgv_list, pct_ent, parc_un, mes_ent)
    rec_poc   = calc_poc(vgv_list, poc_vals)

    bdi_rec = np.zeros(12)
    if "rolling" in st.session_state:
        spes = {k:v for k,v in st.session_state.rolling.items() if k != titulo}
        if spes:
            bdi_rec = bdi_matriz_mensal(spes)

    METODOS = [
        ("💰 Competência", rec_comp,  "Reconhece VGV no mês da venda"),
        ("🏦 Caixa",        rec_caixa, f"Entrada {pct_ent:.0f}% + R${parc_un:,.0f}/mês + saldo entrega"),
        ("📊 POC",          rec_poc,   "VGV acumulado × avanço físico incremental"),
        ("⚖️ Comparativo",  None,      ""),
    ]
    tabs_m = st.tabs([m[0] for m in METODOS])

    def _render_metodo(tab, rec_arr, nome_met, descr):
        with tab:
            if rec_arr is None: return
            res = build_dre_rolling(emp_base, estado["meses_reais"],
                                    rec_arr, estado["cron_orc"], g_cust)
            dr = res["dre"]; ir = res["is_real"]

            rb_t  = float(dr["rec_bruta"].sum()); ebt_t = float(dr["ebitda"].sum())
            ll_t  = float(dr["lucro_liq"].sum())
            mg_e  = ebt_t/rb_t*100 if rb_t!=0 else 0
            mg_l  = ll_t/rb_t*100  if rb_t!=0 else 0
            n_real= int(ir.sum())

            st.caption(f"**{nome_met}** — {descr} | 🟦 {n_real} mês(es) real(is) | ░ projetado")
            km1,km2,km3,km4 = st.columns(4)
            km1.metric("Receita Bruta", fmt(rb_t))
            km2.metric("EBITDA",        fmt(ebt_t), f"{mg_e:+.1f}%", delta_color="normal")
            km3.metric("Lucro Líquido", fmt(ll_t),  f"{mg_l:+.1f}%", delta_color="normal")
            km4.metric("BDI Matriz",    fmt(float(bdi_rec.sum())),
                       "sobre CPV das SPEs", delta_color="off")

            # Cores: real=sólido | proj=transparente
            def bar_colors(base_hex, is_real_mask, alpha_proj=0.35):
                import re as _re
                r,g,b = tuple(int(base_hex.lstrip("#")[i:i+2],16) for i in (0,2,4))
                return [base_hex if r_flag else
                        f"rgba({r},{g},{b},{alpha_proj})"
                        for r_flag in is_real_mask]

            fg = go.Figure()
            fg.add_bar(x=MESES, y=dr["rec_bruta"], name="Receita Bruta",
                       marker_color=bar_colors(CHART_BLUE,    ir))
            fg.add_bar(x=MESES, y=dr["cpv"],       name="CPV",
                       marker_color=bar_colors(SOFT_RED,      ir))
            fg.add_bar(x=MESES, y=dr["desp_op"],   name="Desp.Op.",
                       marker_color=bar_colors(CHART_NAVY,    ir))
            fg.add_scatter(x=MESES, y=dr["ebitda"], name="EBITDA",
                           mode="lines+markers",
                           line=dict(color=GOLD,width=2.5,
                                     dash="solid" if ir.all() else "dot"),
                           marker=dict(size=7,
                                       color=[GOLD if r else f"rgba(234,179,8,0.4)" for r in ir]))
            if n_real > 0 and n_real < 12:
                fg.add_vline(x=n_real-0.5, line_dash="dash", line_color=GRAY,
                             line_width=1.5,
                             annotation_text="Real | Proj",
                             annotation_font_size=10)
            fg.update_layout(title=f"DRE Mensal — {nome_met}",
                             barmode="relative", **PL(360))
            fg.update_xaxes(showgrid=False)
            fg.update_yaxes(gridcolor=BORDER,tickprefix="R$ ",tickformat=",.0f")
            st.plotly_chart(fg, use_container_width=True)

            # Tabela DRE
            LINHAS = [("Receita Bruta","rec_bruta"),("Impostos","imp_rec"),
                      ("Receita Líquida","rec_liq"),("CPV","cpv"),
                      ("Lucro Bruto","lucro_bruto"),("Desp. Op.","desp_op"),
                      ("EBITDA","ebitda"),("Res. Financeiro","res_fin"),
                      ("IR / CSLL","ir"),("Lucro Líquido","lucro_liq")]
            TOTS = {"Receita Líquida","Lucro Bruto","EBITDA","Lucro Líquido"}
            # Cabeçalhos: mês real = negrito, proj = normal
            col_lbl = []
            for m_i in range(12):
                col_lbl.append(f"✅{MESES[m_i]}" if ir[m_i] else MESES[m_i])
            rows_t = []
            for lbl,key in LINHAS:
                row={"Linha DRE":lbl}
                for m_i,cl in enumerate(col_lbl):
                    row[cl]=float(dr[key][m_i])
                row["TOTAL"]=float(dr[key].sum())
                rows_t.append(row)
            df_t = pd.DataFrame(rows_t).set_index("Linha DRE")

            def hl_t(r):
                return ([f"background-color:{BLIGHT};font-weight:700;color:{NAVY}"]*len(r)
                        if r.name in TOTS else [""]*len(r))
            def cn_t(v):
                try: return f"color:{SOFT_RED}" if float(v)<0 else ""
                except: return ""
            fmt_t = {c:"R$ {:,.0f}" for c in df_t.columns}
            try:    st_df = df_t.style.format(fmt_t).apply(hl_t,axis=1).map(cn_t)
            except: st_df = df_t.style.format(fmt_t).apply(hl_t,axis=1).applymap(cn_t)
            st.dataframe(st_df, use_container_width=True, height=380)

            st.download_button(f"📥 Exportar {nome_met} em Excel",
                               data=excel_dre(df_t, f"RF_{nome_met}"),
                               file_name=f"RF_{nome_met}_{titulo.replace(' ','_')}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key=f"dl_rf_{nome_met}")

    for (nome_m,rec_m,desc_m),tab_m in zip(METODOS[:3], tabs_m[:3]):
        _render_metodo(tab_m, rec_m, nome_m, desc_m)

    # ── Tab Comparativo ──────────────────────────────────────────────────
    with tabs_m[3]:
        st.markdown("#### ⚖️ Comparativo dos 3 Métodos — Receita e EBITDA Anuais")
        metodos_res = {}
        for nome_m, rec_m, _ in METODOS[:3]:
            if rec_m is None: continue
            r2 = build_dre_rolling(emp_base, estado["meses_reais"],
                                   rec_m, estado["cron_orc"], g_cust)["dre"]
            metodos_res[nome_m] = r2

        # KPIs comparativos
        ck = st.columns(3)
        for i,(nm,r2) in enumerate(metodos_res.items()):
            rb2 = float(r2["rec_bruta"].sum())
            eb2 = float(r2["ebitda"].sum())
            ck[i].metric(nm, fmt(rb2), f"EBITDA {fmt(eb2)}", delta_color="off")

        # Gráfico comparativo receita mensal
        fg_c = go.Figure()
        cores_c = [CHART_BLUE, CHART_TEAL, GOLD]
        dashes_c = ["solid","dash","dot"]
        for (nm,r2),cor,dash in zip(metodos_res.items(),cores_c,dashes_c):
            fg_c.add_scatter(x=MESES,y=r2["rec_bruta"],name=nm,
                             mode="lines+markers",
                             line=dict(color=cor,width=2.5,dash=dash),
                             marker=dict(size=7))
        fg_c.update_layout(title="Receita Bruta — 3 Métodos",**PL(320))
        fg_c.update_xaxes(showgrid=False)
        fg_c.update_yaxes(gridcolor=BORDER,tickprefix="R$ ",tickformat=",.0f")
        st.plotly_chart(fg_c, use_container_width=True)

        # Gráfico EBITDA comparativo
        fg_e = go.Figure()
        for (nm,r2),cor,dash in zip(metodos_res.items(),cores_c,dashes_c):
            fg_e.add_scatter(x=MESES,y=r2["ebitda"],name=nm,
                             mode="lines+markers",
                             line=dict(color=cor,width=2.5,dash=dash),
                             marker=dict(size=7))
        fg_e.add_hline(y=0,line_dash="dash",line_color=GRAY,line_width=1)
        fg_e.update_layout(title="EBITDA — 3 Métodos",**PL(320))
        fg_e.update_xaxes(showgrid=False)
        fg_e.update_yaxes(gridcolor=BORDER,tickprefix="R$ ",tickformat=",.0f")
        st.plotly_chart(fg_e, use_container_width=True)

        # Tabela-resumo anual 3 métodos
        linhas_c = ["Receita Bruta","EBITDA","Lucro Líquido"]
        keys_c   = ["rec_bruta","ebitda","lucro_liq"]
        rows_c   = []
        for lbl,key in zip(linhas_c,keys_c):
            row={"Linha":lbl}
            for nm,r2 in metodos_res.items():
                row[nm] = float(r2[key].sum())
            rows_c.append(row)
        df_c = pd.DataFrame(rows_c).set_index("Linha")
        def cn_c(v):
            try: return f"color:{SOFT_RED}" if float(v)<0 else ""
            except: return ""
        fmt_c = {c:"R$ {:,.0f}" for c in df_c.columns}
        try:    sc = df_c.style.format(fmt_c).map(cn_c)
        except: sc = df_c.style.format(fmt_c).applymap(cn_c)
        st.dataframe(sc, use_container_width=True)
        st.caption("💡 Mesmo CPV e Despesas para os 3 métodos — diferença está apenas no timing de reconhecimento da receita.")




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
    st.markdown(f"## 📐 Indicadores Financeiros — {titulo}")
    st.caption("Métricas de desempenho operacional e financeiro para 2025.")
    st.divider()
    base4=dre(emp_base)
    rb4=float(base4["rec_bruta"].sum()); rl4=float(base4["rec_liq"].sum())
    lb4=float(base4["lucro_bruto"].sum()); ebt4=float(base4["ebitda"].sum())
    ll4=float(base4["lucro_liq"].sum());  cpv4=float(base4["cpv"].sum()); dop4=float(base4["desp_op"].sum())
    mg_b4=(lb4/rl4*100) if rl4!=0 else 0; mg_e4=(ebt4/rl4*100) if rl4!=0 else 0
    mg_l4=(ll4/rl4*100) if rl4!=0 else 0; cpv_p=(abs(cpv4)/rl4*100) if rl4!=0 else 0
    dop_p=(abs(dop4)/rl4*100) if rl4!=0 else 0; mc_p=(lb4/rl4*100) if rl4!=0 else 0
    pe4=(-dop4/(mc_p/100)) if mc_p!=0 else 0; gao4=(lb4/ebt4) if ebt4!=0 else 0

    st.markdown("### 💹 Margens")
    i1,i2,i3,i4,i5=st.columns(5)
    kpi_popover(i1,"Margem Bruta",f"{mg_b4:.1f}%",help_text="Lucro Bruto / Receita Líquida")
    kpi_popover(i2,"Margem EBITDA",f"{mg_e4:.1f}%",help_text="EBITDA / Receita Bruta")
    kpi_popover(i3,"Margem Líquida",f"{mg_l4:.1f}%",help_text="Lucro Líquido / Receita Bruta")
    kpi_popover(i4,"CPV / Rec. Líq.",f"{cpv_p:.1f}%",help_text="Peso do custo direto sobre a receita líquida")
    kpi_popover(i5,"Desp. Op. / Rec.",f"{dop_p:.1f}%",help_text="Peso das despesas operacionais sobre a receita líquida")

    st.divider(); st.markdown("### ⚖️ Alavancagem e Equilíbrio")
    j1,j2,j3=st.columns(3)
    kpi_popover(j1,"Ponto de Equilíbrio",fmt(pe4),
                help_text="Receita mínima para cobrir todas as despesas fixas operacionais (PE Contábil = Desp. Fixas / Margem de Contribuição %)")
    kpi_popover(j2,"GAO",f"{gao4:.2f}x",
                help_text="GAO = Lucro Bruto / EBITDA. Quanto maior o GAO, mais o EBITDA é amplificado por variações na receita.")
    kpi_popover(j3,"Total Desp. Fixas",fmt(dop4),
                help_text="Despesas operacionais totais — estrutura de custos fixos do período.")

    st.divider(); st.markdown("### 📊 Composição de Custos")
    fc1,fc2=st.columns(2)
    with fc1:
        fp_pie=go.Figure(go.Pie(labels=["CPV / CSP","Despesas Op.","EBITDA"],
                                values=[abs(cpv4),abs(dop4),max(ebt4,0)],hole=0.45,
                                marker=dict(colors=[CHART_BLUE,CHART_NAVY,CHART_TEAL]),
                                textinfo="label+percent",textfont=dict(size=11)))
        fp_pie.update_layout(title="Estrutura sobre Receita Líquida",**PL(320))
        st.plotly_chart(fp_pie,use_container_width=True)
    with fc2:
        fp_stk=go.Figure()
        fp_stk.add_bar(x=MESES,y=np.abs(base4["cpv"]),  name="CPV",      marker_color=CHART_BLUE)
        fp_stk.add_bar(x=MESES,y=np.abs(base4["desp_op"]),name="Desp.Op.",marker_color=CHART_NAVY)
        fp_stk.add_scatter(x=MESES,y=base4["rec_liq"],name="Rec. Líquida",
                           mode="lines+markers",line=dict(color=GOLD,width=2.5),marker=dict(size=6))
        fp_stk.update_layout(title="Custos vs Receita Líquida (mensal)",barmode="stack",**PL(320))
        fp_stk.update_xaxes(showgrid=False)
        fp_stk.update_yaxes(gridcolor=BORDER,tickprefix="R$ ",tickformat=",.0f")
        st.plotly_chart(fp_stk,use_container_width=True)

    st.divider(); st.markdown("### 📋 Resumo dos Indicadores")
    ind_rows=[
        ("Receita Bruta",fmt(rb4),"Total bruto faturado no ano"),
        ("Receita Líquida",fmt(rl4),"Após deduções e impostos"),
        ("Lucro Bruto",fmt(lb4),"Receita Líquida − CPV"),
        ("Margem Bruta",f"{mg_b4:.1f}%","Lucro Bruto / Rec. Líquida"),
        ("EBITDA",fmt(ebt4),"Resultado antes juros, IR, deprec."),
        ("Margem EBITDA",f"{mg_e4:.1f}%","EBITDA / Receita Bruta"),
        ("Lucro Líquido",fmt(ll4),"Resultado final do exercício"),
        ("Margem Líquida",f"{mg_l4:.1f}%","Lucro Líquido / Receita Bruta"),
        ("CPV / Rec. Líquida",f"{cpv_p:.1f}%","Peso do custo direto"),
        ("Desp. Op. / Rec. Líq.",f"{dop_p:.1f}%","Peso das despesas operacionais"),
        ("Ponto de Equilíbrio",fmt(pe4),"Receita mínima para cobrir custos fixos"),
        ("GAO",f"{gao4:.2f}x","Sensibilidade do EBITDA à receita"),
    ]
    df_ind=pd.DataFrame(ind_rows,columns=["Indicador","Valor","Descrição"]).set_index("Indicador")
    tot_ind={"Lucro Bruto","EBITDA","Lucro Líquido","Ponto de Equilíbrio"}
    def hl_i(r): return ([f"background-color:{BLIGHT};font-weight:700;color:{NAVY}"]*len(r) if r.name in tot_ind else [""]*len(r))
    st.dataframe(df_ind.style.apply(hl_i,axis=1),use_container_width=True,height=470)
    st.download_button("📥 Exportar Indicadores em Excel",
                       data=excel_dre(df_ind,"Indicadores"),
                       file_name=f"Indicadores_{titulo.replace(' ','_')}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key="dl_ind")
    st.caption("🔄 Próximos: FCFF · Valuation DCF · Cronograma Físico-Financeiro · Deploy")





# ══════════════════════════════════════════════════════════════════════ TAB 5
@st.fragment
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


# ── Roteamento ────────────────────────────────────────────────────────────────
if   _tab == TABS[0]: render_dre()
elif _tab == TABS[1]: render_rolling()
elif _tab == TABS[2]: render_sensibilidade()
elif _tab == TABS[3]: render_indicadores()
elif _tab == TABS[4]: render_fcff_dcf()
