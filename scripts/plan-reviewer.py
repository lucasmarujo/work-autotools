import json
import re
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"

SYSTEM_PROMPT = """
Você é um tech lead sênior revisando um planejamento de implementação.

Sua tarefa é comparar a descrição original da task com o planejamento gerado e verificar se o planejamento está completo e alinhado com o objetivo da task.

IMPORTANTE: Sua resposta deve começar com { e terminar com }. Não inclua texto, markdown, tabelas ou explicações fora do JSON.

Retorne SOMENTE um JSON válido neste formato (sem texto antes ou depois):
{
  "approved": true ou false,
  "reasoning": "explicação objetiva em pt-BR de por que foi aprovado ou reprovado",
  "missing": ["item faltante 1", "item faltante 2"],
  "improved_plan": "planejamento completo e corrigido em Markdown (igual ao original + os itens faltantes incorporados de forma coesa)"
}

Regras:
- "approved" deve ser true SOMENTE quando o planejamento cobre completamente o escopo da task, incluindo todos os requisitos funcionais, técnicos e critérios de aceite mencionados na descrição.
- Se aprovado, "missing" deve ser lista vazia [] e "improved_plan" deve ser o mesmo texto do planejamento recebido.
- Se reprovado, "missing" lista os pontos ausentes e "improved_plan" contém o planejamento já corrigido e completo (não apenas o diff, mas o plano inteiro revisado).
- Responda APENAS com JSON. Nenhum texto adicional.
"""


def _extract_json(text: str) -> dict:
    """Extracts the first valid JSON object from a string (handles LLM preamble/postamble)."""
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try to find JSON block inside markdown code fences
    match = re.search(r"```(?:json)?\s*({.*?})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find the first { ... } block
    match = re.search(r"({.*})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract valid JSON from LLM response:\n{text[:500]}")


def review_plan(task_content: dict, plan: str, model: str) -> tuple[bool, str, str]:
    """
    Asks the LLM to review if `plan` is aligned with `task_content`.

    Returns:
        (approved: bool, reasoning: str, plan: str)
        - If approved=True,  plan is unchanged.
        - If approved=False, plan is the improved version from the LLM.
    """
    task_text = f"""Chave: {task_content['key']}
Título: {task_content['summary']}
Tipo: {task_content['type']} | Prioridade: {task_content['priority']}
Link: {task_content['url']}

--- DESCRIÇÃO DA TASK ---
{task_content['description'] or '(sem descrição)'}
"""
    if task_content.get("comments"):
        task_text += "\n--- COMENTÁRIOS ---\n" + "\n\n".join(task_content["comments"])

    prompt = f"""{SYSTEM_PROMPT}

=== TASK ===
{task_text}

=== PLANEJAMENTO PARA REVISÃO ===
{plan}
"""

    response = requests.post(
        OLLAMA_URL,
        json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
    )
    if response.status_code == 404:
        raise Exception(f"Modelo '{model}' não encontrado no Ollama. Execute: ollama pull {model}")
    response.raise_for_status()

    raw = response.json()["response"]

    try:
        result = _extract_json(raw)
    except ValueError as e:
        # If we can't parse JSON, treat as not approved and return original plan
        print(f"    ⚠️  Revisor retornou resposta não-JSON. Tratando como reprovado.")
        print(f"    Resposta bruta: {raw[:300]}")
        return False, "Falha ao parsear resposta do revisor.", plan

    approved        = bool(result.get("approved", False))
    reasoning       = result.get("reasoning", "")
    improved_plan   = result.get("improved_plan", plan)

    return approved, reasoning, improved_plan if not approved else plan
