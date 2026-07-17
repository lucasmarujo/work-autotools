"""
Exporta pendências de code review de Merge Requests abertos no GitLab
para arquivos Markdown individuais (um por MR) dentro da pasta reviews-mr/.

Variável de ambiente necessária:
    GITLAB_TOKEN  —  Personal Access Token do GitLab

Uso:
    python export-gitlab-mr-reviews.py
    python export-gitlab-mr-reviews.py --output-dir minha-pasta
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

GITLAB_API_BASE_URL = "https://gitlab.com/api/v4"
OUTPUT_DIR = "reviews-mr"
PER_PAGE = 100

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------


def _get_token() -> str:
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        logger.error("Variável de ambiente GITLAB_TOKEN não definida.")
        sys.exit(1)
    return token


def _build_headers(token: str) -> dict[str, str]:
    return {"PRIVATE-TOKEN": token, "Accept": "application/json"}


# ---------------------------------------------------------------------------
# Paginação genérica
# ---------------------------------------------------------------------------


def _paginated_get(url: str, headers: dict, params: dict | None = None) -> list[dict]:
    """Faz GET paginado na API do GitLab e retorna todos os itens."""
    params = dict(params or {})
    params.setdefault("per_page", PER_PAGE)
    page = 1
    all_items: list[dict] = []

    while True:
        params["page"] = page
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if not data:
            break

        all_items.extend(data)

        total_pages = response.headers.get("x-total-pages")
        if total_pages and page >= int(total_pages):
            break

        page += 1

    return all_items


# ---------------------------------------------------------------------------
# Buscar usuário autenticado
# ---------------------------------------------------------------------------


def get_current_user(headers: dict) -> dict:
    url = f"{GITLAB_API_BASE_URL}/user"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Buscar Merge Requests abertos do usuário
# ---------------------------------------------------------------------------


def fetch_open_merge_requests(headers: dict, author_id: int) -> list[dict]:
    url = f"{GITLAB_API_BASE_URL}/merge_requests"
    params = {
        "state": "opened",
        "scope": "created_by_me",
        "author_id": author_id,
    }
    merge_requests = _paginated_get(url, headers, params)
    logger.info("Merge Requests abertos encontrados: %d", len(merge_requests))
    return merge_requests


# ---------------------------------------------------------------------------
# Buscar discussões de um MR
# ---------------------------------------------------------------------------


def fetch_mr_discussions(headers: dict, project_id: int, mr_iid: int) -> list[dict]:
    url = (
        f"{GITLAB_API_BASE_URL}/projects/{project_id}"
        f"/merge_requests/{mr_iid}/discussions"
    )
    return _paginated_get(url, headers)


# ---------------------------------------------------------------------------
# Extrair comentários pendentes de revisores
# ---------------------------------------------------------------------------


_BOT_BODY_MARKERS = ("<!-- BUGBOT_REVIEW -->", "<!-- BUGBOT_AUTOFIX")


def _is_bot_note(note: dict) -> bool:
    """Identifica notas geradas por bots (ex: Cursor Bugbot)."""
    author = note.get("author", {})
    # Usuário marcado como bot pela API do GitLab
    if author.get("bot", False):
        return True
    # Nome ou username vazio
    if not author.get("name", "").strip() or not author.get("username", "").strip():
        return True
    # Corpo da nota contém marcadores de bot conhecidos
    body = note.get("body", "")
    return any(marker in body for marker in _BOT_BODY_MARKERS)


def _extract_pending_comments(
    discussions: list[dict],
    author_username: str,
) -> list[dict[str, Any]]:
    """
    Filtra notas que:
      - não foram feitas pelo próprio autor do MR
      - pertencem a discussões não resolvidas (quando aplicável)
      - não são notas de sistema
    """
    comments: list[dict[str, Any]] = []

    for discussion in discussions:
        is_resolved = discussion.get("resolved", None)
        if is_resolved is True:
            continue

        for note in discussion.get("notes", []):
            if note.get("system", False):
                continue
            if note["author"]["username"] == author_username:
                continue

            position = note.get("position") or {}
            file_path = position.get("new_path") or position.get("old_path") or ""
            new_line = position.get("new_line")
            old_line = position.get("old_line")

            line_info = ""
            if new_line:
                line_info = f"Linha {new_line}"
            elif old_line:
                line_info = f"Linha {old_line} (removida)"

            comments.append(
                {
                    "author": note["author"].get("name", "").strip()
                             or note["author"].get("username", "").strip()
                             or "Bot",
                    "body": note.get("body", ""),
                    "file": file_path,
                    "line": line_info,
                    "created_at": note.get("created_at", ""),
                }
            )

    return sorted(comments, key=lambda c: c["created_at"])


# ---------------------------------------------------------------------------
# Seleção interativa de autores
# ---------------------------------------------------------------------------


def _collect_unique_authors(mrs_data: list[dict[str, Any]]) -> list[str]:
    """Retorna lista ordenada de autores únicos entre todos os MRs."""
    seen: set[str] = set()
    authors: list[str] = []
    for mr in mrs_data:
        for comment in mr["comments"]:
            name = comment["author"]
            if name not in seen:
                seen.add(name)
                authors.append(name)
    return sorted(authors)


def _prompt_author_selection(authors: list[str]) -> set[str]:
    """
    Exibe os autores encontrados e retorna o conjunto dos selecionados.
    Conjunto vazio = todos.
    """
    print()
    print("  Autores encontrados nos comentários:")
    print()
    print("  [0] Todos")
    for i, author in enumerate(authors, start=1):
        print(f"  [{i}] {author}")
    print()

    raw = input("  Selecione os autores (ex: 1  ou  1,2  ou  0 para todos): ").strip()

    if not raw or raw == "0":
        return set()

    selected: set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        idx = int(part)
        if idx == 0:
            return set()  # todos
        if 1 <= idx <= len(authors):
            selected.add(authors[idx - 1])

    return selected


# ---------------------------------------------------------------------------
# Geração do Markdown
# ---------------------------------------------------------------------------


def _sanitize_filename(title: str) -> str:
    """Remove caracteres inválidos do título para usar como nome de arquivo."""
    import re
    sanitized = re.sub(r'[<>:"/\\|?*]', '-', title)
    sanitized = re.sub(r'-{2,}', '-', sanitized).strip(' .-')
    return sanitized[:120]


def generate_markdown_for_mr(mr_data: dict[str, Any]) -> str:
    """Gera conteúdo Markdown para um único MR."""
    lines: list[str] = [f"# MR: {mr_data['title']}", ""]
    lines.append(f"**Link:** {mr_data['url']}")
    lines.append("")

    if not mr_data["comments"]:
        lines.append("_Sem comentários pendentes de revisores._")
    else:
        for comment in mr_data["comments"]:
            lines.append("## Comentário")
            lines.append("")
            lines.append(f"**Autor:** {comment['author']}")
            lines.append("")

            if comment["file"]:
                location = comment["file"]
                if comment["line"]:
                    location += f" — {comment['line']}"
                lines.append(f"**Arquivo:** `{location}`")
                lines.append("")

            lines.append("**Trecho do comentário:**")
            lines.append("")
            for body_line in comment["body"].splitlines():
                lines.append(f"> {body_line}")
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orquestração principal
# ---------------------------------------------------------------------------


def run(output_dir: str | None = None):
    """Entry-point chamável pelo main.py ou pela CLI."""
    token = _get_token()
    headers = _build_headers(token)

    logger.info("Autenticando no GitLab...")
    user = get_current_user(headers)
    username = user["username"]
    logger.info("Usuário: %s (id=%d)", username, user["id"])

    logger.info("Buscando Merge Requests abertos...")
    merge_requests = fetch_open_merge_requests(headers, user["id"])

    if not merge_requests:
        logger.info("Nenhum MR aberto encontrado.")
        return

    # --- 1. Coletar todos os comentários de todos os MRs ---
    mrs_data: list[dict[str, Any]] = []

    for mr in merge_requests:
        title = mr["title"]
        web_url = mr["web_url"]
        project_id = mr["project_id"]
        mr_iid = mr["iid"]

        logger.info("Buscando discussões do MR !%d — %s", mr_iid, title)
        discussions = fetch_mr_discussions(headers, project_id, mr_iid)
        comments = _extract_pending_comments(discussions, username)
        logger.info("  → %d comentário(s) pendente(s)", len(comments))

        if comments:
            mrs_data.append(
                {"title": title, "url": web_url, "iid": mr_iid, "comments": comments}
            )

    if not mrs_data:
        logger.info("Nenhum comentário pendente encontrado em nenhum MR.")
        return

    # --- 2. Perguntar ao usuário quais autores incluir ---
    all_authors = _collect_unique_authors(mrs_data)
    selected_authors = _prompt_author_selection(all_authors)

    if selected_authors:
        logger.info("Filtrando por: %s", ", ".join(sorted(selected_authors)))
    else:
        logger.info("Incluindo comentários de todos os autores.")

    # --- 3. Gerar arquivos ---
    output_path = Path(output_dir or OUTPUT_DIR)
    output_path.mkdir(parents=True, exist_ok=True)
    logger.info("Pasta de saída: %s", output_path.resolve())

    generated_files: list[Path] = []

    for mr in mrs_data:
        filtered_comments = (
            [c for c in mr["comments"] if c["author"] in selected_authors]
            if selected_authors
            else mr["comments"]
        )

        if not filtered_comments:
            logger.info("MR !%d — sem comentários dos autores selecionados, ignorado.", mr["iid"])
            continue

        mr_data = {"title": mr["title"], "url": mr["url"], "comments": filtered_comments}
        markdown_content = generate_markdown_for_mr(mr_data)

        safe_name = _sanitize_filename(mr["title"])
        file_name = f"MR-{mr['iid']}-{safe_name}.md"
        file_path = output_path / file_name
        file_path.write_text(markdown_content, encoding="utf-8")
        generated_files.append(file_path)
        logger.info("  → Arquivo: %s", file_path.name)

    logger.info("%d arquivo(s) gerado(s) em %s", len(generated_files), output_path.resolve())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Exporta pendências de code review de MRs abertos no GitLab.",
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help=f"Pasta de saída dos arquivos Markdown (padrão: {OUTPUT_DIR})",
    )
    args = parser.parse_args()
    run(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
