"""
Gera um resumo em Markdown de todos os Merge Requests abertos
(não merged) do usuário autenticado no GitLab, agrupados por projeto
e por branch de origem.

Variável de ambiente necessária:
    GITLAB_TOKEN  —  Personal Access Token do GitLab

Uso:
    python gitlab-open-mrs-summary.py
    python gitlab-open-mrs-summary.py --output-dir minha-pasta
"""

import os
import sys
import argparse
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

GITLAB_API_BASE_URL = "https://gitlab.com/api/v4"
OUTPUT_DIR = "open-mrs"
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
# Buscar MRs abertos do usuário (não merged, não fechados)
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
# Buscar informações do projeto
# ---------------------------------------------------------------------------


def _fetch_project_info(headers: dict, project_id: int) -> dict | None:
    url = f"{GITLAB_API_BASE_URL}/projects/{project_id}"
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Agrupamento por projeto e branch
# ---------------------------------------------------------------------------


def _group_by_project_and_branch(
    headers: dict, merge_requests: list[dict]
) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}

    for mr in merge_requests:
        project_id = mr["project_id"]

        if project_id not in grouped:
            project_info = _fetch_project_info(headers, project_id) or {}
            grouped[project_id] = {
                "id": project_id,
                "name": project_info.get("name", str(project_id)),
                "namespace": project_info.get("namespace", {}).get("full_path", "—"),
                "url": project_info.get("web_url", ""),
                "branches": {},
            }

        source_branch = mr.get("source_branch", "—")
        branches = grouped[project_id]["branches"]
        branches.setdefault(source_branch, []).append(mr)

    return sorted(
        grouped.values(),
        key=lambda p: f"{p['namespace']}/{p['name']}".lower(),
    )


# ---------------------------------------------------------------------------
# Geração do Markdown
# ---------------------------------------------------------------------------


def _format_datetime(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value or "—"


def generate_markdown(
    user_name: str,
    today: date,
    projects: list[dict[str, Any]],
) -> str:
    total_mrs = sum(
        len(mrs) for project in projects for mrs in project["branches"].values()
    )

    lines: list[str] = [
        f"# Merge Requests Abertos — {today.isoformat()}",
        "",
        f"**Usuário:** {user_name}  ",
        f"**Data:** {today.isoformat()}  ",
        f"**Total de projetos:** {len(projects)}  ",
        f"**Total de MRs abertos:** {total_mrs}  ",
        "",
        "---",
        "",
    ]

    for project in projects:
        lines.append(f"## {project['name']}")
        lines.append("")
        lines.append(f"**Namespace:** {project['namespace']}  ")
        if project["url"]:
            lines.append(f"**URL:** {project['url']}  ")
        lines.append("")

        sorted_branches = sorted(project["branches"].items(), key=lambda kv: kv[0].lower())

        for source_branch, mrs in sorted_branches:
            lines.append(f"### Branch: `{source_branch}`")
            lines.append("")
            lines.append("| MR | Título | Target | Criado em | Atualizado em | Link |")
            lines.append("|----|--------|--------|-----------|---------------|------|")

            for mr in sorted(mrs, key=lambda m: m.get("created_at", "")):
                iid = mr.get("iid", "—")
                title = (mr.get("title", "") or "").replace("|", "\\|")
                target = mr.get("target_branch", "—")
                created = _format_datetime(mr.get("created_at", ""))
                updated = _format_datetime(mr.get("updated_at", ""))
                web_url = mr.get("web_url", "")
                lines.append(
                    f"| !{iid} | {title} | `{target}` | {created} | {updated} | {web_url} |"
                )

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
    user_name = user["name"]
    logger.info("Usuário: %s (id=%d)", user_name, user["id"])

    logger.info("Buscando Merge Requests abertos...")
    merge_requests = fetch_open_merge_requests(headers, user["id"])

    if not merge_requests:
        logger.info("Nenhum MR aberto encontrado.")
        return

    logger.info("Agrupando MRs por projeto e branch...")
    projects = _group_by_project_and_branch(headers, merge_requests)
    logger.info("%d projeto(s) com MRs abertos.", len(projects))

    today = date.today()
    markdown_content = generate_markdown(user_name, today, projects)

    output_path = Path(output_dir or OUTPUT_DIR)
    output_path.mkdir(parents=True, exist_ok=True)

    file_name = f"open-mrs-{today.isoformat()}.md"
    file_path = output_path / file_name
    file_path.write_text(markdown_content, encoding="utf-8")

    logger.info("Resumo salvo em: %s", file_path.resolve())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Gera resumo dos MRs abertos do usuário no GitLab agrupados por projeto e branch.",
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help=f"Pasta de saída do arquivo Markdown (padrão: {OUTPUT_DIR})",
    )
    args = parser.parse_args()
    run(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
