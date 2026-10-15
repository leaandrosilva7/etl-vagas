"""Carga idempotente no Postgres (Supabase). Escreve apenas em:
coleta_vagas, raw_vagas, vaga, vaga_rejeitada, vaga_skill.
Não escreve em estatistica_salarial / tendencia_* / metrica_dashboard (derivadas)."""
from __future__ import annotations

import logging

import psycopg
from psycopg.types.json import Jsonb

from etl import skills as skills_mod

log = logging.getLogger(__name__)

COLUNAS_VAGA = ["fonte", "id_externo", "titulo", "empresa", "cidade", "estado", "localizacao",
                "modalidade", "senioridade", "salario_min", "salario_max", "descricao",
                "url_original", "data_publicacao", "pais", "moeda", "salario_estimado"]

SQL_UPSERT_VAGA = f"""
INSERT INTO vaga ({', '.join(COLUNAS_VAGA)}, coleta_id)
VALUES ({', '.join('%(' + c + ')s' for c in COLUNAS_VAGA)}, %(coleta_id)s)
ON CONFLICT (fonte, id_externo) DO UPDATE SET
  {', '.join(f'{c} = EXCLUDED.{c}' for c in COLUNAS_VAGA if c not in ('fonte', 'id_externo'))},
  coleta_id = EXCLUDED.coleta_id,
  data_coleta = CURRENT_DATE
RETURNING id, (xmax = 0) AS inserida
"""


def conectar(url: str) -> psycopg.Connection:
    # prepare_threshold=None: compatível com o pooler do Supabase (modo transaction, porta 6543)
    return psycopg.connect(url, autocommit=True, prepare_threshold=None)


def carregar(conn: psycopg.Connection, validas: list[dict], rejeitadas: list[dict],
             termo_busca: str, pais_coleta: str, fonte: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO coleta_vagas (fonte, termo_busca, pais, total_coletado, total_validas, total_rejeitadas) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (fonte, termo_busca, pais_coleta, len(validas) + len(rejeitadas),
             len(validas), len(rejeitadas)))
        coleta_id = cur.fetchone()[0]
    log.info("Coleta %s registrada", coleta_id)

    resumo = {"coleta_id": coleta_id, "inseridas": 0, "atualizadas": 0, "vinculos_skill": 0}
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT id, nome FROM skill")
            catalogo = skills_mod.compilar_catalogo(cur.fetchall())

            for v in validas:
                cur.execute(
                    "INSERT INTO raw_vagas (fonte, id_externo, payload) VALUES (%s,%s,%s) "
                    "ON CONFLICT (fonte, id_externo) DO UPDATE "
                    "SET payload = EXCLUDED.payload, coletado_em = now()",
                    (v["fonte"], v["id_externo"], Jsonb(v["_bruto"])))
                cur.execute(SQL_UPSERT_VAGA, {**{c: v[c] for c in COLUNAS_VAGA}, "coleta_id": coleta_id})
                vaga_id, inserida = cur.fetchone()
                resumo["inseridas" if inserida else "atualizadas"] += 1

                for skill_id in skills_mod.identificar(v["titulo"], v["descricao"], catalogo):
                    cur.execute("INSERT INTO vaga_skill (vaga_id, skill_id) VALUES (%s,%s) "
                                "ON CONFLICT DO NOTHING", (vaga_id, skill_id))
                    resumo["vinculos_skill"] += cur.rowcount

            for r in rejeitadas:  # idempotente: remove rejeição anterior do mesmo registro
                if r["id_externo"]:
                    cur.execute("DELETE FROM vaga_rejeitada WHERE fonte=%s AND id_externo=%s",
                                (r["fonte"], r["id_externo"]))
                cur.execute(
                    "INSERT INTO vaga_rejeitada (coleta_id, fonte, id_externo, motivo, payload) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (coleta_id, r["fonte"], r["id_externo"], r["motivo"], Jsonb(r["payload"])))
        status = "sucesso"
    except Exception:
        log.exception("Falha na carga; transação desfeita")
        status = "erro"
        raise
    finally:
        with conn.cursor() as cur:
            cur.execute("UPDATE coleta_vagas SET status=%s WHERE id=%s", (status, coleta_id))
    return resumo
