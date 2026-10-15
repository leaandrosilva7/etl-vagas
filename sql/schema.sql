-- =====================================================================
-- Plataforma de Analise de Vagas - schema do ETL (PostgreSQL / Supabase)
-- Pode ser executado mais de uma vez (IF NOT EXISTS).
-- =====================================================================

-- 1) Execucoes de coleta (classe ColetaVagas) -------------------------
CREATE TABLE IF NOT EXISTS coleta_vagas (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fonte             TEXT        NOT NULL,
    termo_busca       TEXT        NOT NULL,
    pais              TEXT        NOT NULL DEFAULT 'BR',
    executada_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_coletado    INT         NOT NULL DEFAULT 0,
    total_validas     INT         NOT NULL DEFAULT 0,
    total_rejeitadas  INT         NOT NULL DEFAULT 0,
    status            TEXT        NOT NULL DEFAULT 'em_andamento'
                      CHECK (status IN ('em_andamento', 'sucesso', 'erro'))
);

-- 2) Dado bruto (staging): guarda o JSON original de cada vaga ---------
CREATE TABLE IF NOT EXISTS raw_vagas (
    fonte        TEXT        NOT NULL,
    id_externo   TEXT        NOT NULL,
    payload      JSONB       NOT NULL,
    coletado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (fonte, id_externo)
);

-- 3) Vagas tratadas (classe Vaga) --------------------------------------
CREATE TABLE IF NOT EXISTS vaga (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fonte            TEXT          NOT NULL,
    id_externo       TEXT          NOT NULL,
    titulo           TEXT          NOT NULL,
    empresa          TEXT          NOT NULL,
    cidade           TEXT,
    estado           CHAR(2),
    localizacao      TEXT,
    modalidade       TEXT          NOT NULL DEFAULT 'nao_informado'
                     CHECK (modalidade IN ('remoto', 'hibrido', 'presencial', 'nao_informado')),
    senioridade      TEXT          NOT NULL DEFAULT 'nao_informado'
                     CHECK (senioridade IN ('estagio', 'trainee', 'junior', 'pleno', 'senior', 'nao_informado')),
    salario_min      NUMERIC(12,2),
    salario_max      NUMERIC(12,2),
    descricao        TEXT,
    url_original     TEXT          NOT NULL,
    data_publicacao  DATE,
    data_coleta      DATE          NOT NULL DEFAULT CURRENT_DATE,
    coleta_id        BIGINT REFERENCES coleta_vagas(id) ON DELETE SET NULL,
    -- evita duplicidade: a mesma vaga da mesma fonte so existe uma vez
    CONSTRAINT uq_vaga_fonte_externo UNIQUE (fonte, id_externo),
    CONSTRAINT ck_salario CHECK (salario_min IS NULL OR salario_max IS NULL OR salario_min <= salario_max)
);
CREATE INDEX IF NOT EXISTS ix_vaga_data_pub    ON vaga (data_publicacao DESC);
CREATE INDEX IF NOT EXISTS ix_vaga_modalidade  ON vaga (modalidade);
CREATE INDEX IF NOT EXISTS ix_vaga_senioridade ON vaga (senioridade);
CREATE INDEX IF NOT EXISTS ix_vaga_cidade      ON vaga (cidade);

-- 4) Skills (classes Skill e VagaSkill) -------------------------------
CREATE TABLE IF NOT EXISTS skill (
    id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS vaga_skill (
    vaga_id   BIGINT NOT NULL REFERENCES vaga(id)  ON DELETE CASCADE,
    skill_id  BIGINT NOT NULL REFERENCES skill(id) ON DELETE CASCADE,
    PRIMARY KEY (vaga_id, skill_id)
);
CREATE INDEX IF NOT EXISTS ix_vaga_skill_skill ON vaga_skill (skill_id);

-- 5) Registros rejeitados na validacao ---------------------------------
CREATE TABLE IF NOT EXISTS vaga_rejeitada (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    coleta_id   BIGINT REFERENCES coleta_vagas(id) ON DELETE SET NULL,
    fonte       TEXT,
    id_externo  TEXT,
    motivo      TEXT        NOT NULL,
    payload     JSONB,
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 6) Tabelas derivadas (recalculadas pelo ETL a cada execucao) ---------
CREATE TABLE IF NOT EXISTS estatistica_salarial (
    tecnologia     TEXT          NOT NULL,
    senioridade    TEXT          NOT NULL,
    salario_medio  NUMERIC(12,2),
    salario_min    NUMERIC(12,2),
    salario_max    NUMERIC(12,2),
    total_vagas    INT           NOT NULL,
    PRIMARY KEY (tecnologia, senioridade)
);

CREATE TABLE IF NOT EXISTS tendencia_mercado (
    periodo              TEXT PRIMARY KEY,          -- 'YYYY-MM'
    total_vagas          INT NOT NULL,
    variacao_percentual  NUMERIC(8,2)               -- vs. periodo anterior
);

CREATE TABLE IF NOT EXISTS metrica_dashboard (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    calculado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_vagas        INT NOT NULL,
    salario_medio      NUMERIC(12,2),
    percentual_remoto  NUMERIC(5,2),
    skill_top          TEXT
);

-- 7) View: tecnologias mais demandadas por mes (UC06) -------------------
CREATE OR REPLACE VIEW v_tecnologias_demandadas AS
SELECT s.nome                                  AS tecnologia,
       to_char(v.data_publicacao, 'YYYY-MM')   AS periodo,
       COUNT(*)                                AS total_vagas
FROM vaga v
JOIN vaga_skill vs ON vs.vaga_id = v.id
JOIN skill s       ON s.id = vs.skill_id
WHERE v.data_publicacao IS NOT NULL
GROUP BY s.nome, to_char(v.data_publicacao, 'YYYY-MM');

-- 8) Seguranca (Supabase) -----------------------------------------------
-- RLS ligado em tudo: sem isso, a API publica do Supabase exporia as tabelas.
-- O ETL conecta direto no Postgres (role postgres), que ignora o RLS.
-- Usuarios logados (authenticated) so podem LER as tabelas de consulta.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['coleta_vagas','raw_vagas','vaga','skill','vaga_skill',
                             'vaga_rejeitada','estatistica_salarial','tendencia_mercado',
                             'metrica_dashboard']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;

    FOREACH t IN ARRAY ARRAY['vaga','skill','vaga_skill','estatistica_salarial',
                             'tendencia_mercado','metrica_dashboard']
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = t AND policyname = 'leitura_autenticados') THEN
            EXECUTE format('CREATE POLICY leitura_autenticados ON %I FOR SELECT TO authenticated USING (true)', t);
        END IF;
    END LOOP;
END $$;

ALTER VIEW v_tecnologias_demandadas SET (security_invoker = true);
