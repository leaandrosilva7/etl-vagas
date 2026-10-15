"""Conector Jobicy (https://github.com/Jobicy/remote-jobs-api).

Extrair -> transformar -> validar. Não acessa banco.
Termos de uso: não chamar a API com frequência (limite ~1 req/hora; vagas com
atraso de ~6h). Confirme na documentação oficial antes de agendar.
Convenção do banco: salário SEMPRE mensal na moeda da vaga.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import date, datetime
from typing import Any

import requests

log = logging.getLogger(__name__)

FONTE = "jobicy"
BASE_URL = "https://jobicy.com/api/v2/remote-jobs"
USER_AGENT = "etl-vagas/0.2 (plataforma de analise de vagas)"

NIVEL = {"junior": "junior", "entry": "junior", "entry-level": "junior", "intern": "estagio",
         "internship": "estagio", "midweight": "pleno", "mid": "pleno", "mid-level": "pleno",
         "senior": "senior", "lead": "senior"}

# jobGeo livre -> ISO-2. Fora da lista => 'ZZ' (desconhecido). DECISÃO A CONFIRMAR.
PAIS = {"usa": "US", "us": "US", "united states": "US", "uk": "GB", "united kingdom": "GB",
        "canada": "CA", "germany": "DE", "brazil": "BR", "brasil": "BR", "portugal": "PT",
        "spain": "ES", "france": "FR", "netherlands": "NL", "ireland": "IE",
        "australia": "AU", "india": "IN", "mexico": "MX", "argentina": "AR"}
PAIS_DESCONHECIDO = "ZZ"

# horas/semanas para converter para mensal
_PERIODO_PARA_MES = {"yearly": 1 / 12, "year": 1 / 12, "annual": 1 / 12, "annually": 1 / 12,
                     "monthly": 1, "month": 1, "weekly": 52 / 12, "hourly": 160}


# ---------- EXTRAÇÃO ----------
def extrair(count: int = 50, geo: str | None = None, industry: str | None = None,
            tag: str | None = None, timeout: int = 30, tentativas: int = 3) -> dict[str, Any]:
    params: dict[str, Any] = {"count": min(count, 100)}
    for k, v in (("geo", geo), ("industry", industry), ("tag", tag)):
        if v:
            params[k] = v
    for n in range(1, tentativas + 1):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=timeout,
                                headers={"User-Agent": USER_AGENT})
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            payload = resp.json()
            log.info("Jobicy: %s vagas recebidas (params=%s)", len(payload.get("jobs", [])), params)
            return payload
        except (requests.RequestException, ValueError) as exc:
            log.warning("Jobicy: tentativa %s/%s falhou: %s", n, tentativas, exc)
            if n == tentativas:
                raise
            time.sleep(2 ** n)
    raise RuntimeError("inalcançável")


# ---------- TRANSFORMAÇÃO ----------
def _limpar_html(texto: str | None) -> str | None:
    if not texto:
        return None
    sem_tags = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", html.unescape(sem_tags)).strip() or None


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


def _mensal(valor: float | None, periodo: str | None) -> float | None:
    if valor is None:
        return None
    fator = _PERIODO_PARA_MES.get((periodo or "yearly").strip().lower())
    if fator is None:
        log.warning("Jobicy: período salarial desconhecido %r; salário descartado", periodo)
        return None
    return round(valor * fator, 2)


def _data(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).strip().replace("Z", "")).date()
    except ValueError:
        return None


def transformar(job: dict[str, Any]) -> dict[str, Any]:
    """Devolve um dict com as chaves = colunas de `vaga` (exceto id/coleta_id/data_coleta)."""
    geo = (job.get("jobGeo") or "").strip()
    nivel = job.get("jobLevel")
    if isinstance(nivel, list):
        nivel = nivel[0] if nivel else None
    periodo = job.get("salaryPeriod")
    moeda = (job.get("salaryCurrency") or "").strip().upper()
    smin = _mensal(_num(job.get("annualSalaryMin")), periodo)
    smax = _mensal(_num(job.get("annualSalaryMax")), periodo)
    return {
        "fonte": FONTE,
        "id_externo": str(job.get("id") or "").strip(),
        "titulo": html.unescape((job.get("jobTitle") or "").strip()),
        "empresa": html.unescape((job.get("companyName") or "").strip()),
        "cidade": None,
        "estado": None,
        "localizacao": geo or None,
        "modalidade": "remoto",  # a Jobicy só lista vagas remotas
        "senioridade": NIVEL.get((nivel or "").strip().lower(), "nao_informado"),
        "salario_min": smin,
        "salario_max": smax,
        "descricao": _limpar_html(job.get("jobDescription")),
        "url_original": (job.get("url") or "").strip(),
        "data_publicacao": _data(job.get("pubDate")),
        "pais": PAIS.get(geo.lower(), PAIS_DESCONHECIDO),
        "moeda": moeda if len(moeda) == 3 and (smin or smax) else None,
        "salario_estimado": False,
    }


# ---------- VALIDAÇÃO ----------
def validar(vaga: dict[str, Any]) -> list[str]:
    erros = [f"campo obrigatório ausente: {c}"
             for c in ("id_externo", "titulo", "empresa", "url_original") if not vaga.get(c)]
    smin, smax = vaga.get("salario_min"), vaga.get("salario_max")
    if smin is not None and smax is not None and smin > smax:
        erros.append("salario_min maior que salario_max")
    if (smin is not None or smax is not None) and not vaga.get("moeda"):
        # moeda é opcional no schema; só avisamos
        log.debug("vaga %s com salário sem moeda", vaga.get("id_externo"))
    return erros


# ---------- ORQUESTRAÇÃO (sem banco) ----------
def processar(payload: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Retorna (validas, rejeitadas). Cada item guarda o JSON original em `_bruto`.
    Deduplica por (fonte, id_externo) dentro do lote."""
    validas: dict[str, dict] = {}
    rejeitadas: list[dict] = []
    duplicadas = 0
    for bruto in payload.get("jobs", []):
        try:
            vaga = transformar(bruto)
            erros = validar(vaga)
            if erros:
                rejeitadas.append({"fonte": FONTE, "id_externo": vaga.get("id_externo") or None,
                                   "motivo": "; ".join(erros), "payload": bruto})
                continue
            if vaga["id_externo"] in validas:
                duplicadas += 1
                continue
            vaga["_bruto"] = bruto
            validas[vaga["id_externo"]] = vaga
        except Exception as exc:
            log.exception("Jobicy: erro ao processar registro")
            rejeitadas.append({"fonte": FONTE, "id_externo": str(bruto.get("id") or "") or None,
                               "motivo": f"exceção: {exc}", "payload": bruto})
    log.info("Jobicy: %s válidas, %s rejeitadas, %s duplicadas no lote",
             len(validas), len(rejeitadas), duplicadas)
    return list(validas.values()), rejeitadas
