"""Conector Adzuna (https://developer.adzuna.com/docs/search).

Extrair -> transformar -> validar. Não acessa banco.

Pontos de atenção (termos de uso / limites da API):
- Exige app_id e app_key (cadastro gratuito). O plano gratuito tem limite de chamadas:
  confira a cota atual no painel antes de agendar execuções recorrentes.
- Os termos pedem usar a `redirect_url` como link da vaga e exibir a atribuição
  "Jobs by Adzuna" quando os dados forem mostrados ao usuário (dashboard).
- `description` vem TRUNCADA (trecho), então a identificação de skills fica limitada.
- Salário: anual, sem moeda no payload; `salary_is_predicted = "1"` quando é estimativa da Adzuna.
Convenção do banco: salário SEMPRE mensal na moeda da vaga (aqui: anual / 12).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

from etl.sources import comum

log = logging.getLogger(__name__)

FONTE = "adzuna"
BASE_URL = "https://api.adzuna.com/v1/api/jobs"
USER_AGENT = "etl-vagas/0.3 (plataforma de analise de vagas)"
POR_PAGINA_MAX = 50
PAUSA_ENTRE_PAGINAS = 1.0  # segundos; evita rajada de chamadas

# A Adzuna não informa a moeda no payload: deduzimos pelo país da consulta.
MOEDA_PAIS = {"br": "BRL", "us": "USD", "gb": "GBP", "ca": "CAD", "au": "AUD", "nz": "NZD",
              "ch": "CHF", "in": "INR", "mx": "MXN", "pl": "PLN", "sg": "SGD", "za": "ZAR",
              "de": "EUR", "fr": "EUR", "es": "EUR", "it": "EUR", "nl": "EUR", "at": "EUR",
              "be": "EUR"}

# A Adzuna não tem campo de modalidade nem de senioridade: inferência conservadora
# (só quando o texto diz explicitamente). Caso contrário, 'nao_informado'. DECISÃO A CONFIRMAR.
_HIBRIDO = re.compile(r"\b(h[ií]brid[oa]|hybrid)\b", re.IGNORECASE)
_REMOTO = re.compile(r"\b(remot[eo]|home[\s-]?office|work from home|teletrabalho)\b", re.IGNORECASE)
_NIVEIS = [
    ("estagio", re.compile(r"\b(est[aá]gi[oa]|estagi[aá]ri[oa]|intern(ship)?)\b", re.IGNORECASE)),
    ("trainee", re.compile(r"\btrainee\b", re.IGNORECASE)),
    ("junior", re.compile(r"\b(j[uú]nior|jr|entry[\s-]level)\b", re.IGNORECASE)),
    ("pleno", re.compile(r"\b(pleno|mid[\s-]?level)\b", re.IGNORECASE)),
    ("senior", re.compile(r"\b(s[eê]nior|sr|lead)\b", re.IGNORECASE)),
]


class AdzunaErro(RuntimeError):
    """Falha de extração já com segredos removidos da mensagem."""


def _redigir(msg: str, segredos: list[str]) -> str:
    # a URL (com app_key) aparece nas mensagens de erro do requests: nunca logar a chave
    for s in segredos:
        if s:
            msg = msg.replace(s, "***")
    return msg


# ---------- EXTRAÇÃO ----------
def _get(url: str, params: dict[str, Any], timeout: int, tentativas: int) -> dict[str, Any]:
    segredos = [str(params.get("app_key", "")), str(params.get("app_id", ""))]
    for n in range(1, tentativas + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": USER_AGENT})
            if resp.status_code in (400, 401, 403):  # erro nosso: tentar de novo não resolve
                raise AdzunaErro(f"HTTP {resp.status_code}: verifique app_id/app_key, "
                                 f"código do país e parâmetros da busca")
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json()
        except AdzunaErro:
            raise
        except (requests.RequestException, ValueError) as exc:
            msg = _redigir(str(exc), segredos)
            log.warning("Adzuna: tentativa %s/%s falhou: %s", n, tentativas, msg)
            if n == tentativas:
                raise AdzunaErro(f"falha após {tentativas} tentativas: {msg}") from None
            time.sleep(2 ** n)
    raise RuntimeError("inalcançável")


def extrair(app_id: str, app_key: str, pais: str = "br", what: str | None = None,
            where: str | None = None, paginas: int = 1, por_pagina: int = POR_PAGINA_MAX,
            max_dias: int | None = None, timeout: int = 30, tentativas: int = 3) -> dict[str, Any]:
    """Pagina /search/{pagina} e junta tudo em {'count', 'pais', 'results'}."""
    pais = pais.strip().lower()
    if not re.fullmatch(r"[a-z]{2}", pais):
        raise ValueError(f"código de país inválido: {pais!r} (use ISO-2, ex.: br, us, gb)")
    por_pagina = max(1, min(por_pagina, POR_PAGINA_MAX))

    resultados: list[dict] = []
    total = None
    for pagina in range(1, paginas + 1):
        params: dict[str, Any] = {"app_id": app_id, "app_key": app_key,
                                  "results_per_page": por_pagina, "sort_by": "date",
                                  "content-type": "application/json"}
        for k, v in (("what", what), ("where", where), ("max_days_old", max_dias)):
            if v:
                params[k] = v
        dados = _get(f"{BASE_URL}/{pais}/search/{pagina}", params, timeout, tentativas)
        lote = dados.get("results", [])
        total = dados.get("count", total)
        resultados.extend(lote)
        log.info("Adzuna[%s]: página %s -> %s vagas (total na busca: %s)", pais, pagina, len(lote), total)
        if len(lote) < por_pagina:  # última página
            break
        if pagina < paginas:
            time.sleep(PAUSA_ENTRE_PAGINAS)
    return {"count": total, "pais": pais, "results": resultados}


# ---------- TRANSFORMAÇÃO ----------
def _modalidade(titulo: str, descricao: str | None) -> str:
    texto = f"{titulo}\n{descricao or ''}"
    if _HIBRIDO.search(texto):
        return "hibrido"
    if _REMOTO.search(texto):
        return "remoto"
    return "nao_informado"


def _senioridade(titulo: str) -> str:
    for nivel, padrao in _NIVEIS:
        if padrao.search(titulo):
            return nivel
    return "nao_informado"


def _mensal(anual: float | None) -> float | None:
    return round(anual / 12, 2) if anual is not None else None


def transformar(job: dict[str, Any], pais: str) -> dict[str, Any]:
    """Devolve um dict com as chaves = colunas de `vaga` (exceto id/coleta_id/data_coleta)."""
    pais = pais.strip().lower()
    loc = job.get("location") or {}
    area = [a for a in (loc.get("area") or []) if a]  # ex.: ["Brasil", "São Paulo", "São Paulo"]
    titulo = comum.limpar_html(job.get("title")) or ""
    descricao = comum.limpar_html(job.get("description"))
    smin = _mensal(comum.num(job.get("salary_min")))
    smax = _mensal(comum.num(job.get("salary_max")))
    return {
        "fonte": FONTE,
        "id_externo": str(job.get("id") or "").strip(),
        "titulo": titulo,
        "empresa": comum.limpar_html((job.get("company") or {}).get("display_name")) or "",
        "cidade": area[-1] if len(area) >= 3 else None,
        "estado": area[1] if len(area) >= 2 else None,
        "localizacao": (loc.get("display_name") or "").strip() or None,
        "modalidade": _modalidade(titulo, descricao),
        "senioridade": _senioridade(titulo),
        "salario_min": smin,
        "salario_max": smax,
        "descricao": descricao,
        "url_original": (job.get("redirect_url") or "").strip(),
        "data_publicacao": comum.data_iso(job.get("created")),
        "pais": pais.upper(),
        "moeda": MOEDA_PAIS.get(pais) if (smin or smax) else None,
        "salario_estimado": str(job.get("salary_is_predicted") or "0").strip() == "1",
    }


# ---------- ORQUESTRAÇÃO (sem banco) ----------
def processar(payload: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    pais = payload.get("pais", "br")
    if pais not in MOEDA_PAIS:
        log.warning("Adzuna: país %r sem moeda mapeada; salários ficarão sem moeda", pais)
    return comum.processar_lote(
        FONTE, payload.get("results", []),
        transformar=lambda b: transformar(b, pais),
        id_bruto=lambda b: str(b.get("id") or "") or None)
