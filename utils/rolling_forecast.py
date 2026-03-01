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
    rec = np.zeros(12)
    me  = min(int(mes_entrega)-1, 11)
    for m in range(12):
        n, pr = vgv[m]["unidades"], vgv[m]["preco"]
        if n<=0 or pr<=0: continue
        vgv_m   = n*pr
        entrada = vgv_m * pct_entrada/100
        rec[m] += entrada
        for p in range(m+1, me):
            rec[p] += parcela_un * n
        if me < 12:
            n_parc = max(me-(m+1), 0)
            saldo  = max(vgv_m - entrada - parcela_un*n*n_parc, 0)
            rec[me]+= saldo
    return rec

def calc_poc(vgv: list, poc_acum: list) -> np.ndarray:
    delta = np.clip(np.diff(np.concatenate([[0.0],[p/100 for p in poc_acum]])), 0, None)
    rec   = np.zeros(12)
    acum  = 0.0
    for m in range(12):
        acum    += vgv[m]["unidades"]*vgv[m]["preco"]
        rec[m]   = acum * delta[m]
    return rec

def build_dre_rolling(base: dict, meses_reais: dict,
                       receita: np.ndarray, cron_orc: dict,
                       g_custos: float = 0.0) -> dict:
    sazon = calc_sazonalidade(base)
    f     = 1 + g_custos/100
    b     = {k: float(np.array(base.get(k,[0]*12)).sum())
             for k in ["imp_rec","cpv","desp_op","res_fin","ir"]}
    r     = {k: np.zeros(12) for k in
             ["rec_bruta","imp_rec","cpv","desp_op","res_fin","ir"]}
    is_real = np.zeros(12, dtype=bool)

    for m in range(12):
        mes = m+1
        r["rec_bruta"][m] = receita[m]
        if mes in meses_reais:
            rd = meses_reais[mes]
            for k in ["imp_rec","cpv","desp_op","res_fin","ir"]:
                r[k][m] = rd.get(k, b[k]*sazon[k][m]*f)
            is_real[m] = True
        else:
            r["imp_rec"][m] = b["imp_rec"]*sazon["imp_rec"][m]*f
            if mes in cron_orc:
                r["cpv"][m]    = -abs(cron_orc[mes].get("cpv_orc", 0))
                r["desp_op"][m]= -abs(cron_orc[mes].get("dop_orc", 0))
            else:
                r["cpv"][m]    = b["cpv"]   *sazon["cpv"][m]   *f
                r["desp_op"][m]= b["desp_op"]*sazon["desp_op"][m]*f
            r["res_fin"][m] = b["res_fin"]*sazon["res_fin"][m]*f
            r["ir"][m]      = b["ir"]     *sazon["ir"][m]     *f

    r["rec_liq"]        = r["rec_bruta"] + r["imp_rec"]
    r["lucro_bruto"]    = r["rec_liq"]   + r["cpv"]
    r["ebitda"]         = r["lucro_bruto"]+ r["desp_op"]
    r["lucro_antes_ir"] = r["ebitda"]    + r["res_fin"]
    r["lucro_liq"]      = r["lucro_antes_ir"] + r["ir"]
    return {"dre": r, "is_real": is_real}

def bdi_matriz_mensal(spes_state: dict) -> np.ndarray:
    total = np.zeros(12)
    for d in spes_state.values():
        rate = d.get("bdi_rate", 14.0)/100
        cpv  = np.zeros(12)
        for m in range(12):
            mes = m+1
            if mes in d.get("meses_reais",{}):
                cpv[m] = abs(d["meses_reais"][mes].get("cpv", 0))
            elif mes in d.get("cron_orc",{}):
                cpv[m] = abs(d["cron_orc"][mes].get("cpv_orc", 0))
        total += cpv*rate
    return total
