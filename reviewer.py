import subprocess
import argparse
import sys
import json
import time
import threading
import requests
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"


def run_git_command(repo_path, args):
    result = subprocess.run(
        ["git"] + args,
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise Exception(result.stderr)
    return result.stdout.strip()


def get_diff(repo_path, base_branch):
    return run_git_command(
        repo_path,
        ["diff", "--unified=3", f"{base_branch}...HEAD"]
    )


def get_changed_files(repo_path, base_branch):
    output = run_git_command(
        repo_path,
        ["diff", "--name-only", f"{base_branch}...HEAD"]
    )
    return output.splitlines()


def check_ollama_running():
    try:
        r = requests.get("http://localhost:11434", timeout=3)
        return True
    except requests.exceptions.ConnectionError:
        print("\n❌ Ollama is not running.")
        print("   Start it in a separate terminal with: ollama serve")
        print("   Then re-run this script.\n")
        sys.exit(1)


def load_prompt():
    script_dir = Path(__file__).parent
    prompt_path = script_dir / "prompt.txt"
    return prompt_path.read_text(encoding="utf-8")


def call_ollama(model, prompt):
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "prompt": prompt,
            "stream": True
        },
        stream=True
    )
    if response.status_code == 404:
        raise Exception(
            f"Model '{model}' not found in Ollama. "
            f"Run 'ollama pull {model}' or use --model with an available model. "
            f"Available models: run 'ollama list'"
        )
    response.raise_for_status()

    # Show elapsed time while waiting for the first token
    first_token_received = threading.Event()
    start_time = time.time()

    def waiting_indicator():
        while not first_token_received.is_set():
            elapsed = int(time.time() - start_time)
            print(f"\r⏳ Processing input... {elapsed}s", end="", flush=True)
            time.sleep(1)
        print("\r" + " " * 40 + "\r", end="", flush=True)  # clear the line

    timer_thread = threading.Thread(target=waiting_indicator, daemon=True)
    timer_thread.start()

    full_response = []
    for line in response.iter_lines():
        if line:
            chunk = json.loads(line)
            token = chunk.get("response", "")
            if token and not first_token_received.is_set():
                first_token_received.set()
                timer_thread.join()
            print(token, end="", flush=True)
            full_response.append(token)
            if chunk.get("done"):
                break

    print()  # newline after streaming ends
    return "".join(full_response)


def run(repo_path: str, base_branch: str, model: str = "qwen3.5", max_diff: int = 50000):
    """Callable entry point — usable from main.py or CLI."""
    check_ollama_running()

    print("📄 Getting changed files...")
    changed_files = get_changed_files(repo_path, base_branch)

    if not changed_files:
        print("No changes detected.")
        return

    print(f"   {len(changed_files)} file(s) changed")

    print("🧾 Getting git diff...")
    diff = get_diff(repo_path, base_branch)

    diff_chars = len(diff)
    if max_diff and diff_chars > max_diff:
        diff = diff[:max_diff]
        print(f"   ⚠️  Diff truncated: {diff_chars:,} → {max_diff:,} chars")
    else:
        print(f"   Diff size: {diff_chars:,} chars")

    print("🧠 Loading prompt...")
    base_prompt = load_prompt()

    full_prompt = f"""
Changed files:
{chr(10).join(changed_files)}

Git diff:
{diff}

{base_prompt}
"""

    print(f"🚀 Sending to Ollama ({model})...")
    print("   (first token may take a while depending on diff size)\n")
    print("===== AI SECURITY REVIEW =====\n")
    call_ollama(model, full_prompt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo",     required=True)
    parser.add_argument("--base",     default="main")
    parser.add_argument("--model",    default="qwen3.5")
    parser.add_argument("--max-diff", type=int, default=50000)
    args = parser.parse_args()
    run(args.repo, args.base, args.model, args.max_diff)


if __name__ == "__main__":
    main()