"""Self-check para jira-tasks-export.py (sem dependências externas, sem rede)."""

import importlib.util
import shutil
import tempfile
from pathlib import Path


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_here = Path(__file__).parent
export = _load("jira_tasks_export", _here / "jira-tasks-export.py")


def test_sanitize_filename():
    assert export._sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"


def test_build_md_subtask_link():
    task = {
        "key": "GL-2", "url": "https://x/browse/GL-2", "summary": "Sub",
        "description": "desc", "comments": [], "attachments": [],
    }
    md = export._build_md(task, parent_key="GL-1")
    assert "Subtask de [GL-1]" in md


def test_build_md_attachments_section():
    task = {
        "key": "GL-1", "url": "https://x/browse/GL-1", "summary": "T",
        "description": "desc", "comments": [],
        "attachments": [{"filename": "doc.pdf", "url": "http://x", "size": 1}],
    }
    md = export._build_md(task)
    assert "[doc.pdf](./doc.pdf)" in md


def test_build_comments_md():
    task = {
        "key": "GL-1", "url": "https://x/browse/GL-1", "summary": "T",
        "comments": ["Ana: primeiro", "Bob: segundo"],
    }
    md = export._build_comments_md(task)
    assert "# Comentários — [GL-1]" in md
    assert "- Ana: primeiro" in md
    assert "- Bob: segundo" in md


def test_export_task_writes_separate_comments_file():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        task = {
            "key": "GL-1", "url": "https://x/browse/GL-1", "summary": "T",
            "description": "desc", "attachments": [],
            "comments": ["Ana: oi"],
        }
        export._export_task(task, tmp_dir)
        desc = (tmp_dir / "GL-1-description.md").read_text(encoding="utf-8")
        comments = (tmp_dir / "GL-1-comments.md")
        assert comments.exists()
        assert "Ana: oi" in comments.read_text(encoding="utf-8")
        assert "Ana: oi" not in desc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_export_task_no_comments_file_when_empty():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        task = {
            "key": "GL-1", "url": "https://x/browse/GL-1", "summary": "T",
            "description": "desc", "attachments": [], "comments": [],
        }
        export._export_task(task, tmp_dir)
        assert not (tmp_dir / "GL-1-comments.md").exists()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_export_task_downloads_attachments_and_writes_md():
    downloaded = []

    def fake_download(url, dest_path):
        downloaded.append((url, dest_path))
        Path(dest_path).write_bytes(b"x")

    original = export.jira.download_attachment
    export.jira.download_attachment = fake_download
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        task = {
            "key": "GL-1", "url": "https://x/browse/GL-1", "summary": "T",
            "description": "desc", "comments": [],
            "attachments": [{"filename": "doc.pdf", "url": "http://x", "size": 1}],
        }
        out_path = export._export_task(task, tmp_dir)
        assert out_path.exists()
        assert (tmp_dir / "doc.pdf").exists()
        assert len(downloaded) == 1
    finally:
        export.jira.download_attachment = original
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_sanitize_filename()
    test_build_md_subtask_link()
    test_build_md_attachments_section()
    test_build_comments_md()
    test_export_task_writes_separate_comments_file()
    test_export_task_no_comments_file_when_empty()
    test_export_task_downloads_attachments_and_writes_md()
    print("OK - all checks passed")
