"""Utilitários compartilhados pelos conectores novos (Adzuna e próximos).
O conector da Jobicy ainda tem cópias próprias; dá para migrá-lo depois."""
from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime
from typing import Any, Callable

log = logging.getLogger(__name__)


def limpar_html(texto: str | None) -> str | None:
    if not texto:
        return None
    sem_tags = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", html.unescape(sem_tags)).strip() or None


def num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


def data_iso(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).strip().replace("Z", "")).date()
    except ValueError:
        return None


def validar(vaga: dict[str, Any]) -> list[str]:
    erros = [f"campo obrigatório ausente: {c}"
             for c in ("id_externo", "titulo", "empresa", "url_original") if not vaga.get(c)]
    smin, smax = vaga.get("salario_min"), vaga.get("salario_max")
    if smin is not None and smax is not None and smin > smax:
        erros.append("salario_min maior que salario_max")
    return erros


def processar_lote(fonte: str, brutos: list[dict], transformar: Callable[[dict], dict],
                   id_bruto: Callable[[dict], str | None]) -> tuple[list[dict], list[dict]]:
    """Retorna (validas, rejeitadas). Guarda o JSON original em `_bruto`.
    Deduplica por id_externo dentro do lote (mantém a primeira ocorrência)."""
    validas: dict[str, dict] = {}
    rejeitadas: list[dict] = []
    duplicadas = 0
    for bruto in brutos:
        try:
            vaga = transformar(bruto)
            erros = validar(vaga)
            if erros:
                rejeitadas.append({"fonte": fonte, "id_externo": vaga.get("id_externo") or None,
                                   "motivo": "; ".join(erros), "payload": bruto})
                continue
            if vaga["id_externo"] in validas:
                duplicadas += 1
                continue
            vaga["_bruto"] = bruto
            validas[vaga["id_externo"]] = vaga
        except Exception as exc:
            log.exception("%s: erro ao processar registro", fonte)
            rejeitadas.append({"fonte": fonte, "id_externo": id_bruto(bruto),
                               "motivo": f"exceção: {exc}", "payload": bruto})
    log.info("%s: %s válidas, %s rejeitadas, %s duplicadas no lote",
             fonte, len(validas), len(rejeitadas), duplicadas)
    return list(validas.values()), rejeitadas
