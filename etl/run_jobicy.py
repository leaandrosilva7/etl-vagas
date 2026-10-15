"""Executa a ETL da Jobicy.
  python -m etl.run_jobicy --dry-run --count 20      # não toca no banco
  python -m etl.run_jobicy --count 50 --tag python   # extrai e carrega
"""
import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from etl import config, load
from etl.sources import jobicy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--geo")
    ap.add_argument("--industry")
    ap.add_argument("--tag")
    ap.add_argument("--dry-run", action="store_true", help="não grava no banco; salva JSONs em data/dry_run/")
    a = ap.parse_args()

    payload = jobicy.extrair(a.count, a.geo, a.industry, a.tag)
    validas, rejeitadas = jobicy.processar(payload)
    print(f"Recebidas: {len(payload.get('jobs', []))} | válidas: {len(validas)} | rejeitadas: {len(rejeitadas)}")

    if a.dry_run:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = Path("data/dry_run")
        out.mkdir(parents=True, exist_ok=True)
        ser = lambda o: json.dumps(o, ensure_ascii=False, indent=2, default=str)
        (out / f"jobicy_validas_{ts}.json").write_text(ser([{k: v for k, v in x.items() if k != "_bruto"} for x in validas]), encoding="utf-8")
        (out / f"jobicy_rejeitadas_{ts}.json").write_text(ser(rejeitadas), encoding="utf-8")
        print(f"Dry-run: arquivos em {out}/")
        return

    termo = a.tag or a.industry or "todas"
    with load.conectar(config.database_url()) as conn:
        print(load.carregar(conn, validas, rejeitadas, termo, a.geo or "global", jobicy.FONTE))


if __name__ == "__main__":
    main()
