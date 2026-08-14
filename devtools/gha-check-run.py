#!/usr/bin/env python3
"""Report each Taskfile leaf task as its own GitHub check run.

Wraps a tool invocation (`run`) so it creates a GitHub check run, runs the
tool, and completes the check run with the tool's conclusion; `finalize`
closes out any check left `in_progress` by a killed/cancelled job and writes
a summary. See Taskfile.yml's `CHECK_RUN_PREFIX` var for how `run` is wired
in, and .github/workflows/validate.yml for `finalize`.

Deliberately stdlib-only: this runs before `uv sync` has necessarily
completed, inside the mise devtools container.

Reporting only activates when running under GitHub Actions with a token,
repository, and head sha available (see `Env.reporting_enabled`) - outside
that (locally, or on a fork PR with a read-only token) `run` just executes
the command and returns its exit code unchanged. GHA_CHECK_DRY_RUN=1 stubs
the GitHub API calls (logs what would have been sent instead), for testing.

`run --output-is-sarif` additionally treats the command's stdout as a SARIF
document: the check run's text is a summary of its results instead of raw
log lines, and (when reporting is enabled) it is uploaded to the Code
Scanning API - see Taskfile.yml's `zizmor`, `ruff:lint`, and `rumdl:check`
tasks and `OUTPUT_SARIF` var.
"""

from __future__ import annotations

import argparse
import base64
import collections
import gzip
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_API_VERSION = "2022-11-28"
_DEFAULT_STATE_PATH = ".cache/gha-checks/state.jsonl"
_LOG_TAIL_LINES = 200
_LOG_TAIL_CHARS = 60_000


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sarif_ref(raw_ref: str | None) -> str | None:
    """Rewrite `refs/pull/<n>/merge` to `refs/pull/<n>/head`.

    The Code Scanning API 422s when `ref` and `commit_sha` don't refer to the
    same commit. On `pull_request` events GITHUB_REF is the ephemeral merge
    ref, but GHA_CHECK_HEAD_SHA (like check runs need) is the PR head sha -
    same fix github/codeql-action applies for the same reason.
    """
    if raw_ref and raw_ref.startswith("refs/pull/") and raw_ref.endswith("/merge"):
        return raw_ref.removesuffix("/merge") + "/head"
    return raw_ref


@dataclass(frozen=True, kw_only=True)
class Env:
    actions: bool
    token: str | None
    repository: str | None
    api_url: str
    server_url: str
    run_id: str | None
    run_attempt: str | None
    head_sha: str | None
    ref: str | None
    state_path: Path
    dry_run: bool

    @classmethod
    def from_os_environ(cls) -> Env:
        return cls(
            actions=os.environ.get("GITHUB_ACTIONS") == "true",
            token=os.environ.get("GITHUB_TOKEN") or None,
            repository=os.environ.get("GITHUB_REPOSITORY") or None,
            api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            server_url=os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
            run_id=os.environ.get("GITHUB_RUN_ID") or None,
            run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT") or None,
            head_sha=os.environ.get("GHA_CHECK_HEAD_SHA") or None,
            ref=_sarif_ref(os.environ.get("GITHUB_REF") or None),
            state_path=Path(os.environ.get("GHA_CHECK_STATE", _DEFAULT_STATE_PATH)),
            dry_run=os.environ.get("GHA_CHECK_DRY_RUN", "") not in ("", "0", "false"),
        )

    def reporting_enabled(self) -> bool:
        return bool(self.actions and self.token and self.repository and self.head_sha)


def _details_url(env: Env) -> str | None:
    if not (env.repository and env.run_id):
        return None
    url = f"{env.server_url}/{env.repository}/actions/runs/{env.run_id}"
    if env.run_attempt:
        url += f"/attempts/{env.run_attempt}"
    return url


def _api_call(
    env: Env,
    method: str,
    path: str,
    payload: dict[str, Any],
    *,
    log_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """POST/PATCH to the GitHub API, or (dry-run) log it and return None.

    `log_payload`, when given, is logged instead of `payload` in dry-run mode
    - lets callers redact large fields (e.g. a base64 SARIF blob) from logs.
    """
    url = f"{env.api_url}/repos/{env.repository}{path}"
    if env.dry_run:
        shown = payload if log_payload is None else log_payload
        _notify(f"DRY RUN: would {method} {url}\n  payload: {shown!r}")
        return None
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method=method
    )
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {env.token}")
    request.add_header("X-GitHub-Api-Version", _API_VERSION)
    with urllib.request.urlopen(request, timeout=30) as response:
        result: dict[str, Any] = json.load(response)
        return result


