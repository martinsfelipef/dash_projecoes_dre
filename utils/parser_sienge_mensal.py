import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from parser_cronograma_sienge import parse_cronograma_sienge

def parse_sienge_mensal(bdata: bytes) -> dict:
    res = parse_cronograma_sienge(bdata)
    if "erro" in res:
        return res
    return {
        "ok":      True,
        "cpv":    -abs(res["cpv_real"]),
        "desp_op":-abs(res["dop_real"]),
        "res_fin":-abs(res["rf_real"]),
        "ir":     -abs(res["ir_real"]),
        "imp_rec": 0.0,
    }
