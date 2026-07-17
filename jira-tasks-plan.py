import argparse
import sys
import json
import time
import threading
import importlib.util
from datetime import datetime
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Load hyphen-named local modules (Python can't import them directly)
# ---------------------------------------------------------------------------
_here = Path(__file__).parent

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

jira          = _load("atlassan_api",  _here / "atlassan-api.py")
plan_reviewer = _load("plan_reviewer", _here / "plan-reviewer.py")

OLLAMA_URL = "http://localhost:11434/api/generate"
BOARD_ID   = 96
PLANS_DIR  = Path(__file__).parent / "plans"

PLAN_PROMPT = """
Você é um Tech Lead Sênior responsável por elaborar um planejamento técnico completo e detalhado com base na task descrita abaixo.

Gere um planejamento técnico em pt-BR seguindo obrigatoriamente as regras abaixo:

## Regras obrigatórias

1. Você DEVE considerar 100% dos requisitos descritos na task.
2. Nenhum campo, regra de negócio, validação ou comportamento descrito pode ser omitido.
3. Todos os campos mencionados na task devem ser explicitamente listados no planejamento, incluindo:
   - Nome do campo
   - Tipo de dado
   - Obrigatoriedade (required ou opcional)
   - Valor default (se aplicável)
   - Validações
4. Caso a task envolva API:
   - Descrever endpoints (método HTTP, rota)
   - Payload de request completo (com tipos)
   - Payload de response completo (com tipos)
   - Códigos de status HTTP possíveis
5. Caso envolva banco de dados:
   - Estrutura da tabela
   - Tipos das colunas
   - Índices
   - Chaves primárias e estrangeiras
   - Migrações necessárias
6. Caso envolva frontend:
   - Componentes afetados
   - Estados necessários
   - Estrutura de props
   - Regras de renderização
7. Mapear todas as camadas impactadas:
   - Backend
   - Frontend
   - Banco
   - Integrações externas
   - Filas/Jobs (se houver)
8. Não assumir comportamento implícito. Se algo não estiver claro, declarar a suposição explicitamente.
9. Não resumir requisitos.
10. Não omitir detalhes técnicos.

---

# Estrutura obrigatória da resposta (em Markdown)

## 1. Objetivo e contexto
Descrever claramente o problema que a task resolve e o impacto esperado.

## 2. Levantamento completo dos requisitos da task
Listar todos os requisitos explicitamente extraídos da descrição original.

## 3. Análise técnica detalhada
Descrever:
- Camadas afetadas
- Arquitetura envolvida
- Impacto no domínio
- Dependências

## 4. Modelagem de dados (se aplicável)
Descrever tabelas, campos e tipos no seguinte formato:

Tabela: NomeDaTabela

| Campo | Tipo | Obrigatório | Default | Observações |
|-------|------|------------|----------|-------------|

## 5. Contratos de API (se aplicável)

### Endpoint
- Método:
- Rota:
- Autenticação:

### Request Body
```json
{
  "campo": "tipo"
}
Response Body
{
  "campo": "tipo"
}
Status possíveis

200

400

404

500

6. Regras de negócio

Listar todas as regras explicitamente.

7. Passos de implementação

Passos numerados, detalhados, sem pular etapas.

8. Critérios de aceite

Checklist validável.

9. Riscos e pontos de atenção

Listar riscos técnicos, de performance, regressão, segurança, etc.
"""


# ---------------------------------------------------------------------------
# Ollama helpers
# ---------------------------------------------------------------------------

def check_ollama():
    try:
        requests.get("http://localhost:11434", timeout=3)
    except requests.exceptions.ConnectionError:
        print("\n❌ Ollama não está rodando.")
        print("   Inicie em outro terminal com: ollama serve")
        sys.exit(1)


def generate_plan_streaming(task_content: dict, model: str) -> str:
    """Sends task to Ollama with streaming, prints tokens live, returns full text."""
    task_text = f"""Chave: {task_content['key']}
Título: {task_content['summary']}
Tipo: {task_content['type']} | Prioridade: {task_content['priority']}
Link: {task_content['url']}

--- DESCRIÇÃO ---
{task_content['description'] or '(sem descrição)'}
"""
    if task_content.get("comments"):
        task_text += "\n--- COMENTÁRIOS ---\n" + "\n\n".join(task_content["comments"])

    prompt = f"{PLAN_PROMPT}\n\n=== TASK ===\n{task_text}"

    response = requests.post(
        OLLAMA_URL,
        json={"model": model, "prompt": prompt, "stream": True},
        stream=True,
    )
    if response.status_code == 404:
        raise Exception(f"Modelo '{model}' não encontrado. Execute: ollama pull {model}")
    response.raise_for_status()

    first_token = threading.Event()
    start_time  = time.time()

    def waiting_indicator():
        while not first_token.is_set():
            elapsed = int(time.time() - start_time)
            print(f"\r    ⏳ Gerando planejamento... {elapsed}s", end="", flush=True)
            time.sleep(1)
        print("\r" + " " * 50 + "\r", end="", flush=True)

    t = threading.Thread(target=waiting_indicator, daemon=True)
    t.start()

    tokens = []
    for line in response.iter_lines():
        if line:
            chunk = json.loads(line)
            token = chunk.get("response", "")
            if token and not first_token.is_set():
                first_token.set()
                t.join()
            print(token, end="", flush=True)
            tokens.append(token)
            if chunk.get("done"):
                break

    print()
    return "".join(tokens)


# ---------------------------------------------------------------------------
# .md builder
# ---------------------------------------------------------------------------

def build_md(task: dict, plan: str, approved: bool, iterations: int) -> str:
    now    = datetime.now().strftime("%Y-%m-%d %H:%M")
    status = "✅ Aprovado" if approved else "⚠️ Draft (limite de revisões atingido)"
    return f"""# Planejamento: {task['key']} — {task['summary']}

| Campo       | Valor |
|-------------|-------|
| **Chave**   | [{task['key']}]({task['url']}) |
| **Tipo**    | {task['type']} |
| **Prioridade** | {task['priority']} |
| **Status**  | {task['status']} |
| **Gerado em** | {now} |
| **Revisões** | {iterations} |
| **Resultado** | {status} |

---

{plan}
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(model: str = "llama3.1", max_retries: int = 5, board: int = BOARD_ID):
    """Callable entry point — usable from main.py or CLI."""
    check_ollama()
    PLANS_DIR.mkdir(parents=True, exist_ok=True)

    _run_core(model, max_retries, board)


def _run_core(model: str, max_retries: int, board: int):

    print("🔐 Autenticando na Jira...")
    account_id, display_name = jira.get_my_account_id()
    print(f"✅ Usuário: {display_name}")

    print(f"\n📋 Buscando tasks pendentes no board {board}...")
    pending = jira.get_pending_tasks(account_id, board)

    if not pending:
        print("Nenhuma task pendente encontrada.")
        return

    print(f"   {len(pending)} task(s) pendente(s) encontrada(s)\n")
    for t in pending:
        print(f"   • {t['key']} [{t['fields']['status']['name']}] — {t['fields']['summary']}")
    print()

    for idx, raw_issue in enumerate(pending, 1):
        key = raw_issue["key"]
        print(f"{'='*62}")
        print(f"  [{idx}/{len(pending)}] {key} — {raw_issue['fields']['summary']}")
        print(f"{'='*62}")

        print("  📥 Carregando conteúdo completo da task...")
        task = jira.get_task_full_content(key)

        if not task["description"]:
            print("  ⚠️  Task sem descrição — pulando.\n")
            continue

        # --- Step 1: Generate initial plan ---
        print("  🧠 Gerando planejamento inicial...\n")
        plan = generate_plan_streaming(task, model)

        # --- Step 2: Review loop ---
        approved   = False
        iterations = 0

        for attempt in range(1, max_retries + 1):
            iterations = attempt
            print(f"\n  🔍 Revisão #{attempt}/{max_retries}...")

            approved, reasoning, plan = plan_reviewer.review_plan(task, plan, model)

            print(f"  {'✅ Aprovado' if approved else '❌ Reprovado'}")
            print(f"  Motivo: {reasoning}")

            if approved:
                break

            if attempt < max_retries:
                print(f"  ♻️  Incorporando melhorias e revisando novamente...\n")

        # --- Step 3: Save .md ---
        suffix   = "-plan.md" if approved else "-plan-DRAFT.md"
        out_path = PLANS_DIR / f"{key}{suffix}"
        out_path.write_text(build_md(task, plan, approved, iterations), encoding="utf-8")

        icon = "✅" if approved else "⚠️ "
        print(f"\n  {icon} Salvo em: {out_path}\n")

    print("\n🏁 Concluído.")


def main():
    parser = argparse.ArgumentParser(description="Gera planejamentos de tasks Jira com LLM")
    parser.add_argument("--model",       default="llama3.1")
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--board",       type=int, default=BOARD_ID)
    args = parser.parse_args()
    run(args.model, args.max_retries, args.board)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
