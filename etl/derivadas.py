"""Recalcula as tabelas derivadas a partir de `vaga`, `vaga_skill` e `skill`.

Roda inteiro em UMA transação: se algo falhar, as tabelas ficam como estavam
(o front nunca enxerga tabela pela metade).

  estatistica_salarial  -> substituída (tecnologia x senioridade x moeda)
  tendencia_mercado     -> substituída (vagas por mês + variação vs mês anterior)
  tendencia_skill       -> substituída (vagas por mês e por skill)
  metrica_dashboard     -> ACRESCENTA 1 linha (histórico); o front lê a mais recente

Decisões de cálculo (ajuste aqui se o time pensar diferente):
- Salário de uma vaga = ponto médio de min/max (ou o único valor informado).
- Salários ESTIMADOS (salario_estimado = true, ex.: previsão da Adzuna) ficam de fora.
- Nunca se misturam moedas: estatísticas são por moeda; a métrica do dashboard usa
  só a moeda MOEDA_DASHBOARD.
- Período = mês de data_publicacao (vagas sem data não entram nas tendências).
"""
from __future__ import annotations

import logging

import psycopg

log = logging.getLogger(__name__)

MOEDA_DASHBOARD = "BRL"

# valor mensal "representativo" da vaga, só para vagas com salário real (não estimado)
_VALOR = "COALESCE((v.salario_min + v.salario_max) / 2, v.salario_min, v.salario_max)"
_COM_SALARIO = ("v.moeda IS NOT NULL AND v.salario_estimado = FALSE "
                "AND (v.salario_min IS NOT NULL OR v.salario_max IS NOT NULL)")

SQL_ESTATISTICA_SALARIAL = f"""
INSERT INTO estatistica_salarial
    (tecnologia, senioridade, moeda, salario_medio, salario_min, salario_max, total_vagas)
SELECT s.nome, v.senioridade, v.moeda,
       ROUND(AVG({_VALOR}), 2),
       MIN(COALESCE(v.salario_min, v.salario_max)),
       MAX(COALESCE(v.salario_max, v.salario_min)),
       COUNT(*)
FROM vaga v
JOIN vaga_skill vs ON vs.vaga_id = v.id
JOIN skill s       ON s.id = vs.skill_id
WHERE {_COM_SALARIO}
GROUP BY s.nome, v.senioridade, v.moeda
"""

SQL_TENDENCIA_MERCADO = """
WITH mensal AS (
    SELECT date_trunc('month', data_publicacao)::date AS mes, COUNT(*) AS n
    FROM vaga
    WHERE data_publicacao IS NOT NULL
    GROUP BY 1
)
INSERT INTO tendencia_mercado (periodo, total_vagas, variacao_percentual)
SELECT to_char(m.mes, 'YYYY-MM'),
       m.n,
       -- só compara com o mês imediatamente anterior (sem "pular" meses vazios)
       CASE WHEN p.n > 0 THEN ROUND(100.0 * (m.n - p.n) / p.n, 2) END
FROM mensal m
LEFT JOIN mensal p ON p.mes = (m.mes - INTERVAL '1 month')::date
"""

SQL_TENDENCIA_SKILL = """
INSERT INTO tendencia_skill (periodo, skill_id, total_vagas)
SELECT to_char(v.data_publicacao, 'YYYY-MM'), vs.skill_id, COUNT(*)
FROM vaga v
JOIN vaga_skill vs ON vs.vaga_id = v.id
WHERE v.data_publicacao IS NOT NULL
GROUP BY 1, 2
"""

# percentual_remoto: só entre vagas com modalidade informada (nao_informado fica fora)
SQL_METRICA_DASHBOARD = f"""
INSERT INTO metrica_dashboard (total_vagas, salario_medio, percentual_remoto, skill_top, moeda)
SELECT
    (SELECT COUNT(*) FROM vaga),
    (SELECT ROUND(AVG({_VALOR}), 2) FROM vaga v
      WHERE {_COM_SALARIO} AND v.moeda = %(moeda)s),
    (SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE modalidade = 'remoto') / NULLIF(COUNT(*), 0), 2)
       FROM vaga WHERE modalidade <> 'nao_informado'),
    (SELECT s.nome FROM vaga_skill vs JOIN skill s ON s.id = vs.skill_id
      GROUP BY s.nome ORDER BY COUNT(*) DESC, s.nome LIMIT 1),
    %(moeda)s
"""


def recalcular(conn: psycopg.Connection) -> dict[str, int]:
    """Recalcula tudo. Retorna quantas linhas cada tabela passou a ter."""
    resumo: dict[str, int] = {}
    with conn.transaction(), conn.cursor() as cur:
        for tabela, sql in (("estatistica_salarial", SQL_ESTATISTICA_SALARIAL),
                            ("tendencia_mercado", SQL_TENDENCIA_MERCADO),
                            ("tendencia_skill", SQL_TENDENCIA_SKILL)):
            cur.execute(f"DELETE FROM {tabela}")
            cur.execute(sql)
            resumo[tabela] = cur.rowcount
        cur.execute(SQL_METRICA_DASHBOARD, {"moeda": MOEDA_DASHBOARD})
        resumo["metrica_dashboard"] = cur.rowcount
    log.info("Derivadas recalculadas: %s", resumo)
    return resumo