def _notify(message: str) -> None:
    print(f"::notice::[gha-check-run] {message}", file=sys.stderr)


def _warn(message: str) -> None:
    print(f"::warning::[gha-check-run] {message}", file=sys.stderr)


def _append_state(env: Env, record: dict[str, Any]) -> None:
    env.state_path.parent.mkdir(parents=True, exist_ok=True)
    with env.state_path.open("a") as handle:
        handle.write(json.dumps(record) + "\n")


def _tail(lines: collections.deque[str]) -> str:
    text = "".join(lines)
    if len(text) > _LOG_TAIL_CHARS:
        text = f"... (truncated) ...\n{text[-_LOG_TAIL_CHARS:]}"
    return text


def _create_check_run(env: Env, name: str, started_at: str) -> int | None:
    payload: dict[str, Any] = {
        "name": name,
        "head_sha": env.head_sha,
        "status": "in_progress",
        "started_at": started_at,
    }
    details_url = _details_url(env)
    if details_url:
        payload["details_url"] = details_url
    response = _api_call(env, "POST", "/check-runs", payload)
    return int(response["id"]) if response is not None else None


def _complete_check_run(
    env: Env,
    check_run_id: int | None,
    name: str,
    *,
    conclusion: str,
    completed_at: str,
    exit_code: int,
    log_tail: str,
) -> None:
    payload: dict[str, Any] = {
        "name": name,
        "head_sha": env.head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": completed_at,
        "output": {
            "title": f"{name}: {conclusion}",
            "summary": f"Exit code {exit_code}.",
            "text": f"```\n{log_tail}\n```" if log_tail else "(no output)",
        },
    }
    if check_run_id is None:
        # Only happens in dry-run mode - no real id to PATCH against.
        _notify(f"DRY RUN: would PATCH check-runs/<pending-id>\n  payload: {payload!r}")
        return
    _api_call(env, "PATCH", f"/check-runs/{check_run_id}", payload)


def _run_passthrough(command: Sequence[str]) -> int:
    return subprocess.run(command, check=False).returncode


def _parse_sarif_results(text: str) -> list[dict[str, Any]] | None:
    """Return the SARIF `results` across all runs, or None if `text` isn't SARIF.

    Parenthesized despite ruff's py314 target: this runs via a bare `python3`
    outside the project's uv-managed venv (see module docstring), so it must
    stay syntax-compatible with whatever python3 the GitHub Actions runner or
    devtools container happen to provide - not necessarily 3.14.
    """
    try:
        doc = json.loads(text)
        return [r for run in doc["runs"] for r in run.get("results", [])]
    except (json.JSONDecodeError, KeyError, TypeError):  # fmt: skip
        return None


def _summarize_sarif_results(
    results: list[dict[str, Any]], *, max_results: int = 50
) -> str:
    """Render SARIF `results` as short human-readable lines."""
    if not results:
        return "No SARIF results."
    lines = []
    for result in results[:max_results]:
        rule = result.get("ruleId", "?")
        level = result.get("level", "warning")
        message = result.get("message", {}).get("text", "")
        location = ""
        for loc in result.get("locations") or []:
            physical = loc.get("physicalLocation", {})
            uri = physical.get("artifactLocation", {}).get("uri", "")
            region = physical.get("region", {})
            start_line = region.get("startLine") or region.get("endLine")
            location = f"{uri}:{start_line}" if start_line else uri
            break
        lines.append(f"- [{level}] {rule} ({location}): {message}")
    if len(results) > max_results:
        lines.append(f"... and {len(results) - max_results} more")
    return "\n".join(lines)


