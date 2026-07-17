"""
Gera um resumo em Markdown dos commits feitos ontem pelo usuário autenticado
no GitLab, agrupados por branch.

Variável de ambiente necessária:
    GITLAB_TOKEN  —  Personal Access Token do GitLab

Uso:
    python gitlab-yesterday-summary.py
    python gitlab-yesterday-summary.py --output-dir minha-pasta
"""

import os
import sys
import argparse
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

GITLAB_API_BASE_URL = "https://gitlab.com/api/v4"
OUTPUT_DIR = "commits-summary"
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
# Buscar push events de ontem (project_id + branch)
# ---------------------------------------------------------------------------


def _fetch_push_event_targets(
    headers: dict, user_id: int, after: str, before: str
) -> set[tuple[int, str]]:
    url = f"{GITLAB_API_BASE_URL}/users/{user_id}/events"
    params = {"action": "pushed", "after": after, "before": before}
    events = _paginated_get(url, headers, params)

    logger.info("Total de eventos no período: %d", len(events))
    push_events = [e for e in events if "pushed" in e.get("action_name", "").lower()]
    logger.info("Push events filtrados: %d", len(push_events))

    targets: set[tuple[int, str]] = set()
    for event in push_events:
        project_id = event.get("project_id")
        push_data = event.get("push_data") or {}
        ref = push_data.get("ref")
        if project_id and ref:
            targets.add((project_id, ref))

    return targets


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
# Buscar commits do usuário no projeto/branch no intervalo de ontem
# ---------------------------------------------------------------------------


def _fetch_branch_commits(
    headers: dict,
    project_id: int,
    branch: str,
    author_name: str,
    since: str,
    until: str,
) -> list[dict]:
    url = f"{GITLAB_API_BASE_URL}/projects/{project_id}/repository/commits"
    params = {
        "since": since,
        "until": until,
        "author": author_name,
        "ref_name": branch,
    }
    return _paginated_get(url, headers, params)


# ---------------------------------------------------------------------------
# Geração do Markdown
# ---------------------------------------------------------------------------


def _format_committed_at(committed_at: str) -> str:
    try:
        dt = datetime.fromisoformat(committed_at.replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S")
    except Exception:
        return committed_at


def _short_sha(sha: str) -> str:
    return sha[:8]


def generate_markdown(
    user_name: str,
    yesterday: date,
    branches_commits: dict[str, list[dict[str, Any]]],
) -> str:
    total_commits = sum(len(commits) for commits in branches_commits.values())
    lines: list[str] = [
        f"# Resumo de Commits — {yesterday.isoformat()}",
        "",
        f"**Usuário:** {user_name}  ",
        f"**Data:** {yesterday.isoformat()}  ",
        f"**Total de branches:** {len(branches_commits)}  ",
        f"**Total de commits:** {total_commits}  ",
        "",
        "---",
        "",
    ]

    for branch in sorted(branches_commits.keys()):
        commits = branches_commits[branch]
        lines.append(f"## {branch}")
        lines.append("")

        lines.append("| SHA | Mensagem | Projeto | Horário |")
        lines.append("|-----|----------|---------|---------|")

        commits_sorted = sorted(commits, key=lambda c: c.get("committed_date", ""))
        for commit in commits_sorted:
            sha = _short_sha(commit.get("id", ""))
            message = commit.get("title", "").replace("|", "\\|")
            project_name = commit.get("_project_name", "—").replace("|", "\\|")
            committed_at = _format_committed_at(commit.get("committed_date", ""))
            lines.append(f"| `{sha}` | {message} | {project_name} | {committed_at} |")

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

    yesterday = date.today() - timedelta(days=1)
    # GitLab `after` é exclusivo: passa anteontem para capturar eventos de ontem
    after_str = (yesterday - timedelta(days=1)).isoformat()
    before_str = date.today().isoformat()
    since_iso = f"{yesterday.isoformat()}T00:00:00Z"
    until_iso = f"{yesterday.isoformat()}T23:59:59Z"

    logger.info("Buscando push events em %s...", yesterday.isoformat())
    targets = _fetch_push_event_targets(headers, user["id"], after_str, before_str)

    if not targets:
        logger.info("Nenhum push registrado ontem.")
        return

    logger.info("%d par(es) projeto/branch encontrado(s).", len(targets))

    project_info_cache: dict[int, dict] = {}
    branches_commits: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for project_id, branch in sorted(targets):
        project_info = project_info_cache.get(project_id)
        if project_info is None:
            project_info = _fetch_project_info(headers, project_id)
            if not project_info:
                logger.warning("Projeto id=%d não encontrado ou sem acesso.", project_id)
                continue
            project_info_cache[project_id] = project_info

        project_name = project_info.get("name", str(project_id))
        namespace = project_info.get("namespace", {}).get("full_path", "—")

        logger.info(
            "Buscando commits: %s/%s @ %s...", namespace, project_name, branch
        )
        commits = _fetch_branch_commits(
            headers, project_id, branch, user_name, since_iso, until_iso
        )
        logger.info("  → %d commit(s) encontrado(s)", len(commits))

        for commit in commits:
            commit["_project_name"] = project_name
            branches_commits[branch].append(commit)

    if not branches_commits:
        logger.info("Nenhum commit encontrado ontem.")
        return

    markdown_content = generate_markdown(user_name, yesterday, branches_commits)

    output_path = Path(output_dir or OUTPUT_DIR)
    output_path.mkdir(parents=True, exist_ok=True)

    file_name = f"commits-{yesterday.isoformat()}.md"
    file_path = output_path / file_name
    file_path.write_text(markdown_content, encoding="utf-8")

    logger.info("Resumo salvo em: %s", file_path.resolve())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Gera resumo de commits do dia anterior no GitLab.",
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
