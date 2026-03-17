import numpy as np

def calc_sazonalidade(base: dict) -> dict:
    sazon = {}
    for k in ["rec_bruta","imp_rec","cpv","desp_op","res_fin","ir"]:
        a = np.array(base.get(k, [0]*12), dtype=float)
        t = a.sum()
        sazon[k] = a/t if t != 0 else np.ones(12)/12
    return sazon

def calc_competencia(vgv: list) -> np.ndarray:
    return np.array([v["unidades"]*v["preco"] for v in vgv], dtype=float)

def calc_caixa(vgv: list, pct_entrada: float, parcela_un: float, mes_entrega: int) -> np.ndarray:
    N = len(vgv)
    rec = np.zeros(N)
    me  = min(int(mes_entrega)-1, N-1)
    for m in range(N):
        n, pr = vgv[m]["unidades"], vgv[m]["preco"]
        if n<=0 or pr<=0: continue
        vgv_m   = n*pr
        entrada = vgv_m * pct_entrada/100
        rec[m] += entrada
        for p in range(m+1, me):
            rec[p] += parcela_un * n
        if me < N:
            n_parc = max(me-(m+1), 0)
            saldo  = max(vgv_m - entrada - parcela_un*n*n_parc, 0)
            rec[me]+= saldo
    return rec

def calc_poc(vgv: list, poc_acum: list) -> np.ndarray:
    N = len(vgv)
    # Garante que poc_acum tenha o mesmo tamanho que vgv
    _poc = list(poc_acum)
    if len(_poc) < N:
        _poc = _poc + [100] * (N - len(_poc))
    _poc = _poc[:N]
    delta = np.clip(np.diff(np.concatenate([[0.0],[p/100 for p in _poc]])), 0, None)
    rec   = np.zeros(N)
    acum  = 0.0
    for m in range(N):
        acum    += vgv[m]["unidades"]*vgv[m]["preco"]
        rec[m]   = acum * delta[m]
    return rec

def build_dre_rolling(base: dict, meses_reais: dict,
                       receita: np.ndarray, cron_orc: dict,
                       g_custos: float = 0.0) -> dict:
    N = len(receita)
    sazon = calc_sazonalidade(base)
    f     = 1 + g_custos/100
    b     = {k: float(np.array(base.get(k,[0]*12)).sum())
             for k in ["imp_rec","cpv","desp_op","res_fin","ir"]}
    r     = {k: np.zeros(N) for k in
             ["rec_bruta","imp_rec","cpv","desp_op","res_fin","ir"]}
    is_real = np.zeros(N, dtype=bool)

    for m in range(N):
        mes = m+1
        r["rec_bruta"][m] = receita[m]
        # Calcula índice de sazonalidade (wrap ao redor de 12 meses)
        s_idx = m % 12
        if mes in meses_reais:
            rd = meses_reais[mes]
            for k in ["imp_rec","cpv","desp_op","res_fin","ir"]:
                r[k][m] = rd.get(k, b[k]*sazon[k][s_idx]*f)
            is_real[m] = True
        else:
            r["imp_rec"][m] = b["imp_rec"]*sazon["imp_rec"][s_idx]*f
            if mes in cron_orc:
                # Aceita tanto "cpv_orc" (legado) quanto "cpv" (novo cronograma)
                _cpv_val = cron_orc[mes].get("cpv", cron_orc[mes].get("cpv_orc", 0))
                _dop_val = cron_orc[mes].get("desp_op", cron_orc[mes].get("dop_orc", 0))
                r["cpv"][m]    = -abs(_cpv_val)
                r["desp_op"][m]= -abs(_dop_val)
            else:
                r["cpv"][m]    = b["cpv"]   *sazon["cpv"][s_idx]   *f
                r["desp_op"][m]= b["desp_op"]*sazon["desp_op"][s_idx]*f
            r["res_fin"][m] = b["res_fin"]*sazon["res_fin"][s_idx]*f
            r["ir"][m]      = b["ir"]     *sazon["ir"][s_idx]     *f

    r["rec_liq"]        = r["rec_bruta"] + r["imp_rec"]
    r["lucro_bruto"]    = r["rec_liq"]   + r["cpv"]
    r["ebitda"]         = r["lucro_bruto"]+ r["desp_op"]
    r["lucro_antes_ir"] = r["ebitda"]    + r["res_fin"]
    r["lucro_liq"]      = r["lucro_antes_ir"] + r["ir"]
    return {"dre": r, "is_real": is_real}

def bdi_matriz_mensal(spes_state: dict, N: int = 12) -> np.ndarray:
    total = np.zeros(N)
    for d in spes_state.values():
        rate = d.get("bdi_rate", 14.0)/100
        cpv  = np.zeros(N)
        for m in range(N):
            mes = m+1
            if mes in d.get("meses_reais",{}):
                cpv[m] = abs(d["meses_reais"][mes].get("cpv", 0))
            elif mes in d.get("cron_orc",{}):
                _val = d["cron_orc"][mes].get("cpv", d["cron_orc"][mes].get("cpv_orc", 0))
                cpv[m] = abs(_val)
        total += cpv*rate
    return total
