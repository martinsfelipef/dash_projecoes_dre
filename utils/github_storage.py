"""
Persistência via GitHub API.

Lê/escreve `dados.json` num repositório privado separado.
Só actua se `st.secrets["github"]` estiver configurado;
em ambiente local sem secrets.toml é silenciosamente ignorado.
"""
import json
import numpy as np
import pandas as pd


class _Encoder(json.JSONEncoder):
    """Serializa tipos numpy/pandas para JSON."""
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return {"__ndarray__": True, "data": obj.tolist()}
        if isinstance(obj, pd.DataFrame):
            return {
                "__dataframe__": True,
                "records": obj.to_dict("records"),
                "columns": obj.columns.tolist(),
            }
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        return super().default(obj)


def _hook(obj):
    """Reconstrói numpy arrays e DataFrames a partir do JSON."""
    if "__ndarray__" in obj:
        return np.array(obj["data"])
    if "__dataframe__" in obj:
        return pd.DataFrame(obj["records"], columns=obj["columns"])
    return obj


def _get_repo():
    import streamlit as st
    from github import Github
    token = st.secrets["github"]["token"]
    repo_name = st.secrets["github"]["repo"]
    return Github(token).get_repo(repo_name)


def load_state_github():
    """
    Lê `dados.json` do repo privado configurado em secrets.

    Retorna o dict deserializado, ou None se não configurado / arquivo vazio.
    """
    try:
        import streamlit as st
        if "github" not in st.secrets:
            return None
        repo = _get_repo()
        try:
            file = repo.get_contents("dados.json")
            raw = file.decoded_content.decode("utf-8").strip()
            if not raw or raw == "{}":
                return None
            return json.loads(raw, object_hook=_hook)
        except Exception:
            return None
    except Exception:
        return None


def save_state_github(clientes):
    """
    Serializa `clientes` e commita `dados.json` no repo privado.

    Silenciosamente ignorado se secrets não estiver configurado.
    Exibe st.warning em caso de erro de API.
    """
    try:
        import streamlit as st
        if "github" not in st.secrets:
            return
        repo = _get_repo()
        content = json.dumps(
            dict(clientes), ensure_ascii=False, indent=2, cls=_Encoder
        )
        try:
            file = repo.get_contents("dados.json")
            repo.update_file(
                "dados.json",
                "dashboard: atualiza estado",
                content,
                file.sha,
            )
        except Exception:
            repo.create_file(
                "dados.json",
                "dashboard: estado inicial",
                content,
            )
    except Exception as e:
        import streamlit as st
        st.warning(f"Aviso: não foi possível salvar no GitHub: {e}")
