"""
Gera um resumo em Markdown dos commits feitos ontem pelo usuário autenticado
no GitHub, agrupados por branch.

Variável de ambiente necessária:
    GITHUB_TOKEN  —  Personal Access Token do GitHub (escopo `repo` para
                     enxergar repositórios privados)

Uso:
    python github-yesterday-summary.py
    python github-yesterday-summary.py --output-dir minha-pasta
"""

import os
import sys
import argparse
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
OUTPUT_DIR = "commits-summary"
PER_PAGE = 100
# A API de eventos do GitHub expõe no máximo 300 eventos (3 páginas de 100).
MAX_EVENT_PAGES = 3
BRANCH_REF_PREFIX = "refs/heads/"

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
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        logger.error("Variável de ambiente GITHUB_TOKEN não definida.")
        sys.exit(1)
    return token


def _build_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


# ---------------------------------------------------------------------------
# Paginação genérica
# ---------------------------------------------------------------------------


def _paginated_get(
    url: str, headers: dict, params: dict | None = None, max_pages: int | None = None
) -> list[dict]:
    params = dict(params or {})
    params.setdefault("per_page", PER_PAGE)
    page = 1
    all_items: list[dict] = []

    while max_pages is None or page <= max_pages:
        params["page"] = page
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if not data:
            break

        all_items.extend(data)

        if len(data) < params["per_page"]:
            break

        page += 1

    return all_items


# ---------------------------------------------------------------------------
# Buscar usuário autenticado
# ---------------------------------------------------------------------------


def get_current_user(headers: dict) -> dict:
    url = f"{GITHUB_API_BASE_URL}/user"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Buscar push events (repositório + branch)
# ---------------------------------------------------------------------------


def _fetch_user_events(headers: dict, login: str) -> list[dict]:
    url = f"{GITHUB_API_BASE_URL}/users/{login}/events"
    return _paginated_get(url, headers, max_pages=MAX_EVENT_PAGES)


def collect_push_targets(
    events: list[dict], target_dates: set[date]
) -> set[tuple[str, str]]:
    """Extrai os pares (repositório, branch) com push nas datas informadas.

    O payload de PushEvent não traz os commits em repositórios privados, então
    apenas o alvo do push é aproveitado; os commits vêm da API do repositório.
    """
    targets: set[tuple[str, str]] = set()

    for event in events:
        if event.get("type") != "PushEvent":
            continue

        pushed_at = _parse_datetime(event.get("created_at", ""))
        if pushed_at is None or pushed_at.date() not in target_dates:
            continue

        ref = (event.get("payload") or {}).get("ref") or ""
        repository = (event.get("repo") or {}).get("name")
        if not repository or not ref.startswith(BRANCH_REF_PREFIX):
            continue

        targets.add((repository, ref.removeprefix(BRANCH_REF_PREFIX)))

    return targets


# ---------------------------------------------------------------------------
# Buscar commits do usuário no repositório/branch no intervalo de ontem
# ---------------------------------------------------------------------------


def _fetch_branch_commits(
    headers: dict,
    repository: str,
    branch: str,
    author: str,
    since: str,
    until: str,
) -> list[dict]:
    url = f"{GITHUB_API_BASE_URL}/repos/{repository}/commits"
    params = {
        "sha": branch,
        "author": author,
        "since": since,
        "until": until,
        "per_page": PER_PAGE,
    }
    response = requests.get(url, headers=headers, params=params, timeout=30)
    if response.status_code in (404, 409):
        return []
    response.raise_for_status()

    commits = response.json()
    if len(commits) < PER_PAGE:
        return commits

    return _paginated_get(url, headers, params)


def _is_merge_commit(commit: dict) -> bool:
    return len(commit.get("parents") or []) > 1


def keep_origin_branch(
    branches_commits: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Mantém cada commit apenas na branch em que ele está mais perto do topo.

    Branches criadas a partir de outra herdam o histórico de origem; a menor
    distância até o topo indica onde o commit foi realmente feito.
    """
    owner: dict[str, str] = {}
    closest: dict[str, int] = {}

    for branch in sorted(branches_commits):
        for commit in branches_commits[branch]:
            sha = commit.get("sha", "")
            position = commit.get("_position", 0)
            if sha not in closest or position < closest[sha]:
                closest[sha] = position
                owner[sha] = branch

    result: dict[str, list[dict[str, Any]]] = {}
    for branch in sorted(branches_commits):
        kept = [
            commit
            for commit in branches_commits[branch]
            if owner.get(commit.get("sha", "")) == branch
        ]
        if kept:
            result[branch] = kept

    return result


# ---------------------------------------------------------------------------
# Intervalo de datas
# ---------------------------------------------------------------------------


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _to_utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_bounds_utc(day: date) -> tuple[str, str]:
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day, time.max).astimezone()
    return _to_utc_iso(start), _to_utc_iso(end)


# ---------------------------------------------------------------------------
# Geração do Markdown
# ---------------------------------------------------------------------------


def _commit_datetime(commit: dict) -> datetime | None:
    author = (commit.get("commit") or {}).get("author") or {}
    return _parse_datetime(author.get("date", ""))


def _format_committed_at(commit: dict) -> str:
    committed_at = _commit_datetime(commit)
    return committed_at.strftime("%H:%M:%S") if committed_at else "—"


def _commit_title(commit: dict) -> str:
    message = ((commit.get("commit") or {}).get("message") or "").splitlines()
    return message[0] if message else ""


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

        lines.append("| SHA | Mensagem | Repositório | Horário |")
        lines.append("|-----|----------|-------------|---------|")

        commits_sorted = sorted(commits, key=_format_committed_at)
        for commit in commits_sorted:
            sha = _short_sha(commit.get("sha", ""))
            message = _commit_title(commit).replace("|", "\\|")
            repository = commit.get("_repository", "—").replace("|", "\\|")
            committed_at = _format_committed_at(commit)
            lines.append(f"| `{sha}` | {message} | {repository} | {committed_at} |")

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

    logger.info("Autenticando no GitHub...")
    user = get_current_user(headers)
    login = user["login"]
    user_name = user.get("name") or login
    logger.info("Usuário: %s (login=%s)", user_name, login)

    today = date.today()
    yesterday = today - timedelta(days=1)
    since_iso, until_iso = _day_bounds_utc(yesterday)

    logger.info("Buscando push events...")
    events = _fetch_user_events(headers, login)
    logger.info("Total de eventos no período: %d", len(events))

    # Inclui os pushes de hoje: commits de ontem podem ter sido enviados hoje.
    targets = collect_push_targets(events, {yesterday, today})

    if not targets:
        logger.info("Nenhum push registrado ontem.")
        return

    logger.info("%d par(es) repositório/branch encontrado(s).", len(targets))

    branches_commits: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for repository, branch in sorted(targets):
        logger.info("Buscando commits: %s @ %s...", repository, branch)
        commits = [
            commit
            for commit in _fetch_branch_commits(
                headers, repository, branch, login, since_iso, until_iso
            )
            if not _is_merge_commit(commit)
        ]
        logger.info("  → %d commit(s) próprio(s) encontrado(s)", len(commits))

        for position, commit in enumerate(commits):
            commit["_repository"] = repository
            commit["_position"] = position
            branches_commits[branch].append(commit)

    branches_commits = keep_origin_branch(branches_commits)

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
        description="Gera resumo de commits do dia anterior no GitHub.",
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