def _upload_sarif(env: Env, name: str, sarif_text: str, started_at: str) -> None:
    """Best-effort upload to the Code Scanning API. Never raises.

    No "category" field - unlike check-runs/check-suites, the sarifs upload
    endpoint rejects it outright ("category is not a permitted key"); category
    is instead read from the SARIF document's own `runs[].automationDetails`
    if present, not from the request. Confirmed directly against the live API
    with a real payload after a 422 in production traced back to this.

    `checkout_uri` is the cwd the wrapped tool ran in, as a `file://` URI -
    some tools (ruff, unlike zizmor/rumdl) emit absolute `file://` paths in
    their SARIF rather than repo-relative ones; without this, GitHub can't
    map those to files in the PR diff, so alerts exist under the Security tab
    but never appear as "Files changed" annotations. Confirmed against a live
    PR: ruff's alert location was `file:///srv/workspace/...` (the devtools
    container's workdir, matching `Path.cwd()` here since gha-check-run.py
    and the wrapped subprocess share a cwd).
    """
    sarif_b64 = base64.b64encode(gzip.compress(sarif_text.encode())).decode()
    payload: dict[str, Any] = {
        "commit_sha": env.head_sha,
        "ref": env.ref,
        "sarif": sarif_b64,
        "tool_name": name,
        "started_at": started_at,
        "checkout_uri": Path.cwd().as_uri(),
    }
    log_payload = {**payload, "sarif": f"<{len(sarif_b64)} chars>"}
    try:
        # https://docs.github.com/en/rest/code-scanning/code-scanning?apiVersion=2026-03-10#upload-an-analysis-as-sarif-data
        response = _api_call(
            env, "POST", "/code-scanning/sarifs", payload, log_payload=log_payload
        )
        if response is not None:
            _notify(
                f"uploaded SARIF for {name!r}: {response.get('url', '(no url in response)')}"
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        _warn(
            f"could not upload SARIF for {name!r}: HTTP {exc.code} {exc.reason}: {detail}"
        )
    except (urllib.error.URLError, OSError) as exc:
        _warn(f"could not upload SARIF for {name!r}: {exc}")


def _run_reported(
    env: Env, name: str, command: Sequence[str], *, output_is_sarif: bool = False
) -> int:
    # check_run_id is None in dry-run mode, so it can't double as the key
    # finalize pairs start/complete records by (every task would collide on
    # None) - run_key is a separate identity just for that.
    run_key = uuid.uuid4().hex
    started_at = _now_iso()
    try:
        check_run_id = _create_check_run(env, name, started_at)
    except (urllib.error.URLError, OSError) as exc:
        _notify(f"could not create check run for {name!r} ({exc}); reporting disabled")
        return _run_passthrough(command)

    _append_state(
        env,
        {
            "event": "start",
            "run_key": run_key,
            "check_run_id": check_run_id,
            "name": name,
            "started_at": started_at,
        },
    )

    tail: collections.deque[str] = collections.deque(maxlen=_LOG_TAIL_LINES)
    full_lines: list[str] = []
    start = time.monotonic()
    # SARIF mode keeps stderr separate (a tool's INFO/warning logs on stderr
    # would otherwise corrupt the captured stdout as a JSON document) and
    # buffers every line (a truncated tail would be invalid SARIF to upload).
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=None if output_is_sarif else subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is None:
        raise RuntimeError("subprocess stdout was not piped")
    for line in process.stdout:
        # SARIF mode swallows stdout instead of echoing it - the SARIF
        # document is consumed here (summarized into the check run, uploaded
        # to Code Scanning), not meant for a human reading the job log, and
        # re-emitting a multi-KB JSON blob there is just noise.
        if not output_is_sarif:
            sys.stdout.write(line)
        tail.append(line)
        if output_is_sarif:
            full_lines.append(line)
    exit_code = process.wait()
    duration = time.monotonic() - start
    completed_at = _now_iso()

    log_tail = _tail(tail)
    if output_is_sarif:
        full_text = "".join(full_lines)
        _notify(
            f"{name}: captured {len(full_text)} byte(s) of SARIF output from stdout"
        )
        sarif_results = _parse_sarif_results(full_text)
        if sarif_results is not None:
            log_tail = _summarize_sarif_results(sarif_results)
            # Some SARIF-capable tools (zizmor among them) always exit 0 in
            # SARIF mode, leaving pass/fail entirely to the SARIF results
            # themselves - so a non-empty result set fails the check run
            # even though the process didn't.
            if sarif_results and exit_code == 0:
                _warn(
                    f"{name}: exited 0 but SARIF reported {len(sarif_results)} "
                    "result(s); treating the check run as failed"
                )
                exit_code = 1
        if env.reporting_enabled():
            _upload_sarif(env, name, full_text, started_at)
    conclusion = "success" if exit_code == 0 else "failure"
    try:
        _complete_check_run(
            env,
            check_run_id,
            name,
            conclusion=conclusion,
            completed_at=completed_at,
            exit_code=exit_code,
            log_tail=log_tail,
        )
    except (urllib.error.URLError, OSError) as exc:
        _warn(f"could not update check run for {name!r}: {exc}")

    _append_state(
        env,
        {
            "event": "complete",
            "run_key": run_key,
            "check_run_id": check_run_id,
            "name": name,
            "conclusion": conclusion,
            "exit_code": exit_code,
            "duration_s": round(duration, 3),
            "completed_at": completed_at,
        },
    )
    return exit_code


def cmd_run(args: argparse.Namespace) -> int:
    # nargs=REMAINDER doesn't strip a leading '--', so strip it here.
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        args.parser.error("missing command after '--'")

    data: dict[str, Any] = {}
    for raw in args.data:
        data.update(json.loads(raw))
    name = str(data.get("task") or command[0])

    env = Env.from_os_environ()
    if not env.reporting_enabled():
        return _run_passthrough(command)
    return _run_reported(env, name, command, output_is_sarif=args.output_is_sarif)


def cmd_finalize(args: argparse.Namespace) -> int:
    env = Env.from_os_environ()
    if not env.state_path.exists():
        return 0

    starts: dict[str, dict[str, Any]] = {}
    completions: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in env.state_path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        run_key = record["run_key"]
        if record["event"] == "start":
            starts[run_key] = record
            order.append(run_key)
        elif record["event"] == "complete":
            completions[run_key] = record

    summary_lines: list[str] = []
    for run_key in order:
        name = starts[run_key]["name"]
        check_run_id = starts[run_key]["check_run_id"]
        completion = completions.get(run_key)
        if completion is None:
            summary_lines.append(f"- {name}: cancelled (job ended before it finished)")
            if check_run_id is not None:
                try:
                    _api_call(
                        env,
                        "PATCH",
                        f"/check-runs/{check_run_id}",
                        {
                            "name": name,
                            "head_sha": env.head_sha,
                            "status": "completed",
                            "conclusion": "cancelled",
                            "completed_at": _now_iso(),
                        },
                    )
                except (urllib.error.URLError, OSError) as exc:
                    _warn(f"could not cancel dangling check run for {name!r}: {exc}")
            continue
        summary_lines.append(f"- {name}: {completion['conclusion']}")

    summary = "\n".join(summary_lines) + "\n"
    step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        with Path(step_summary_path).open("a") as handle:
            handle.write(f"## Tool results\n\n{summary}")
    else:
        print(f"Tool results:\n{summary}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gha-check-run.py",
        description=(
            "Report each Taskfile leaf task as its own GitHub check run "
            "(no-op outside GitHub Actions - see module docstring)."
        ),
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    run_parser = subparsers.add_parser(
        "run", help="run a command, reporting it as a GitHub check run"
    )
    run_parser.add_argument(
        "--data",
        action="append",
        default=[],
        metavar="JSON",
        help='JSON object merged into the check run\'s metadata (e.g. \'{"task": "mypy"}\'); repeatable',
    )
    run_parser.add_argument(
        "--output-is-sarif",
        action="store_true",
        help=(
            "treat the command's stdout as a SARIF document: derive the check "
            "run's text from it instead of raw log lines, and upload it to "
            "the Code Scanning API when reporting is enabled"
        ),
    )
    run_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="the command to run, preceded by '--'",
    )
    run_parser.set_defaults(func=cmd_run, parser=run_parser)

    finalize_parser = subparsers.add_parser(
        "finalize",
        help="close out any check runs left in_progress and write a summary",
    )
    finalize_parser.set_defaults(func=cmd_finalize, parser=finalize_parser)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
