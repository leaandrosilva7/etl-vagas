-- =====================================================================
-- 003_derivadas.sql  (responsabilidade: ETL)
-- Roda DEPOIS do 002. Pode ser executado mais de uma vez.
-- metrica_dashboard.salario_medio é de UMA moeda (nunca mistura BRL com USD);
-- a coluna `moeda` diz qual. Hoje a ETL usa BRL (etl/derivadas.py).
-- =====================================================================
ALTER TABLE metrica_dashboard ADD COLUMN IF NOT EXISTS moeda CHAR(3);

-- Dashboard: sempre a linha mais recente de metrica_dashboard.
--   select * from v_metrica_dashboard_atual;
CREATE OR REPLACE VIEW v_metrica_dashboard_atual AS
SELECT * FROM metrica_dashboard ORDER BY calculado_em DESC, id DESC LIMIT 1;
ALTER VIEW v_metrica_dashboard_atual SET (security_invoker = true);
