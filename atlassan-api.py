import os
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

JIRA_URL  = "https://greenlegis.atlassian.net"
EMAIL     = os.environ.get("JIRA_EMAIL")
API_TOKEN = os.environ.get("JIRA_API_TOKEN")

if not EMAIL or not API_TOKEN:
    raise RuntimeError("Variáveis JIRA_EMAIL / JIRA_API_TOKEN não definidas no .env")

PENDING_STATUSES = ["Não Iniciado", "Ajustes", "Aguardando"]

auth    = HTTPBasicAuth(EMAIL, API_TOKEN)
headers = {"Accept": "application/json"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _adf_cell_text(cell_node) -> str:
    """Extrai texto de uma célula de tabela ADF, sem quebras de linha internas."""
    parts = []
    for child in cell_node.get("content", []):
        parts.append(_adf_to_text(child))
    return " ".join(parts).replace("\n", " ").strip()


def _adf_table_to_md(table_node) -> str:
    """Converte um nó 'table' do ADF em tabela Markdown."""
    rows = [r for r in table_node.get("content", []) if r.get("type") == "tableRow"]
    if not rows:
        return ""

    md_rows: list[list[str]] = []
    header_row_count = 0

    for row in rows:
        cells = row.get("content", [])
        is_header = all(c.get("type") == "tableHeader" for c in cells)

        cell_texts = [_adf_cell_text(c) for c in cells]
        md_rows.append(cell_texts)

        if is_header and len(md_rows) == header_row_count + 1:
            header_row_count += 1

    if not md_rows:
        return ""

    # Calcula largura de cada coluna
    num_cols = max(len(r) for r in md_rows)
    # Normaliza todas as linhas para o mesmo número de colunas
    for r in md_rows:
        while len(r) < num_cols:
            r.append("")

    col_widths = [
        max(len(md_rows[i][c]) for i in range(len(md_rows)))
        for c in range(num_cols)
    ]
    col_widths = [max(w, 3) for w in col_widths]  # mínimo 3 para o separador

    def _fmt_row(cells: list[str]) -> str:
        padded = [cells[i].ljust(col_widths[i]) for i in range(num_cols)]
        return "| " + " | ".join(padded) + " |"

    lines: list[str] = []

    # Se não há header explícito, trata a primeira linha como header
    if header_row_count == 0:
        header_row_count = 1

    for idx, row in enumerate(md_rows):
        lines.append(_fmt_row(row))
        if idx == header_row_count - 1:
            sep = "| " + " | ".join("-" * col_widths[i] for i in range(num_cols)) + " |"
            lines.append(sep)

    return "\n".join(lines) + "\n"


def _adf_to_text(node) -> str:
    """Recursively converts an Atlassian Document Format (ADF) node to Markdown text."""
    if node is None:
        return ""

    node_type = node.get("type", "")

    if node_type == "text":
        text = node.get("text", "")
        # Aplica marcações inline (bold, italic, code, etc.)
        for mark in node.get("marks", []):
            mark_type = mark.get("type", "")
            if mark_type == "strong":
                text = f"**{text}**"
            elif mark_type == "em":
                text = f"*{text}*"
            elif mark_type == "code":
                text = f"`{text}`"
            elif mark_type == "strike":
                text = f"~~{text}~~"
            elif mark_type == "link":
                href = mark.get("attrs", {}).get("href", "")
                text = f"[{text}]({href})"
        return text

    if node_type in ("hardBreak", "rule"):
        return "\n"

    children = node.get("content", [])

    if node_type == "table":
        return _adf_table_to_md(node)

    if node_type == "paragraph":
        text = "".join(_adf_to_text(c) for c in children)
        return text + "\n"

    if node_type in ("bulletList", "orderedList"):
        return "".join(_adf_to_text(c) for c in children)

    if node_type == "listItem":
        content = "".join(_adf_to_text(c) for c in children).strip()
        return f"- {content}\n"

    if node_type == "heading":
        level  = node.get("attrs", {}).get("level", 1)
        content = "".join(_adf_to_text(c) for c in children).strip()
        return "#" * level + f" {content}\n"

    if node_type == "codeBlock":
        code = "".join(_adf_to_text(c) for c in children).strip()
        lang = node.get("attrs", {}).get("language", "")
        return f"```{lang}\n{code}\n```\n"

    if node_type == "blockquote":
        content = "".join(_adf_to_text(c) for c in children).strip()
        return f"> {content}\n"

    if node_type == "mention":
        return node.get("attrs", {}).get("text", "@unknown") + " "

    # doc, inlineCard, mediaSingle, etc.
    return "".join(_adf_to_text(c) for c in children)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_my_account_id() -> tuple[str, str]:
    """Returns (account_id, display_name) for the authenticated user."""
    url = f"{JIRA_URL}/rest/api/3/myself"
    r = requests.get(url, auth=auth, headers=headers)
    r.raise_for_status()
    data = r.json()
    return data["accountId"], data["displayName"]


# ---------------------------------------------------------------------------
# Board / Issues
# ---------------------------------------------------------------------------

def get_board_issues(account_id: str, board_id: int) -> list[dict]:
    """Returns ALL issues in board assigned to account_id (paginated)."""
    issues = []
    start  = 0
    limit  = 50

    while True:
        url = f"{JIRA_URL}/rest/agile/1.0/board/{board_id}/issue"
        params = {
            "jql":        f'assignee = "{account_id}"',
            "fields":     "summary,status,issuetype,priority",
            "startAt":    start,
            "maxResults": limit,
        }
        r = requests.get(url, auth=auth, headers=headers, params=params)
        r.raise_for_status()
        data  = r.json()
        batch = data.get("issues", [])
        issues.extend(batch)

        if start + len(batch) >= data.get("total", 0):
            break
        start += limit

    return issues


def get_pending_tasks(account_id: str, board_id: int) -> list[dict]:
    """Returns issues assigned to account_id whose status is in PENDING_STATUSES."""
    all_issues = get_board_issues(account_id, board_id)
    pending_lower = [s.lower() for s in PENDING_STATUSES]
    return [
        i for i in all_issues
        if i["fields"]["status"]["name"].lower() in pending_lower
    ]


def get_task_full_content(issue_key: str) -> dict:
    """
    Fetches full issue data and returns a clean dict with:
      key, summary, status, type, priority, url, description (plain text), comments,
      attachments, subtasks
    """
    url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}"
    params = {"fields": "summary,status,issuetype,priority,description,comment,attachment,subtasks"}
    r = requests.get(url, auth=auth, headers=headers, params=params)
    r.raise_for_status()
    data   = r.json()
    fields = data["fields"]

    # Parse description from ADF → plain text
    description_adf  = fields.get("description") or {}
    description_text = _adf_to_text(description_adf).strip()

    # Parse comments
    comment_list = fields.get("comment", {}).get("comments", [])
    comments = []
    for c in comment_list:
        author = c.get("author", {}).get("displayName", "Unknown")
        body   = _adf_to_text(c.get("body") or {}).strip()
        if body:
            comments.append(f"{author}: {body}")

    # Parse attachments
    attachments = [
        {"filename": a["filename"], "url": a["content"], "size": a.get("size", 0)}
        for a in fields.get("attachment", [])
    ]

    # Parse subtasks (key + summary only — conteúdo completo é buscado sob demanda)
    subtasks = [
        {"key": s["key"], "summary": s["fields"]["summary"]}
        for s in fields.get("subtasks", [])
    ]

    return {
        "key":         issue_key,
        "summary":     fields.get("summary", ""),
        "status":      fields["status"]["name"],
        "type":        fields["issuetype"]["name"],
        "priority":    fields.get("priority", {}).get("name", "—"),
        "url":         f"{JIRA_URL}/browse/{issue_key}",
        "description": description_text,
        "comments":    comments,
        "attachments": attachments,
        "subtasks":    subtasks,
    }


def download_attachment(url: str, dest_path) -> None:
    """Baixa um anexo da Jira (URL do campo 'content') para dest_path."""
    r = requests.get(url, auth=auth, stream=True)
    r.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
