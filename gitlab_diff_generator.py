"""
Gera um arquivo Markdown com o diff de um Merge Request do GitLab a partir de um link.
"""

import os
import sys
import re
import logging
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = "diff_gitlab"
GITLAB_API_BASE_URL = "https://gitlab.com/api/v4"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_token() -> str:
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        logger.error("Variável de ambiente GITLAB_TOKEN não definida.")
        sys.exit(1)
    return token

def _build_headers(token: str) -> dict[str, str]:
    return {"PRIVATE-TOKEN": token, "Accept": "application/json"}

def parse_gitlab_url(url: str) -> tuple[str, str, str | None, str | None]:
    """
    Parseia a URL do GitLab para extrair o base_url da API, o path do projeto, o IID do MR e/ou o SHA do commit.
    Formatos suportados:
    - MR: https://gitlab.com/grupo/projeto/-/merge_requests/123
    - MR com commit: https://gitlab.com/grupo/projeto/-/merge_requests/123/diffs?commit_id=abc123
    - Commit direto: https://gitlab.com/grupo/projeto/-/commit/abc123
    Retorna: (api_base_url, project_path_encoded, mr_iid, commit_sha)
    """
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/api/v4"
    
    # Extrair project_path
    # O path geralmente é /grupo/projeto/-/algo
    project_path = None
    mr_iid = None
    commit_sha = None

    if "/-/merge_requests/" in parsed.path:
        match = re.search(r'/(.+)/-/merge_requests/(\d+)', parsed.path)
        if match:
            project_path = match.group(1)
            mr_iid = match.group(2)
            
            # Verificar commit_id na query string
            from urllib.parse import parse_qs
            query_params = parse_qs(parsed.query)
            if "commit_id" in query_params:
                commit_sha = query_params["commit_id"][0]
    
    elif "/-/commit/" in parsed.path:
        match = re.search(r'/(.+)/-/commit/([a-f0-9]+)', parsed.path)
        if match:
            project_path = match.group(1)
            commit_sha = match.group(2)

    if not project_path:
        raise ValueError(f"URL do GitLab inválida ou não suportada: {url}")
    
    import urllib.parse
    project_path_encoded = urllib.parse.quote(project_path, safe='')
    
    return base_url, project_path_encoded, mr_iid, commit_sha

def fetch_mr_details(headers: dict, api_base: str, project_id: str, mr_iid: str) -> dict:
    url = f"{api_base}/projects/{project_id}/merge_requests/{mr_iid}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()

def fetch_mr_changes(headers: dict, api_base: str, project_id: str, mr_iid: str) -> dict:
    url = f"{api_base}/projects/{project_id}/merge_requests/{mr_iid}/changes"
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()

def fetch_commit_details(headers: dict, api_base: str, project_id: str, commit_sha: str) -> dict:
    url = f"{api_base}/projects/{project_id}/repository/commits/{commit_sha}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()

def fetch_commit_diff(headers: dict, api_base: str, project_id: str, commit_sha: str) -> list[dict]:
    url = f"{api_base}/projects/{project_id}/repository/commits/{commit_sha}/diff"
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()

def generate_markdown_diff(details: dict, changes: list[dict], is_commit: bool = False) -> str:
    if is_commit:
        title = details.get("title", "Sem título")
        message = details.get("message", "")
        web_url = details.get("web_url", "")
        author = details.get("author_name", "Desconhecido")
        sha = details.get("id", "")
        
        lines = [
            f"# Commit: {title}",
            "",
            f"**Link:** {web_url}",
            f"**Autor:** {author}",
            f"**SHA:** `{sha}`",
            "",
            "## Mensagem",
            "",
            f"```\n{message}\n```",
            "",
            "## Alterações",
            ""
        ]
    else:
        title = details.get("title", "Sem título")
        description = details.get("description", "")
        web_url = details.get("web_url", "")
        author = details.get("author", {}).get("name", "Desconhecido")
        
        lines = [
            f"# MR: {title}",
            "",
            f"**Link:** {web_url}",
            f"**Autor:** {author}",
            f"**Branch:** `{details.get('source_branch')}` → `{details.get('target_branch')}`",
            "",
            "## Descrição",
            "",
            description or "_Sem descrição._",
            "",
            "## Alterações",
            ""
        ]
    
    if not changes:
        lines.append("_Nenhuma alteração encontrada._")
        return "\n".join(lines)
    
    for change in changes:
        old_path = change.get("old_path")
        new_path = change.get("new_path")
        diff = change.get("diff", "")
        
        if old_path == new_path:
            lines.append(f"### Arquivo: `{new_path}`")
        else:
            lines.append(f"### Arquivo: `{old_path}` → `{new_path}`")
        
        if change.get("new_file"):
            lines.append("*(Novo arquivo)*")
        elif change.get("deleted_file"):
            lines.append("*(Arquivo excluído)*")
        elif change.get("renamed_file"):
            lines.append("*(Arquivo renomeado)*")
            
        lines.append("")
        if diff:
            lines.append("```diff")
            lines.append(diff)
            lines.append("```")
        else:
            lines.append("_Arquivo binário ou sem diff textual._")
        lines.append("")
        lines.append("---")
        lines.append("")
        
    return "\n".join(lines)

def run(mr_url: str, output_dir: str | None = None):
    token = _get_token()
    headers = _build_headers(token)
    
    try:
        api_base, project_id, mr_iid, commit_sha = parse_gitlab_url(mr_url)
        
        if commit_sha:
            logger.info(f"Buscando detalhes do commit {commit_sha}...")
            details = fetch_commit_details(headers, api_base, project_id, commit_sha)
            
            logger.info(f"Buscando diff do commit...")
            changes = fetch_commit_diff(headers, api_base, project_id, commit_sha)
            
            md_content = generate_markdown_diff(details, changes, is_commit=True)
            
            # Nome do arquivo sanitizado
            import re
            safe_title = re.sub(r'[<>:"/\\|?*]', '-', details['title'])
            safe_title = re.sub(r'-{2,}', '-', safe_title).strip(' .-')[:100]
            file_name = f"diff-commit-{commit_sha[:8]}-{safe_title}.md"
        else:
            logger.info(f"Buscando detalhes do MR !{mr_iid}...")
            details = fetch_mr_details(headers, api_base, project_id, mr_iid)
            
            logger.info(f"Buscando alterações do MR...")
            changes_data = fetch_mr_changes(headers, api_base, project_id, mr_iid)
            changes = changes_data.get("changes", [])
            
            md_content = generate_markdown_diff(details, changes, is_commit=False)
            
            # Nome do arquivo sanitizado
            import re
            safe_title = re.sub(r'[<>:"/\\|?*]', '-', details['title'])
            safe_title = re.sub(r'-{2,}', '-', safe_title).strip(' .-')[:100]
            file_name = f"diff-MR-{mr_iid}-{safe_title}.md"
        
        out_path = Path(output_dir or DEFAULT_OUTPUT_DIR)
        out_path.mkdir(parents=True, exist_ok=True)
        
        final_file = out_path / file_name
        final_file.write_text(md_content, encoding="utf-8")
        
        logger.info(f"Diff gerado com sucesso: {final_file}")
        return str(final_file)
        
    except Exception as e:
        logger.error(f"Erro ao gerar diff: {e}")
        raise

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python gitlab_diff_generator.py <URL_DO_MR>")
        sys.exit(1)
    run(sys.argv[1])
