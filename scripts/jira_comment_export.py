import requests
from pathlib import Path
import re

JIRA_URL = "https://greenlegis.atlassian.net"


def extract_comment_id(jira_comment_url: str) -> tuple[str, str]:
    """Extrai o issue key e o comment id de uma URL de comentário do Jira."""
    # Exemplo: https://greenlegis.atlassian.net/browse/PDA-509?focusedCommentId=12345
    match = re.search(r"/browse/([A-Z0-9\-]+).*?focusedCommentId=(\d+)", jira_comment_url)
    if not match:
        raise ValueError("URL de comentário do Jira inválida.")
    return match.group(1), match.group(2)


def fetch_jira_comment(issue_key: str, comment_id: str, auth, headers) -> dict:
    url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/comment/{comment_id}"
    r = requests.get(url, auth=auth, headers=headers)
    r.raise_for_status()
    return r.json()


def comment_adf_to_md(comment_adf: dict, adf_to_text_func) -> str:
    return adf_to_text_func(comment_adf)


def export_jira_comment_to_md(jira_comment_url: str, output_dir: Path, auth, headers, adf_to_text_func) -> Path:
    issue_key, comment_id = extract_comment_id(jira_comment_url)
    comment_data = fetch_jira_comment(issue_key, comment_id, auth, headers)
    author = comment_data.get("author", {}).get("displayName", "Desconhecido")
    created = comment_data.get("created", "")[:16].replace("T", " ")
    body_adf = comment_data.get("body", {})
    md_body = comment_adf_to_md(body_adf, adf_to_text_func)
    md = f"""# Comentário Jira

- **Issue:** [{issue_key}]({JIRA_URL}/browse/{issue_key})
- **Autor:** {author}
- **Data:** {created}
- **Comentário ID:** {comment_id}

---

{md_body}
"""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{issue_key}-comment-{comment_id}.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path
