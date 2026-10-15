"""Executa a ETL completa: todas as fontes, depois recalcula as tabelas derivadas.

  python -m etl.run_all                      # tudo, gravando no banco
  python -m etl.run_all --dry-run            # extrai e valida; não toca no banco
  python -m etl.run_all --fontes adzuna      # só uma fonte
  python -m etl.run_all --what python --what dados --paginas 2 --max-dias 30

Configuração (via .env / secrets do Actions):
  DATABASE_URL, ADZUNA_APP_ID, ADZUNA_APP_KEY
  SEARCH_TERMS  termos da Adzuna, separados por vírgula
  MAX_PAGES     páginas por termo na Adzuna (cada página = 1 chamada da cota)
  JOBICY_TAG    (opcional) tag da Jobicy; a Jobicy é chamada 1x por execução

Cada fonte roda isolada: se uma falha, as outras continuam. O processo termina com
código 1 se qualquer etapa falhar (o GitHub Actions marca a execução como falha).
Para plugar uma nova API: escreva `rodar_<fonte>(conn, args)` e registre em FONTES.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from etl import config, derivadas, load
from etl.sources import adzuna, jobicy

log = logging.getLogger("etl.run_all")


def _salvar_dry_run(fonte: str, validas: list[dict], rejeitadas: list[dict]) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path("data/dry_run")
    out.mkdir(parents=True, exist_ok=True)

    def ser(o: Any) -> str:
        return json.dumps(o, ensure_ascii=False, indent=2, default=str)

    (out / f"{fonte}_validas_{ts}.json").write_text(
        ser([{k: v for k, v in x.items() if k != "_bruto"} for x in validas]), encoding="utf-8")
    (out / f"{fonte}_rejeitadas_{ts}.json").write_text(ser(rejeitadas), encoding="utf-8")
    log.info("%s: dry-run salvo em %s/", fonte, out)


def _finalizar(conn, fonte: str, validas: list[dict], rejeitadas: list[dict], recebidas: int,
               termo: str, pais: str, dry_run: bool) -> dict:
    resumo = {"recebidas": recebidas, "validas": len(validas), "rejeitadas": len(rejeitadas)}
    if dry_run:
        _salvar_dry_run(fonte, validas, rejeitadas)
    else:
        resumo.update(load.carregar(conn, validas, rejeitadas, termo, pais, fonte))
    return resumo


# ---------- FONTES ----------
def rodar_jobicy(conn, a: argparse.Namespace) -> dict:
    tag = a.jobicy_tag or config.jobicy_tag()
    payload = jobicy.extrair(a.jobicy_count, tag=tag)  # 1 chamada (limite ~1 req/hora)
    validas, rejeitadas = jobicy.processar(payload)
    return _finalizar(conn, jobicy.FONTE, validas, rejeitadas, len(payload.get("jobs", [])),
                      tag or "todas", "global", a.dry_run)


def rodar_adzuna(conn, a: argparse.Namespace) -> dict:
    app_id, app_key = config.adzuna_credentials()
    termos = a.what or config.search_terms()
    if not termos:
        log.warning("Adzuna: sem termos (SEARCH_TERMS/--what); buscando vagas sem filtro de termo")
        termos = [None]
    paginas = a.paginas or config.max_pages()
    log.info("Adzuna: até %s chamadas (%s termos x %s páginas)", len(termos) * paginas,
             len(termos), paginas)

    brutos: list[dict] = []
    for termo in termos:
        payload = adzuna.extrair(app_id, app_key, a.pais, termo, a.where, paginas,
                                 a.por_pagina, a.max_dias)
        brutos.extend(payload["results"])
    # a deduplicação entre termos acontece em processar() (por id_externo)
    validas, rejeitadas = adzuna.processar({"pais": a.pais.lower(), "results": brutos})
    return _finalizar(conn, adzuna.FONTE, validas, rejeitadas, len(brutos),
                      ", ".join(t for t in termos if t) or "todas", a.pais.upper(), a.dry_run)


FONTES: dict[str, Callable[[Any, argparse.Namespace], dict]] = {
    "jobicy": rodar_jobicy,
    "adzuna": rodar_adzuna,
}


# ---------- ORQUESTRAÇÃO ----------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fontes", nargs="+", choices=sorted(FONTES), default=list(FONTES))
    ap.add_argument("--dry-run", action="store_true", help="não grava no banco; salva JSONs em data/dry_run/")
    ap.add_argument("--sem-derivadas", action="store_true", help="não recalcula as tabelas derivadas")
    # Adzuna
    ap.add_argument("--pais", default="br", help="código do país Adzuna (br, us, gb...)")
    ap.add_argument("--what", action="append", help="termo da Adzuna (repetível); padrão: SEARCH_TERMS")
    ap.add_argument("--where", help="cidade/região da Adzuna")
    ap.add_argument("--paginas", type=int, help="páginas por termo; padrão: MAX_PAGES")
    ap.add_argument("--por-pagina", type=int, default=50)
    ap.add_argument("--max-dias", type=int, default=30, help="só vagas dos últimos N dias (Adzuna)")
    # Jobicy
    ap.add_argument("--jobicy-count", type=int, default=100)
    ap.add_argument("--jobicy-tag")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    conn = None
    if not a.dry_run:
        try:
            conn = load.conectar(config.database_url())
        except Exception as exc:
            log.error("Não foi possível conectar ao banco: %s", type(exc).__name__)
            return 1

    resultados: dict[str, dict] = {}
    falhas: list[str] = []
    try:
        for nome in a.fontes:
            log.info("=== %s ===", nome)
            try:
                resultados[nome] = FONTES[nome](conn, a)
            except Exception as exc:  # isola a fonte: as demais continuam
                log.exception("Fonte %s falhou", nome)
                resultados[nome] = {"erro": f"{type(exc).__name__}: {exc}"}
                falhas.append(nome)

        carregou_algo = any("erro" not in r for r in resultados.values())
        if conn is not None and not a.sem_derivadas:
            if carregou_algo:
                try:
                    resultados["derivadas"] = derivadas.recalcular(conn)
                except Exception as exc:
                    log.exception("Recalculo das derivadas falhou")
                    resultados["derivadas"] = {"erro": f"{type(exc).__name__}: {exc}"}
                    falhas.append("derivadas")
            else:
                log.warning("Nenhuma fonte carregou; derivadas não recalculadas")
    finally:
        if conn is not None:
            conn.close()

    print("\n=== RESUMO ===")
    for nome, r in resultados.items():
        print(f"{nome}: {r}")
    if falhas:
        print(f"\nFALHAS: {', '.join(falhas)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
