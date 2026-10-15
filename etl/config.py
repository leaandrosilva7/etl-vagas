"""Configuração via variáveis de ambiente (.env fica fora do Git)."""
import os

from dotenv import load_dotenv

load_dotenv()


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("Defina DATABASE_URL no .env (string de conexão Postgres do Supabase).")
    return url


def adzuna_credentials() -> tuple[str, str]:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("Defina ADZUNA_APP_ID e ADZUNA_APP_KEY no .env "
                           "(cadastro gratuito em developer.adzuna.com).")
    return app_id, app_key


def search_terms() -> list[str]:
    """Termos de busca da Adzuna, separados por vírgula em SEARCH_TERMS."""
    bruto = os.getenv("SEARCH_TERMS", "")
    return [t.strip() for t in bruto.split(",") if t.strip()]


def max_pages(padrao: int = 1) -> int:
    """Páginas por termo (cada página = 1 chamada à API). MAX_PAGES no .env."""
    try:
        return max(1, int(os.getenv("MAX_PAGES", padrao)))
    except ValueError:
        raise RuntimeError("MAX_PAGES deve ser um número inteiro.") from None


def jobicy_tag() -> str | None:
    """Tag opcional da Jobicy (em inglês, ex.: python). Vazio = vagas mais recentes."""
    return os.getenv("JOBICY_TAG", "").strip() or None
