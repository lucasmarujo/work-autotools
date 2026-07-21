import sys
import os
import importlib.util
from pathlib import Path

try:
    from colorama import init, Fore, Back, Style
    init(autoreset=True)
except ImportError:
    print("Instalando colorama...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "colorama", "-q"])
    from colorama import init, Fore, Back, Style
    init(autoreset=True)

# ---------------------------------------------------------------------------
# Load hyphen-named modules
# ---------------------------------------------------------------------------
_here = Path(__file__).parent
_root = _here.parent  # C:\PROJETOS — where the other repos live

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

W  = 58  # menu width

def _clear():
    os.system("cls" if os.name == "nt" else "clear")

def _header():
    print(Fore.CYAN + Style.BRIGHT + "╔" + "═" * W + "╗")
    print(Fore.CYAN + Style.BRIGHT + "║" + " AI AGENT — Central de Automações".center(W) + "║")
    print(Fore.CYAN + Style.BRIGHT + "╠" + "═" * W + "╣")

def _footer():
    print(Fore.CYAN + Style.BRIGHT + "╠" + "═" * W + "╣")
    print(Fore.CYAN + Style.BRIGHT + "║" + Fore.WHITE + "  [0] Sair".ljust(W) + Fore.CYAN + "║")
    print(Fore.CYAN + Style.BRIGHT + "╚" + "═" * W + "╝")

def _menu_item(num: int, title: str, desc: str):
    line1 = f"  [{num}] {title}"
    line2 = f"      {desc}"
    print(Fore.CYAN + Style.BRIGHT + "║" + Fore.YELLOW + Style.BRIGHT + line1.ljust(W) + Fore.CYAN + "║")
    print(Fore.CYAN + Style.BRIGHT + "║" + Fore.WHITE  + Style.DIM    + line2.ljust(W) + Fore.CYAN + "║")
    print(Fore.CYAN + Style.BRIGHT + "║" + " " * W + "║")

def _prompt(msg: str) -> str:
    return input(Fore.GREEN + Style.BRIGHT + f"  ▶  {msg}: " + Style.RESET_ALL).strip()

def _divider(label: str = ""):
    if label:
        pad = (W - len(label) - 2) // 2
        print(Fore.CYAN + "─" * pad + f" {label} " + "─" * (W - pad - len(label) - 2))
    else:
        print(Fore.CYAN + "─" * W)

def _done():
    print()
    input(Fore.GREEN + Style.BRIGHT + "  ✔  Concluído! Pressione ENTER para voltar ao menu..." + Style.RESET_ALL)

def _error(msg: str):
    print(Fore.RED + Style.BRIGHT + f"\n  ✘  {msg}")

def _info(msg: str):
    print(Fore.CYAN + f"  ℹ  {msg}")

# ---------------------------------------------------------------------------
# Option handlers
# ---------------------------------------------------------------------------

def _run_jira_export():
    _clear()
    _divider("EXPORTAR TASKS JIRA PARA .MD")
    print()
    _info("Exporta a descrição das tasks (Aguardando / Em Desenvolvimento)")
    _info("Sem LLM — apenas cópia fiel da descrição para Markdown.")
    print()
    _divider()
    print()

    try:
        exporter = _load("jira_tasks_export", _here / "jira-tasks-export.py")
        exporter.run()
    except Exception as e:
        _error(str(e))

    _done()


def _run_gitlab_mr_reviews():
    _clear()
    _divider("PENDÊNCIAS DE CODE REVIEW — GITLAB")
    print()
    _info("Coleta comentários pendentes dos seus MRs abertos no GitLab.")
    _info("Gera um arquivo .md por MR na pasta reviews-mr/.")
    _info("Requer variável de ambiente GITLAB_TOKEN.")
    print()
    output_dir = _prompt("Pasta de saída [Enter = reviews-mr]") or None
    print()
    _divider()
    print()

    try:
        gl_reviews = _load("export_gitlab_mr_reviews", _here / "export-gitlab-mr-reviews.py")
        gl_reviews.run(output_dir=output_dir)
    except Exception as e:
        _error(str(e))

    _done()


def _run_jira_comment_export():
    _clear()
    _divider("EXPORTAR COMENTÁRIO JIRA → .MD")
    print()
    _info("Exporta um comentário específico do Jira para Markdown, mantendo tabelas e formatação.")
    print()
    jira_url = _prompt("Cole o link do comentário do Jira (com focusedCommentId)")
    output_dir = _prompt("Pasta de saída [Enter = plans]") or "plans"
    print()
    _divider()
    print()

    try:
        jira_api = _load("atlassan_api", _here / "atlassan-api.py")
        exporter = _load("jira_comment_export", _here / "jira_comment_export.py")
        out_path = exporter.export_jira_comment_to_md(
            jira_comment_url=jira_url,
            output_dir=Path(output_dir),
            auth=jira_api.auth,
            headers=jira_api.headers,
            adf_to_text_func=jira_api.adf_to_md
        )
        _info(f"Comentário exportado para: {out_path}")
    except Exception as e:
        _error(str(e))

    _done()


def _run_bbc():
    _clear()
    _divider("MODO teste automatizado")
    print()
    _info("Acha os projetos rodando no momento.")
    _info("Roda testes dependendo da stack em todos projetos abertos")
    _info("Pressione Ctrl+C para encerrar.")
    print()
    _divider()
    print()

    try:
        bbc = _load("bbc", _here / "bbc.py")
        bbc.run()
    except Exception as e:
        _error(str(e))

    _done()


def _run_gitlab_yesterday_summary():
    _clear()
    _divider("RESUMO DE COMMITS DE ONTEM — GITLAB")
    print()
    _info("Busca todos os commits feitos ontem pelo seu usuário no GitLab.")
    _info("Gera um arquivo .md por data na pasta commits-summary/.")
    _info("Requer variável de ambiente GITLAB_TOKEN.")
    print()
    output_dir = _prompt("Pasta de saída [Enter = commits-summary]") or None
    print()
    _divider()
    print()

    try:
        summary = _load("gitlab_yesterday_summary", _here / "gitlab-yesterday-summary.py")
        summary.run(output_dir=output_dir)
    except Exception as e:
        _error(str(e))

    _done()


# ---------------------------------------------------------------------------
# Menu loop
# ---------------------------------------------------------------------------

OPTIONS = {
    "1": (
        "Exportar Tasks Jira → .md",
        "Copia descrição das tasks (Aguardando/Em Desenvolvimento)",
        _run_jira_export,
    ),
    "2": (
        "Pendências de Review GitLab",
        "Exporta comentários pendentes dos MRs abertos p/ .md",
        _run_gitlab_mr_reviews,
    ),
    "3": (
        "Exportar Comentário Jira → .md",
        "Exporta um comentário específico do Jira para Markdown",
        _run_jira_comment_export,
    ),
    "4": (
        "modo teste automatizado",
        "Roda testes dependendo da stack em todos projetos abertos (Ctrl+C p/ sair)",
        _run_bbc,
    ),
    "5": (
        "Resumo de Commits de Ontem — GitLab",
        "Gera .md com seus commits de ontem agrupados por projeto",
        _run_gitlab_yesterday_summary,
    ),
}


def main():
    while True:
        _clear()
        _header()
        print(Fore.CYAN + Style.BRIGHT + "║" + " " * W + "║")
        for key, (title, desc, _) in OPTIONS.items():
            _menu_item(int(key), title, desc)
        _footer()
        print()

        choice = _prompt("Escolha uma opção")

        if choice == "0":
            _clear()
            print(Fore.CYAN + Style.BRIGHT + "\n  Até logo!\n")
            break

        if choice not in OPTIONS:
            _error(f"Opção '{choice}' inválida.")
            import time; time.sleep(1.2)
            continue

        _, _, handler = OPTIONS[choice]
        _clear()
        handler()


if __name__ == "__main__":
    main()
