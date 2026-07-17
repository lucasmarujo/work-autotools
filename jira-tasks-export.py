"""
Exporta a descrição das tasks Jira (Aguardando / Em Desenvolvimento) para .md.
Sem LLM — apenas conversão ADF → Markdown.
"""

import argparse
import importlib.util
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Load hyphen-named local modules
# ---------------------------------------------------------------------------
_here = Path(__file__).parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jira = _load("atlassan_api", _here / "atlassan-api.py")

BOARD_ID = 96
PLANS_DIR = Path(__file__).parent / "plans"

EXPORT_STATUSES = ["Aguardando", "Em Desenvolvimento"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_exportable_tasks(account_id: str, board_id: int) -> list[dict]:
    """Retorna issues cujo status está em EXPORT_STATUSES."""
    all_issues = jira.get_board_issues(account_id, board_id)
    target = [s.lower() for s in EXPORT_STATUSES]
    return [
        i for i in all_issues
        if i["fields"]["status"]["name"].lower() in target
    ]


def _sanitize_filename(name: str) -> str:
    """Remove caracteres inválidos para nomes de arquivo no Windows."""
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name


def _build_md(task: dict, parent_key: str | None = None) -> str:
    """Monta o .md com metadados e a descrição original da task."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    parent_section = ""
    if parent_key:
        parent_section = f"\n> Subtask de [{parent_key}]({jira.JIRA_URL}/browse/{parent_key})\n"

    attachments_section = ""
    if task.get("attachments"):
        attachments_section = "\n---\n\n## Documentos\n\n"
        for a in task["attachments"]:
            fname = _sanitize_filename(a["filename"])
            attachments_section += f"- [{fname}](./{fname})\n"

    comments_section = ""
    if task.get("comments"):
        comments_section = "\n---\n\n## Comentários\n\n"
        for c in task["comments"]:
            comments_section += f"- {c}\n"

    return f"""# [{task['key']}]({task['url']}) — {task['summary']}
{parent_section}
## Descrição

{task['description'] or '_(sem descrição)_'}
{attachments_section}{comments_section}
"""


def _export_task(task: dict, out_dir: Path, parent_key: str | None = None) -> Path:
    """Baixa anexos e grava o .md da task (ou subtask) em out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)

    for a in task.get("attachments", []):
        fname = _sanitize_filename(a["filename"])
        print(f"    📎 Baixando anexo: {fname}")
        jira.download_attachment(a["url"], out_dir / fname)

    out_path = out_dir / f"{task['key']}-description.md"
    out_path.write_text(_build_md(task, parent_key), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(board: int = BOARD_ID):
    """Entry point — chamado pelo main.py ou CLI."""
    PLANS_DIR.mkdir(parents=True, exist_ok=True)

    print("🔐 Autenticando na Jira...")
    account_id, display_name = jira.get_my_account_id()
    print(f"✅ Usuário: {display_name}")

    statuses_label = ", ".join(EXPORT_STATUSES)
    print(f"\n📋 Buscando tasks [{statuses_label}] no board {board}...")
    tasks = _get_exportable_tasks(account_id, board)

    if not tasks:
        print("Nenhuma task encontrada com esses status.")
        return

    print(f"   {len(tasks)} task(s) encontrada(s)\n")
    for t in tasks:
        print(f"   • {t['key']} [{t['fields']['status']['name']}] — {t['fields']['summary']}")
    print()

    for idx, raw_issue in enumerate(tasks, 1):
        key = raw_issue["key"]
        print(f"{'='*62}")
        print(f"  [{idx}/{len(tasks)}] {key} — {raw_issue['fields']['summary']}")
        print(f"{'='*62}")

        print("  📥 Carregando conteúdo completo da task...")
        task = jira.get_task_full_content(key)

        if not task["description"]:
            print("  ⚠️  Task sem descrição — pulando.\n")
            continue

        task_dir = PLANS_DIR / key
        out_path = _export_task(task, task_dir)
        print(f"  ✅ Salvo em: {out_path}\n")

        if task.get("subtasks"):
            print(f"  🔗 {len(task['subtasks'])} subtask(s) encontrada(s)")
            for st in task["subtasks"]:
                print(f"    📥 Carregando subtask {st['key']}...")
                sub_task = jira.get_task_full_content(st["key"])
                sub_path = _export_task(sub_task, task_dir, parent_key=key)
                print(f"    ✅ Salvo em: {sub_path}")
            print()

    print("\n🏁 Exportação concluída.")


def main():
    parser = argparse.ArgumentParser(
        description="Exporta descrição de tasks Jira para .md (sem LLM)"
    )
    parser.add_argument("--board", type=int, default=BOARD_ID)
    args = parser.parse_args()
    run(args.board)


if __name__ == "__main__":
    main()
