"""Tests for devtools/gha-check-run.py.

Invoked as a subprocess rather than imported (its filename has a hyphen, so
it isn't an importable module) - this exercises the actual CLI contract that
Taskfile.yml's CHECK_RUN_PREFIX and .github/workflows/validate.yml's
`finalize` step depend on. GHA_CHECK_DRY_RUN stands in for the GitHub API:
`run`/`finalize` log what they would have sent instead of making real HTTP
calls, so these tests never hit the network.
"""

import contextlib
import http.server
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).parent.parent / "devtools" / "gha-check-run.py"


def _run(
    args: list[str], *, env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _base_env() -> dict[str, str]:
    # Deliberately excludes any ambient GITHUB_* vars so these tests don't
    # depend on whether they happen to run inside GitHub Actions themselves.
    return {"PATH": os.environ.get("PATH", "")}


def _reporting_env(tmp_path: Path) -> dict[str, str]:
    env = _base_env()
    env.update(
        GITHUB_ACTIONS="true",
        GITHUB_TOKEN="fake-token",  # ruff: ignore[hardcoded-password-func-arg] - not a real credential, see module docstring
        GITHUB_REPOSITORY="example/repo",
        GHA_CHECK_HEAD_SHA="deadbeef",
        GHA_CHECK_DRY_RUN="1",
        GHA_CHECK_STATE=str(tmp_path / "state.jsonl"),
    )
    return env


def _read_state(tmp_path: Path) -> list[dict[str, object]]:
    lines = (tmp_path / "state.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# run: passthrough (reporting disabled)
# ---------------------------------------------------------------------------


def test_run_passthrough_preserves_exit_code_and_output(tmp_path: Path) -> None:
    result = _run(
        [
            "run",
            "--data",
            '{"task": "demo"}',
            "--",
            sys.executable,
            "-c",
            "import sys; print('hi'); sys.exit(3)",
        ],
        env=_base_env(),
        cwd=tmp_path,
    )
    assert result.returncode == 3
    assert "hi" in result.stdout


def test_run_passthrough_when_no_token(tmp_path: Path) -> None:
    env = _base_env()
    env["GITHUB_ACTIONS"] = "true"  # actions=true but no token -> still passthrough
    result = _run(
        ["run", "--data", '{"task": "demo"}', "--", sys.executable, "-c", "pass"],
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert not (tmp_path / ".cache" / "gha-checks" / "state.jsonl").exists()


def test_run_missing_command_errors(tmp_path: Path) -> None:
    result = _run(
        ["run", "--data", '{"task": "demo"}', "--"], env=_base_env(), cwd=tmp_path
    )
    assert result.returncode == 2
    assert "missing command" in result.stderr


# ---------------------------------------------------------------------------
# run: reporting enabled (dry-run - see module docstring)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("exit_code", "conclusion"), [(0, "success"), (7, "failure")])
def test_run_records_conclusion_and_propagates_exit_code(
    tmp_path: Path, exit_code: int, conclusion: str
) -> None:
    env = _reporting_env(tmp_path)
    result = _run(
        [
            "run",
            "--data",
            '{"task": "mypy"}',
            "--",
            sys.executable,
            "-c",
            f"import sys; sys.exit({exit_code})",
        ],
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode == exit_code
    records = _read_state(tmp_path)
    assert [r["event"] for r in records] == ["start", "complete"]
    assert records[0]["name"] == "mypy"
    assert records[1]["conclusion"] == conclusion
    assert records[1]["exit_code"] == exit_code


def test_run_dry_run_env_var_is_case_insensitive(tmp_path: Path) -> None:
    # A common footgun: GHA_CHECK_DRY_RUN=False (capitalized, as e.g. Python's
    # str(False) would produce) must still disable dry-run, not enable it by
    # falling through to the "anything not in ('', '0', 'false') enables it"
    # branch. GITHUB_API_URL points at an unreachable local port instead of
    # the real API - dry-run being off is evidenced by a real HTTP attempt
    # failing fast, not by hitting the network (see module docstring).
    env = _reporting_env(tmp_path)
    env["GHA_CHECK_DRY_RUN"] = "False"
    env["GITHUB_API_URL"] = "http://127.0.0.1:1"
    result = _run(
        [
            "run",
            "--data",
            '{"task": "demo"}',
            "--",
            sys.executable,
            "-c",
            "print('hi')",
        ],
        env=env,
        cwd=tmp_path,
    )
    assert "DRY RUN" not in result.stderr
    assert result.returncode == 0
    assert "hi" in result.stdout


def test_run_uses_first_command_word_as_name_when_no_data(tmp_path: Path) -> None:
    env = _reporting_env(tmp_path)
    _run(["run", "--", "true"], env=env, cwd=tmp_path)
    records = _read_state(tmp_path)
    assert records[0]["name"] == "true"


def test_run_streams_command_output(tmp_path: Path) -> None:
    env = _reporting_env(tmp_path)
    result = _run(
        [
            "run",
            "--data",
            '{"task": "demo"}',
            "--",
            sys.executable,
            "-c",
            "print('hi')",
        ],
        env=env,
        cwd=tmp_path,
    )
    assert "hi" in result.stdout


def _sarif_with_one_result() -> str:
    return json.dumps(
        {
            "runs": [
                {
                    "results": [
                        {
                            "ruleId": "demo/some-rule",
                            "level": "warning",
                            "message": {"text": "oops"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "a.yml"},
                                        "region": {"startLine": 3},
                                    }
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    )


def test_run_output_is_sarif_summarizes_and_uploads(tmp_path: Path) -> None:
    env = _reporting_env(tmp_path)
    sarif = _sarif_with_one_result()
    result = _run(
        [
            "run",
            "--data",
            '{"task": "zizmor"}',
            "--output-is-sarif",
            "--",
            sys.executable,
            "-c",
            f"print({sarif!r})",
        ],
        env=env,
        cwd=tmp_path,
    )
    # Some SARIF-capable tools (zizmor among them) exit 0 unconditionally in
    # SARIF mode, so a non-empty result set must fail the check run - and the
    # process's own exit code - even though the wrapped command exited 0.
    assert result.returncode == 1
    records = _read_state(tmp_path)
    assert [r["event"] for r in records] == ["start", "complete"]
    assert records[1]["conclusion"] == "failure"
    assert records[1]["exit_code"] == 1
    # The check-run text is a summary derived from the SARIF, not raw stdout.
    assert "demo/some-rule" in result.stderr
    assert "a.yml:3" in result.stderr
    # Uploaded to the Code Scanning API, with the sarif field redacted rather
    # than the full base64 blob dumped into the (dry-run) log.
    assert "/code-scanning/sarifs" in result.stderr
    assert re.search(r"<\d+ chars>", result.stderr)
    # checkout_uri lets GitHub map a tool's absolute file:// SARIF paths
    # (ruff does this) back to repo-relative paths in the PR diff.
    assert f"'checkout_uri': '{tmp_path.as_uri()}'" in result.stderr


def test_run_output_is_sarif_rewrites_pull_merge_ref_to_head(tmp_path: Path) -> None:
    # GITHUB_REF is the ephemeral merge ref on pull_request events, but
    # GHA_CHECK_HEAD_SHA is the PR head sha - the Code Scanning API 422s
    # unless ref and commit_sha refer to the same commit.
    env = _reporting_env(tmp_path)
    env["GITHUB_REF"] = "refs/pull/42/merge"
    sarif = json.dumps({"runs": [{"results": []}]})
    result = _run(
        [
            "run",
            "--data",
            '{"task": "zizmor"}',
            "--output-is-sarif",
            "--",
            sys.executable,
            "-c",
            f"print({sarif!r})",
        ],
        env=env,
        cwd=tmp_path,
    )
    assert "'ref': 'refs/pull/42/head'" in result.stderr


def test_run_output_is_sarif_with_no_results_stays_successful(tmp_path: Path) -> None:
    env = _reporting_env(tmp_path)
    sarif = json.dumps({"runs": [{"results": []}]})
    result = _run(
        [
            "run",
            "--data",
            '{"task": "zizmor"}',
            "--output-is-sarif",
            "--",
            sys.executable,
            "-c",
            f"print({sarif!r})",
        ],
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode == 0
    records = _read_state(tmp_path)
    assert records[1]["conclusion"] == "success"
    assert records[1]["exit_code"] == 0
    assert "No SARIF results." in result.stderr


def test_run_output_is_sarif_with_invalid_json_still_completes(tmp_path: Path) -> None:
    env = _reporting_env(tmp_path)
    result = _run(
        [
            "run",
            "--data",
            '{"task": "zizmor"}',
            "--output-is-sarif",
            "--",
            sys.executable,
            "-c",
            "import sys; print('not valid json'); sys.exit(1)",
        ],
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode == 1
    records = _read_state(tmp_path)
    assert [r["event"] for r in records] == ["start", "complete"]
    assert records[1]["conclusion"] == "failure"
    assert records[1]["exit_code"] == 1
    # Falls back to the raw output as check-run text when SARIF parsing fails.
    assert "not valid json" in result.stderr


def test_two_runs_get_distinct_check_identities(tmp_path: Path) -> None:
    # Regression test: check_run_id is None for every task in dry-run mode,
    # so finalize must key start/complete records by something else
    # (run_key) or every task collapses onto the last one's name.
    env = _reporting_env(tmp_path)
    _run(
        ["run", "--data", '{"task": "a"}', "--", sys.executable, "-c", "pass"],
        env=env,
        cwd=tmp_path,
    )
    _run(
        ["run", "--data", '{"task": "b"}', "--", sys.executable, "-c", "pass"],
        env=env,
        cwd=tmp_path,
    )
    records = _read_state(tmp_path)
    starts = [r for r in records if r["event"] == "start"]
    assert {r["name"] for r in starts} == {"a", "b"}
    assert len({r["run_key"] for r in starts}) == 2


def test_append_state_survives_concurrent_writers(tmp_path: Path) -> None:
    # validate:static tasks run concurrently under VALIDATE_PARALLEL, so
    # multiple gha-check-run.py processes append to the same state file at
    # once - every line must still parse as valid JSON afterward (no
    # interleaved/corrupted writes).
    env = _reporting_env(tmp_path)
    task_count = 16
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                str(_SCRIPT),
                "run",
                "--data",
                f'{{"task": "t{i}"}}',
                "--",
                sys.executable,
                "-c",
                "pass",
            ],
            env=env,
            cwd=tmp_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for i in range(task_count)
    ]
    for proc in procs:
        assert proc.wait() == 0
    records = _read_state(tmp_path)  # raises if any line fails to parse
    assert len(records) == 2 * task_count
    assert {r["event"] for r in records} == {"start", "complete"}


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


def test_finalize_with_no_state_file_is_a_noop(tmp_path: Path) -> None:
    result = _run(["finalize"], env=_reporting_env(tmp_path), cwd=tmp_path)
    assert result.returncode == 0


def test_finalize_reports_each_completed_run(tmp_path: Path) -> None:
    env = _reporting_env(tmp_path)
    _run(
        ["run", "--data", '{"task": "a"}', "--", sys.executable, "-c", "pass"],
        env=env,
        cwd=tmp_path,
    )
    _run(
        [
            "run",
            "--data",
            '{"task": "b"}',
            "--",
            sys.executable,
            "-c",
            "import sys; sys.exit(1)",
        ],
        env=env,
        cwd=tmp_path,
    )
    result = _run(["finalize"], env=env, cwd=tmp_path)
    assert result.returncode == 0
    assert "- a: success" in result.stdout
    assert "- b: failure" in result.stdout


def test_finalize_reports_dangling_start_as_cancelled(tmp_path: Path) -> None:
    env = _reporting_env(tmp_path)
    state_path = tmp_path / "state.jsonl"
    state_path.write_text(
        json.dumps(
            {
                "event": "start",
                "run_key": "abc",
                "check_run_id": 42,
                "name": "orphaned-tool",
                "started_at": "2026-01-01T00:00:00Z",
            }
        )
        + "\n"
    )
    result = _run(["finalize"], env=env, cwd=tmp_path)
    assert result.returncode == 0
    assert "orphaned-tool: cancelled" in result.stdout


def test_finalize_skips_malformed_state_lines(tmp_path: Path) -> None:
    # A killed job or a filesystem without atomic-append guarantees can leave
    # a truncated/corrupt line - finalize must skip it, not crash the
    # always() reporting step over one bad line.
    env = _reporting_env(tmp_path)
    state_path = tmp_path / "state.jsonl"
    good_start = json.dumps(
        {
            "event": "start",
            "run_key": "a",
            "check_run_id": 1,
            "name": "a",
            "started_at": "2026-01-01T00:00:00Z",
        }
    )
    good_complete = json.dumps(
        {
            "event": "complete",
            "run_key": "a",
            "check_run_id": 1,
            "name": "a",
            "conclusion": "success",
            "exit_code": 0,
            "duration_s": 0.1,
            "completed_at": "2026-01-01T00:00:01Z",
        }
    )
    state_path.write_text(f"{good_start}\nnot valid json\n{good_complete}\n")
    result = _run(["finalize"], env=env, cwd=tmp_path)
    assert result.returncode == 0
    assert "- a: success" in result.stdout
    assert "skipping malformed state line" in result.stderr


def test_finalize_writes_to_step_summary_file(tmp_path: Path) -> None:
    env = _reporting_env(tmp_path)
    _run(
        ["run", "--data", '{"task": "mypy"}', "--", sys.executable, "-c", "pass"],
        env=env,
        cwd=tmp_path,
    )
    summary_path = tmp_path / "summary.md"
    env["GITHUB_STEP_SUMMARY"] = str(summary_path)
    result = _run(["finalize"], env=env, cwd=tmp_path)
    assert result.returncode == 0
    assert "mypy: success" in summary_path.read_text()


# ---------------------------------------------------------------------------
# Failure paths. GHA_CHECK_DRY_RUN can't reach these - it never makes a
# request, so it can never get a rejection back. These point the script at a
# stub API on localhost instead, so a fork PR's 403 and a transient 500 are
# real HTTP responses rather than monkeypatched internals.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _stub_api(
    respond: Callable[[str, str], tuple[int, dict[str, Any]]],
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Serve a stand-in GitHub API, yielding its base URL and the requests it got."""
    received: list[dict[str, Any]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            received.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "body": json.loads(self.rfile.read(length) or b"{}"),
                }
            )
            status, payload = respond(self.command, self.path)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = _handle  # ruff: ignore[mixed-case-variable-in-class-scope]
        do_PATCH = _handle  # ruff: ignore[mixed-case-variable-in-class-scope]

        def log_message(self, format: str, *args: Any) -> None:
            """Silence the default stderr access log."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _live_env(tmp_path: Path, api_url: str) -> dict[str, str]:
    """Reporting env that talks to a stub API instead of dry-running."""
    env = _reporting_env(tmp_path)
    del env["GHA_CHECK_DRY_RUN"]
    env["GITHUB_API_URL"] = api_url
    return env


def test_run_fails_on_sarif_findings_when_check_creation_is_forbidden(
    tmp_path: Path,
) -> None:
    """A fork PR's read-only token must not turn findings into a pass.

    Check-run creation 403s there. zizmor exits 0 even when it found
    something, so if losing the check run also loses the SARIF exit-code
    derivation, validation passes with findings present.
    """
    sarif = _sarif_with_one_result()
    with _stub_api(
        lambda method, path: (403, {"message": "Resource not accessible"})
    ) as (
        api_url,
        _received,
    ):
        result = _run(
            [
                "run",
                "--data",
                '{"task": "zizmor"}',
                "--output-is-sarif",
                "--",
                sys.executable,
                "-c",
                f"print({sarif!r})",
            ],
            env=_live_env(tmp_path, api_url),
            cwd=tmp_path,
        )
    assert result.returncode == 1
    # Nothing will render the check run, so the findings go to the job log.
    assert "demo/some-rule" in result.stderr
    assert "a.yml:3" in result.stderr


@pytest.mark.parametrize(
    ("patch_status", "expect_reported", "expect_finalize_retry"),
    [(500, False, True), (200, True, False)],
)
def test_finalize_retries_only_completions_the_api_rejected(
    tmp_path: Path,
    patch_status: int,
    expect_reported: bool,
    expect_finalize_retry: bool,
) -> None:
    """A completion that never landed must be retried, a landed one left alone.

    Without the retry the check run stays `in_progress` for good and blocks
    the pull request; without the `reported` flag guarding it, finalize would
    re-PATCH every check run on every job.
    """

    def respond(method: str, path: str) -> tuple[int, dict[str, Any]]:
        if method == "POST":
            return 201, {"id": 4242}
        return patch_status, {"message": "stub"}

    with _stub_api(respond) as (api_url, _received):
        run_result = _run(
            ["run", "--data", '{"task": "mypy"}', "--", sys.executable, "-c", ""],
            env=_live_env(tmp_path, api_url),
            cwd=tmp_path,
        )
    assert run_result.returncode == 0
    completion = _read_state(tmp_path)[-1]
    assert completion["event"] == "complete"
    assert completion["reported"] is expect_reported

    with _stub_api(lambda method, path: (200, {})) as (api_url, finalize_received):
        finalize_result = _run(
            ["finalize"], env=_live_env(tmp_path, api_url), cwd=tmp_path
        )
    assert finalize_result.returncode == 0

    if not expect_finalize_retry:
        assert finalize_received == []
        return
    assert [(r["method"], r["path"]) for r in finalize_received] == [
        ("PATCH", "/repos/example/repo/check-runs/4242")
    ]
    assert finalize_received[0]["body"]["status"] == "completed"
    assert finalize_received[0]["body"]["conclusion"] == "success"
