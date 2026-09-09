"""Self-check para github-yesterday-summary.py (sem dependências externas, sem rede)."""

import importlib.util
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_scripts = Path(__file__).parent.parent / "scripts"
summary = _load("github_yesterday_summary", _scripts / "github-yesterday-summary.py")

YESTERDAY = date.today() - timedelta(days=1)


def _push_event(moment, ref="refs/heads/feature/x", repo="acme/api"):
    return {
        "type": "PushEvent",
        "created_at": moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": {"name": repo},
        "payload": {"ref": ref},
    }


def _local(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour, 0)).astimezone()


def _commit(sha, message, hour=9, repository="acme/api", parents=1, position=0):
    return {
        "sha": sha,
        "parents": [{"sha": f"p{i}"} for i in range(parents)],
        "_position": position,
        "commit": {
            "author": {
                "name": "Lucas Marujo",
                "date": _local(YESTERDAY, hour)
                .astimezone(timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "message": message,
        },
        "_repository": repository,
    }


def test_collect_push_targets_agrupa_repositorio_e_branch():
    events = [
        _push_event(_local(YESTERDAY, 9)),
        _push_event(_local(YESTERDAY, 10)),
        _push_event(_local(YESTERDAY, 11), ref="refs/heads/main", repo="acme/web"),
    ]

    targets = summary.collect_push_targets(events, {YESTERDAY})

    assert targets == {("acme/api", "feature/x"), ("acme/web", "main")}


def test_collect_push_targets_ignora_outra_data_tag_e_nao_push():
    events = [
        _push_event(_local(date.today(), 9)),
        _push_event(_local(YESTERDAY, 9), ref="refs/tags/v1.0.0"),
        {"type": "IssuesEvent", "created_at": "2026-01-01T00:00:00Z", "payload": {}},
    ]

    assert summary.collect_push_targets(events, {YESTERDAY}) == set()


def test_collect_push_targets_aceita_multiplas_datas():
    events = [_push_event(_local(date.today(), 9))]

    targets = summary.collect_push_targets(events, {YESTERDAY, date.today()})

    assert targets == {("acme/api", "feature/x")}


def test_day_bounds_utc_cobre_o_dia_local_inteiro():
    since, until = summary._day_bounds_utc(date(2026, 1, 15))

    assert summary._parse_datetime(since).date() == date(2026, 1, 15)
    assert summary._parse_datetime(since).hour == 0
    assert summary._parse_datetime(until).date() == date(2026, 1, 15)
    assert summary._parse_datetime(until).hour == 23


def test_commit_title_usa_apenas_primeira_linha():
    commit = _commit("aaa1111111", "feat: titulo\n\ncorpo detalhado")

    assert summary._commit_title(commit) == "feat: titulo"


def test_commit_title_sem_mensagem():
    assert summary._commit_title({}) == ""


def test_format_committed_at_sem_data():
    assert summary._format_committed_at({}) == "—"


def test_generate_markdown_escapa_pipe_e_ordena_por_horario():
    branches_commits = {
        "feature/x": [
            _commit("bbb2222222", "fix: b", hour=15),
            _commit("aaa1111111", "feat: a | com pipe", hour=9),
        ]
    }

    md = summary.generate_markdown("Lucas Marujo", YESTERDAY, branches_commits)

    assert f"# Resumo de Commits — {YESTERDAY.isoformat()}" in md
    assert "**Total de branches:** 1" in md
    assert "**Total de commits:** 2" in md
    assert "## feature/x" in md
    assert "feat: a \\| com pipe" in md
    assert "| acme/api |" in md
    assert md.index("`aaa11111`") < md.index("`bbb22222`")
    assert "| 09:00:00 |" in md


def test_is_merge_commit_detecta_dois_pais():
    assert summary._is_merge_commit(_commit("mmm3333333", "Merge pull request #1", parents=2))
    assert not summary._is_merge_commit(_commit("aaa1111111", "feat: a"))
    assert not summary._is_merge_commit({})


def test_keep_origin_branch_mantem_commit_na_branch_mais_proxima_do_topo():
    herdado = _commit("aaa1111111", "feat: base", hour=9)
    branches_commits = {
        "feature/base": [dict(herdado, _position=0)],
        "feature/derivada": [
            _commit("bbb2222222", "feat: nova", hour=15, position=0),
            dict(herdado, _position=1),
        ],
    }

    result = summary.keep_origin_branch(branches_commits)

    assert [c["sha"] for c in result["feature/base"]] == ["aaa1111111"]
    assert [c["sha"] for c in result["feature/derivada"]] == ["bbb2222222"]


def test_keep_origin_branch_remove_branch_que_ficou_vazia():
    herdado = _commit("aaa1111111", "feat: base", hour=9)
    branches_commits = {
        "feature/base": [dict(herdado, _position=0)],
        "feature/derivada": [dict(herdado, _position=3)],
    }

    result = summary.keep_origin_branch(branches_commits)

    assert list(result) == ["feature/base"]


def test_keep_origin_branch_sem_commits():
    assert summary.keep_origin_branch({}) == {}


def test_parse_datetime_invalido():
    assert summary._parse_datetime("") is None


def _run_all():
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ok  {name}")
    print("\nTodos os testes passaram.")


if __name__ == "__main__":
    _run_all()
