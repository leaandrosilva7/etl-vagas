import pytest

from etl import config, run_all


def test_uma_fonte_falha_e_as_outras_continuam(monkeypatch):
    chamadas = []

    def quebra(conn, a):
        raise RuntimeError("API fora do ar")

    def ok(conn, a):
        chamadas.append("ok")
        return {"recebidas": 1}

    monkeypatch.setattr(run_all, "FONTES", {"a": quebra, "b": ok})
    assert run_all.main(["--dry-run", "--fontes", "a", "b"]) == 1   # falha => exit code 1
    assert chamadas == ["ok"]                                       # mas "b" rodou


def test_tudo_certo_retorna_zero(monkeypatch):
    monkeypatch.setattr(run_all, "FONTES", {"b": lambda conn, a: {"recebidas": 1}})
    assert run_all.main(["--dry-run"]) == 0


def test_dry_run_nao_conecta_no_banco(monkeypatch):
    def proibido(*a, **k):
        raise AssertionError("dry-run não pode conectar")

    monkeypatch.setattr(run_all.load, "conectar", proibido)
    monkeypatch.setattr(run_all, "FONTES", {"b": lambda conn, a: {}})
    assert run_all.main(["--dry-run"]) == 0


def test_sem_banco_retorna_erro(monkeypatch):
    monkeypatch.setattr(run_all.config, "database_url", lambda: (_ for _ in ()).throw(RuntimeError("sem url")))
    monkeypatch.setattr(run_all, "FONTES", {"b": lambda conn, a: {}})
    assert run_all.main([]) == 1


def test_config_termos_e_paginas(monkeypatch):
    monkeypatch.setenv("SEARCH_TERMS", " desenvolvedor, analista de dados ,, estagio ti ")
    assert config.search_terms() == ["desenvolvedor", "analista de dados", "estagio ti"]
    monkeypatch.setenv("MAX_PAGES", "5")
    assert config.max_pages() == 5
    monkeypatch.setenv("MAX_PAGES", "abc")
    with pytest.raises(RuntimeError):
        config.max_pages()
