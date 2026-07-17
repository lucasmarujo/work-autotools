"""
Faz code review automatizado de Merge Requests abertos no GitLab
onde o usuário autenticado é reviewer.

Fluxo:
    1. Identifica MRs abertos onde o usuário é reviewer
    2. Coleta o diff de cada MR
    3. Envia para o Ollama (LLM local) para análise
    4. Gera relatório com pontos de atenção, segurança, otimização, etc.

Variável de ambiente necessária:
    GITLAB_TOKEN  —  Personal Access Token do GitLab
"""

import os
import sys
import json
import time
import threading
import logging
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

GITLAB_API_BASE_URL = "https://gitlab.com/api/v4"
OLLAMA_URL = "http://localhost:11434/api/chat"
PER_PAGE = 100
OUTPUT_DIR = "code-review"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

REVIEW_PROMPT = """
<ROLE>
Você é um engenheiro de software sênior especializado em segurança e performance realizando um code review técnico e rigoroso.
Você DEVE responder inteiramente em Português Brasileiro (pt-BR).
</ROLE>

<CONTEXT>
Merge Request para review:
- Título: {mr_title}
- Descrição: {mr_description}
- Branch: {source_branch} → {target_branch}
</CONTEXT>

<TASK>
Analise o diff do Merge Request fornecido abaixo de forma RIGOROSA e TÉCNICA.

Você DEVE:
- Referenciar ARQUIVOS e TRECHOS DE CÓDIGO específicos do diff em cada ponto levantado (ex: "Em `arquivo.cs`, na linha `+   var x = ...`")
- Explicar EXATAMENTE qual é o problema e qual é o risco concreto
- Dar exemplos de como o problema pode ser explorado ou causar falhas, quando aplicável
- Sugerir a correção específica com código quando possível

Você NÃO DEVE:
- Dar conselhos genéricos como "adicione documentação" ou "refatore o código"
- Repetir o que o diff faz sem apontar problemas concretos
- Inventar problemas que não existem no diff — se uma seção não tem problemas, escreva "Nenhum problema encontrado nesta categoria"
- Assumir código que não está visível no diff
</TASK>

<ANALYSIS_CHECKLIST>
Para CADA arquivo alterado no diff, verifique sistematicamente:

1. SEGURANÇA
   - SQL/NoSQL injection (queries montadas com concatenação de strings?)
   - XSS (dados do usuário renderizados sem sanitização?)
   - CSRF, RCE, SSRF
   - Deserialização insegura de dados externos
   - Exposição de segredos, tokens, connection strings
   - Endpoints sem autenticação ou autorização
   - Dados sensíveis em logs

2. QUERIES E BANCO DE DADOS
   - Queries N+1 (loop fazendo query individual por item?)
   - Queries sem índice ou filtros adequados
   - Falta de paginação em consultas que podem retornar muitos registros
   - Transações mal gerenciadas
   - Connection leaks

3. PERFORMANCE E OTIMIZAÇÃO
   - Loops ineficientes (O(n²) onde O(n) é possível)
   - Alocações excessivas de memória (listas desnecessárias, ToList() prematuro)
   - Chamadas síncronas que deveriam ser assíncronas
   - Falta de cache onde aplicável
   - Operações custosas dentro de loops

4. TRATAMENTO DE ERROS
   - Exceções engolidas (catch vazio ou genérico)
   - Falta de tratamento para null/empty em retornos de APIs ou banco
   - Falta de validação de inputs nos endpoints/métodos públicos

5. CONCORRÊNCIA
   - Race conditions em recursos compartilhados
   - Deadlocks potenciais
   - Operações não thread-safe

6. CÓDIGO E ARQUITETURA
   - Violações de SOLID visíveis no diff
   - Acoplamento excessivo entre camadas
   - Código duplicado introduzido neste MR
   - Complexidade ciclomática alta em métodos novos
</ANALYSIS_CHECKLIST>

<OUTPUT_FORMAT>
Você DEVE seguir EXATAMENTE este formato. NÃO altere os títulos das seções.
Cada item DEVE referenciar arquivo e trecho de código específico do diff.

## 📋 Resumo das Alterações
(2-4 frases descrevendo objetivamente o que foi implementado/alterado neste MR)

## 🚨 Problemas Críticos
(Problemas que DEVEM ser corrigidos antes do merge — segurança, bugs, perda de dados)
(Se não houver: "Nenhum problema crítico encontrado.")

- **[arquivo.cs]** `trecho do código` → descrição do problema e risco concreto

## ⚠️ Pontos de Atenção
(Riscos, code smells, débitos técnicos que merecem discussão)

- **[arquivo.cs]** `trecho do código` → descrição e recomendação

## 🔒 Segurança
(Vulnerabilidades, falhas de auth, exposição de dados)

- **[arquivo.cs]** `trecho do código` → vulnerabilidade específica e como pode ser explorada

## ⚡ Performance e Otimização
(Queries problemáticas, loops ineficientes, problemas de memória)

- **[arquivo.cs]** `trecho do código` → problema de performance e sugestão de correção

## 🛠 Melhorias Sugeridas
(Refatorações concretas com justificativa técnica)

- **[arquivo.cs]** `trecho do código` → melhoria sugerida com exemplo de código

## 🧪 Testes Sugeridos
(Cenários de teste específicos para o código alterado neste MR)

- Cenário: (descrição) → valida que (comportamento esperado)
</OUTPUT_FORMAT>
"""


