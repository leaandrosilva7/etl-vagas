-- =====================================================================
-- 002_ajustes_etl.sql  (responsabilidade: ETL)
-- Roda DEPOIS do schema.sql. Pode ser executado mais de uma vez.
-- Não apaga dados de vaga/skill/etc.; só estende e recria tabelas derivadas.
-- Convenção: salario_min/max são sempre MENSAIS na moeda da vaga (campo moeda).
-- =====================================================================

-- 1) Suporte a fontes internacionais ------------------------------------
ALTER TABLE vaga ALTER COLUMN estado TYPE VARCHAR(60);
ALTER TABLE vaga ADD COLUMN IF NOT EXISTS pais CHAR(2) NOT NULL DEFAULT 'BR';
ALTER TABLE vaga ADD COLUMN IF NOT EXISTS moeda CHAR(3);
ALTER TABLE vaga ADD COLUMN IF NOT EXISTS salario_estimado BOOLEAN NOT NULL DEFAULT FALSE;

-- 2) Índices para os filtros da interface (UC09-UC14, UC19) -------------
CREATE INDEX IF NOT EXISTS idx_vaga_data_publicacao ON vaga (data_publicacao DESC);
CREATE INDEX IF NOT EXISTS idx_vaga_senioridade     ON vaga (senioridade);
CREATE INDEX IF NOT EXISTS idx_vaga_modalidade      ON vaga (modalidade);
CREATE INDEX IF NOT EXISTS idx_vaga_cidade          ON vaga (cidade);
CREATE INDEX IF NOT EXISTS idx_vaga_pais            ON vaga (pais);
CREATE INDEX IF NOT EXISTS idx_vaga_skill_skill     ON vaga_skill (skill_id);

-- 3) estatistica_salarial: agora separada por moeda (tabela derivada,
--    é recalculada a cada execução da ETL, então pode ser recriada) ------
DROP TABLE IF EXISTS estatistica_salarial;
CREATE TABLE estatistica_salarial (
    tecnologia    TEXT          NOT NULL,
    senioridade   TEXT          NOT NULL,
    moeda         CHAR(3)       NOT NULL,
    salario_medio NUMERIC(14,2),
    salario_min   NUMERIC(14,2),
    salario_max   NUMERIC(14,2),
    total_vagas   INTEGER       NOT NULL DEFAULT 0,
    PRIMARY KEY (tecnologia, senioridade, moeda)
);
ALTER TABLE estatistica_salarial ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS leitura_autenticados ON estatistica_salarial;
CREATE POLICY leitura_autenticados ON estatistica_salarial
    FOR SELECT TO authenticated USING (true);

-- 4) Tendência por tecnologia (UC07: "volume de vagas e de tecnologias") -
CREATE TABLE IF NOT EXISTS tendencia_skill (
    periodo      CHAR(7)  NOT NULL,                       -- 'YYYY-MM'
    skill_id     INTEGER  NOT NULL REFERENCES skill(id) ON DELETE CASCADE,
    total_vagas  INTEGER  NOT NULL DEFAULT 0,
    PRIMARY KEY (periodo, skill_id)
);
ALTER TABLE tendencia_skill ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS leitura_autenticados ON tendencia_skill;
CREATE POLICY leitura_autenticados ON tendencia_skill
    FOR SELECT TO authenticated USING (true);

-- 5) Ranking de tecnologias filtrável por período (UC06) ----------------
--    Uso: select * from fn_tecnologias_demandadas('2026-01-01', '2026-12-31', 10);
--    Datas nulas = sem limite. A interface (back-end) chama esta função.
CREATE OR REPLACE FUNCTION fn_tecnologias_demandadas(
    p_inicio DATE DEFAULT NULL,
    p_fim    DATE DEFAULT NULL,
    p_limite INTEGER DEFAULT 20
)
RETURNS TABLE (tecnologia TEXT, total_vagas BIGINT, percentual NUMERIC)
LANGUAGE sql STABLE SECURITY INVOKER AS $$
    WITH base AS (
        SELECT v.id
        FROM vaga v
        WHERE (p_inicio IS NULL OR v.data_publicacao >= p_inicio)
          AND (p_fim    IS NULL OR v.data_publicacao <= p_fim)
    ),
    total AS (SELECT COUNT(*) AS n FROM base)
    SELECT s.nome::TEXT,
           COUNT(*)::BIGINT,
           ROUND(100.0 * COUNT(*) / NULLIF((SELECT n FROM total), 0), 2)
    FROM base b
    JOIN vaga_skill vs ON vs.vaga_id = b.id
    JOIN skill s       ON s.id = vs.skill_id
    GROUP BY s.nome
    ORDER BY COUNT(*) DESC, s.nome
    LIMIT p_limite;
$$;
GRANT EXECUTE ON FUNCTION fn_tecnologias_demandadas(DATE, DATE, INTEGER) TO authenticated;
