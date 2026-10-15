"""Executa a ETL da Adzuna.
  python -m etl.run_adzuna --dry-run --what python --paginas 1      # não toca no banco
  python -m etl.run_adzuna --pais br --what python --what "dados" --paginas 2 --max-dias 30
Cada página é 1 chamada à API: termos x páginas = chamadas consumidas da sua cota.
"""
import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from etl import config, load
from etl.sources import adzuna

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pais", default="br", help="código do país Adzuna (br, us, gb...)")
    ap.add_argument("--what", action="append", help="termo de busca; pode repetir a flag")
    ap.add_argument("--where", help="cidade/região (opcional)")
    ap.add_argument("--paginas", type=int, default=1)
    ap.add_argument("--por-pagina", type=int, default=50)
    ap.add_argument("--max-dias", type=int, help="só vagas publicadas nos últimos N dias")
    ap.add_argument("--dry-run", action="store_true", help="não grava no banco; salva JSONs em data/dry_run/")
    a = ap.parse_args()

    termos = a.what or [None]
    print(f"Chamadas previstas à API: {len(termos) * a.paginas} (máximo)")
    app_id, app_key = config.adzuna_credentials()

    brutos: list[dict] = []
    for termo in termos:
        payload = adzuna.extrair(app_id, app_key, a.pais, termo, a.where, a.paginas,
                                 a.por_pagina, a.max_dias)
        brutos.extend(payload["results"])
    # a deduplicação entre termos acontece em processar() (por id_externo)
    validas, rejeitadas = adzuna.processar({"pais": a.pais.lower(), "results": brutos})
    print(f"Recebidas: {len(brutos)} | válidas: {len(validas)} | rejeitadas: {len(rejeitadas)}")

    if a.dry_run:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = Path("data/dry_run")
        out.mkdir(parents=True, exist_ok=True)
        ser = lambda o: json.dumps(o, ensure_ascii=False, indent=2, default=str)
        (out / f"adzuna_validas_{ts}.json").write_text(ser([{k: v for k, v in x.items() if k != "_bruto"} for x in validas]), encoding="utf-8")
        (out / f"adzuna_rejeitadas_{ts}.json").write_text(ser(rejeitadas), encoding="utf-8")
        print(f"Dry-run: arquivos em {out}/")
        return

    termo = ", ".join(t for t in termos if t) or "todas"
    with load.conectar(config.database_url()) as conn:
        print(load.carregar(conn, validas, rejeitadas, termo, a.pais.upper(), adzuna.FONTE))


if __name__ == "__main__":
    main()