def _get_token() -> str:
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        logger.error("Variável de ambiente GITLAB_TOKEN não definida.")
        sys.exit(1)
    return token


def _build_headers(token: str) -> dict[str, str]:
    return {"PRIVATE-TOKEN": token, "Accept": "application/json"}


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


def get_current_user(headers: dict) -> dict:
    url = f"{GITLAB_API_BASE_URL}/user"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_mrs_as_reviewer(headers: dict, reviewer_id: int) -> list[dict]:
    url = f"{GITLAB_API_BASE_URL}/merge_requests"
    params = {
        "state": "opened",
        "reviewer_id": reviewer_id,
        "scope": "all",
    }
    merge_requests = _paginated_get(url, headers, params)
    logger.info("MRs abertos como reviewer: %d", len(merge_requests))
    return merge_requests


def fetch_mr_changes(headers: dict, project_id: int, mr_iid: int) -> dict:
    url = (
        f"{GITLAB_API_BASE_URL}/projects/{project_id}"
        f"/merge_requests/{mr_iid}/changes"
    )
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()


def build_diff_from_changes(changes_data: dict) -> str:
    changes = changes_data.get("changes", [])
    if not changes:
        return ""

    diff_parts = []
    for change in changes:
        file_path = change.get("new_path", change.get("old_path", "unknown"))
        diff_content = change.get("diff", "")
        if diff_content:
            diff_parts.append(f"--- a/{file_path}\n+++ b/{file_path}\n{diff_content}")

    return "\n".join(diff_parts)


def check_ollama_running():
    try:
        requests.get("http://localhost:11434", timeout=3)
    except requests.exceptions.ConnectionError:
        logger.error("Ollama não está rodando. Inicie com: ollama serve")
        sys.exit(1)


def call_ollama_streaming(model: str, system_prompt: str, user_content: str) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "stream": True,
        },
        stream=True,
    )
    if response.status_code == 404:
        raise Exception(
            f"Modelo '{model}' não encontrado no Ollama. "
            f"Execute: ollama pull {model}"
        )
    response.raise_for_status()

    first_token_received = threading.Event()
    start_time = time.time()

    def waiting_indicator():
        while not first_token_received.is_set():
            elapsed = int(time.time() - start_time)
            print(f"\r⏳ Processando... {elapsed}s", end="", flush=True)
            time.sleep(1)
        print("\r" + " " * 40 + "\r", end="", flush=True)

    timer_thread = threading.Thread(target=waiting_indicator, daemon=True)
    timer_thread.start()

    full_response = []
    for line in response.iter_lines():
        if line:
            chunk = json.loads(line)
            token = chunk.get("message", {}).get("content", "")
            if token and not first_token_received.is_set():
                first_token_received.set()
                timer_thread.join()
            print(token, end="", flush=True)
            full_response.append(token)
            if chunk.get("done"):
                break

    print()
    return "".join(full_response)


def _sanitize_filename(title: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]', '-', title)
    sanitized = re.sub(r'-{2,}', '-', sanitized).strip(' .-')
    return sanitized[:120]


def _prompt_mr_selection(merge_requests: list[dict]) -> list[dict]:
    print()
    print("  MRs abertos onde você é reviewer:")
    print()
    print("  [0] Todos")
    for i, mr in enumerate(merge_requests, start=1):
        author = mr.get("author", {}).get("name", "desconhecido")
        print(f"  [{i}] !{mr['iid']} — {mr['title']}")
        print(f"       Autor: {author} | {mr['source_branch']} → {mr['target_branch']}")
        print()

    raw = input("  Selecione os MRs (ex: 1  ou  1,2  ou  0 para todos): ").strip()

    if not raw or raw == "0":
        return merge_requests

    selected: list[dict] = []
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        idx = int(part)
        if idx == 0:
            return merge_requests
        if 1 <= idx <= len(merge_requests):
            selected.append(merge_requests[idx - 1])

    return selected if selected else merge_requests


