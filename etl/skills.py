"""Identificação de skills: só casa com o catálogo que JÁ existe na tabela `skill`.
Não cria skills novas (o catálogo é curado por vocês)."""
from __future__ import annotations

import re


def compilar_catalogo(skills: list[tuple[int, str]]) -> list[tuple[int, re.Pattern]]:
    padroes = []
    for skill_id, nome in skills:
        # borda "inteligente": evita casar "java" dentro de "javascript" e aceita C++, C#, .NET
        p = re.compile(r"(?<![\w+#.])" + re.escape(nome.strip()) + r"(?![\w+#])", re.IGNORECASE)
        padroes.append((skill_id, p))
    return padroes


def identificar(titulo: str, descricao: str | None, catalogo: list[tuple[int, re.Pattern]]) -> set[int]:
    texto = f"{titulo}\n{descricao or ''}"
    return {sid for sid, p in catalogo if p.search(texto)}
