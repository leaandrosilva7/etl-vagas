"""Testa o recálculo das tabelas derivadas contra um Postgres de verdade.
Defina TEST_DATABASE_URL apontando para um banco DESCARTÁVEL já com schema.sql + 002
aplicados (NUNCA o de produção: o teste apaga vaga/skill/derivadas). Sem a variável, pula."""
import os
from pathlib import Path

import pytest

from etl import derivadas, load

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida")

SQL_003 = Path(__file__).resolve().parent.parent / "sql" / "003_derivadas.sql"
TABELAS = ["vaga_skill", "vaga", "skill", "estatistica_salarial", "tendencia_mercado",
           "tendencia_skill", "metrica_dashboard"]


@pytest.fixture()
def conn():
    c = load.conectar(URL)
    c.execute(SQL_003.read_text(encoding="utf-8"))
    for t in TABELAS:
        c.execute(f"DELETE FROM {t}")
    yield c
    c.close()


def _vaga(c, n, pub, moeda, smin, smax, sen, modal, skills, estimado=False):
    vid = c.execute(
        "INSERT INTO vaga (fonte,id_externo,titulo,empresa,url_original,data_publicacao,moeda,"
        "salario_min,salario_max,senioridade,modalidade,salario_estimado) "
        "VALUES ('t',%s,'t','e','http://x',%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (str(n), pub, moeda, smin, smax, sen, modal, estimado)).fetchone()[0]
    for s in skills:
        c.execute("INSERT INTO vaga_skill (vaga_id,skill_id) VALUES (%s,%s)", (vid, s))


@pytest.fixture()
def dados(conn):
    py = conn.execute("INSERT INTO skill (nome) VALUES ('Python') RETURNING id").fetchone()[0]
    sql = conn.execute("INSERT INTO skill (nome) VALUES ('SQL') RETURNING id").fetchone()[0]
    _vaga(conn, 1, "2026-09-10", "BRL", 8000, 12000, "pleno", "remoto", [py, sql])
    _vaga(conn, 2, "2026-10-05", "BRL", 6000, None, "pleno", "remoto", [py])
    _vaga(conn, 3, "2026-10-07", "BRL", 99999, 99999, "pleno", "presencial", [py], estimado=True)
    _vaga(conn, 4, "2026-10-09", "USD", 5000, 7000, "senior", "remoto", [sql])
    _vaga(conn, 5, None, None, None, None, "nao_informado", "nao_informado", [])


def _rows(conn, sql):
    return conn.execute(sql).fetchall()


def test_calculos(conn, dados):
    derivadas.recalcular(conn)

    est = {(t, s, m): (float(med), float(mn), float(mx), n) for t, s, m, med, mn, mx, n in _rows(
        conn, "SELECT tecnologia,senioridade,moeda,salario_medio,salario_min,salario_max,total_vagas "
              "FROM estatistica_salarial")}
    assert est == {
        ("Python", "pleno", "BRL"): (8000.0, 6000.0, 12000.0, 2),   # estimado (vaga 3) fica de fora
        ("SQL", "pleno", "BRL"): (10000.0, 8000.0, 12000.0, 1),
        ("SQL", "senior", "USD"): (6000.0, 5000.0, 7000.0, 1),       # moedas nunca se misturam
    }

    tm = {p: (n, None if v is None else float(v)) for p, n, v in _rows(
        conn, "SELECT periodo,total_vagas,variacao_percentual FROM tendencia_mercado")}
    assert tm == {"2026-09": (1, None), "2026-10": (3, 200.0)}      # vaga sem data não entra

    ts = {(p, nome): n for p, nome, n in _rows(
        conn, "SELECT t.periodo,s.nome,t.total_vagas FROM tendencia_skill t JOIN skill s ON s.id=t.skill_id")}
    assert ts == {("2026-09", "Python"): 1, ("2026-09", "SQL"): 1,
                  ("2026-10", "Python"): 2, ("2026-10", "SQL"): 1}

    total, sal, rem, top, moeda = _rows(
        conn, "SELECT total_vagas,salario_medio,percentual_remoto,skill_top,moeda FROM v_metrica_dashboard_atual")[0]
    assert (total, float(sal), float(rem), top, moeda) == (5, 8000.0, 75.0, "Python", "BRL")


def test_idempotente_e_historico(conn, dados):
    derivadas.recalcular(conn)
    antes = _rows(conn, "SELECT * FROM estatistica_salarial ORDER BY 1,2,3")
    derivadas.recalcular(conn)
    assert _rows(conn, "SELECT * FROM estatistica_salarial ORDER BY 1,2,3") == antes
    assert _rows(conn, "SELECT count(*) FROM tendencia_mercado")[0][0] == 2   # não duplica
    assert _rows(conn, "SELECT count(*) FROM metrica_dashboard")[0][0] == 2   # histórico acumula


def test_falha_desfaz_tudo(conn, dados, monkeypatch):
    derivadas.recalcular(conn)
    monkeypatch.setattr(derivadas, "SQL_TENDENCIA_SKILL", "SELECT coluna_que_nao_existe")
    with pytest.raises(Exception):
        derivadas.recalcular(conn)
    # nada mudou: sem tabela pela metade, sem snapshot extra
    assert _rows(conn, "SELECT count(*) FROM estatistica_salarial")[0][0] == 3
    assert _rows(conn, "SELECT count(*) FROM tendencia_skill")[0][0] == 4
    assert _rows(conn, "SELECT count(*) FROM metrica_dashboard")[0][0] == 1


def test_banco_vazio(conn):
    derivadas.recalcular(conn)
    total, sal, rem, top = _rows(
        conn, "SELECT total_vagas,salario_medio,percentual_remoto,skill_top FROM v_metrica_dashboard_atual")[0]
    assert (total, sal, rem, top) == (0, None, None, None)