def run(model: str = "qwen3", max_diff: int = 80000, output_dir: str | None = None):
    token = _get_token()
    headers = _build_headers(token)

    check_ollama_running()

    logger.info("Autenticando no GitLab...")
    user = get_current_user(headers)
    username = user["username"]
    user_id = user["id"]
    logger.info("Usuário: %s (id=%d)", username, user_id)

    logger.info("Buscando MRs abertos onde você é reviewer...")
    merge_requests = fetch_mrs_as_reviewer(headers, user_id)

    if not merge_requests:
        logger.info("Nenhum MR aberto encontrado onde você é reviewer.")
        return

    selected_mrs = _prompt_mr_selection(merge_requests)

    if not selected_mrs:
        logger.info("Nenhum MR selecionado.")
        return

    output_path = Path(output_dir or OUTPUT_DIR)
    output_path.mkdir(parents=True, exist_ok=True)

    for mr in selected_mrs:
        title = mr["title"]
        mr_iid = mr["iid"]
        project_id = mr["project_id"]
        source_branch = mr.get("source_branch", "")
        target_branch = mr.get("target_branch", "")
        description = mr.get("description", "") or ""
        author_name = mr.get("author", {}).get("name", "desconhecido")

        print()
        print("=" * 60)
        logger.info("Analisando MR !%d — %s (por %s)", mr_iid, title, author_name)
        print("=" * 60)

        logger.info("Buscando alterações do MR...")
        changes_data = fetch_mr_changes(headers, project_id, mr_iid)
        diff = build_diff_from_changes(changes_data)

        if not diff:
            logger.info("MR !%d sem alterações (diff vazio). Pulando.", mr_iid)
            continue

        changed_files = [
            c.get("new_path", c.get("old_path", "unknown"))
            for c in changes_data.get("changes", [])
        ]
        logger.info("  %d arquivo(s) alterado(s)", len(changed_files))

        diff_chars = len(diff)
        if max_diff and diff_chars > max_diff:
            diff = diff[:max_diff]
            logger.warning("  Diff truncado: %s → %s chars", f"{diff_chars:,}", f"{max_diff:,}")
        else:
            logger.info("  Tamanho do diff: %s chars", f"{diff_chars:,}")

        system_prompt = REVIEW_PROMPT.format(
            mr_title=title,
            mr_description=description[:2000] if description else "(sem descrição)",
            source_branch=source_branch,
            target_branch=target_branch,
        )

        user_content = f"""Analise o diff abaixo e retorne o code review ESTRITAMENTE no formato especificado nas instruções.

Arquivos alterados:
{chr(10).join(changed_files)}

Diff completo do MR:
```
{diff}
```"""

        print()
        print(f"🚀 Enviando para Ollama ({model})...")
        print()
        print("===== CODE REVIEW — IA =====")
        print()

        review_result = call_ollama_streaming(model, system_prompt, user_content)

        safe_name = _sanitize_filename(title)
        file_name = f"review-MR-{mr_iid}-{safe_name}.md"
        file_path = output_path / file_name

        md_content = [
            f"# Code Review — MR !{mr_iid}",
            "",
            f"**Título:** {title}",
            f"**Autor:** {author_name}",
            f"**Branch:** {source_branch} → {target_branch}",
            f"**Link:** {mr.get('web_url', '')}",
            f"**Modelo:** {model}",
            "",
            "---",
            "",
            review_result,
        ]

        file_path.write_text("\n".join(md_content), encoding="utf-8")
        logger.info("Review salvo em: %s", file_path)

    logger.info("Concluído! Reviews salvos em %s", output_path.resolve())


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Code review automatizado de MRs do GitLab usando Ollama.",
    )
    parser.add_argument("--model", default="qwen3", help="Modelo do Ollama (padrão: qwen3)")
    parser.add_argument("--max-diff", type=int, default=80000, help="Limite de chars do diff (padrão: 80000)")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help=f"Pasta de saída (padrão: {OUTPUT_DIR})")
    args = parser.parse_args()
    run(model=args.model, max_diff=args.max_diff, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
