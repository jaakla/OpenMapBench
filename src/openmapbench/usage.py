from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import RunManifest, TokenUsage
from .pricing import estimate_cost

MODEL_PATTERN = re.compile(r"^model:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)
REASONING_PATTERN = re.compile(
    r"^reasoning effort:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE
)
TOTAL_PATTERN = re.compile(
    r"^tokens used\s*\r?\n\s*([\d,]+)\s*$", re.MULTILINE | re.IGNORECASE
)
TOKEN_KEYS = {
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
}


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        compact = value.replace(",", "").strip()
        return int(compact) if compact.isdigit() else None
    return None


def _usage_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if any(key in value for key in TOKEN_KEYS):
            yield value
        usage = value.get("usage")
        if isinstance(usage, dict):
            yield usage
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _usage_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _usage_dicts(child)


def _json_usage_candidates(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        candidates.extend(_usage_dicts(payload))
    return candidates


def _token_usage_from_dict(
    payload: dict[str, Any],
    *,
    model: str | None,
    reasoning_effort: str | None,
) -> TokenUsage | None:
    input_details = payload.get("input_tokens_details")
    output_details = payload.get("output_tokens_details")
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = output_details if isinstance(output_details, dict) else {}

    input_tokens = _integer(payload.get("input_tokens"))
    cached_input = _integer(payload.get("cached_input_tokens"))
    if cached_input is None:
        cached_input = _integer(input_details.get("cached_tokens"))
    cache_write = _integer(payload.get("cache_write_input_tokens"))
    if cache_write is None:
        cache_write = _integer(payload.get("cache_write_tokens"))
    if cache_write is None:
        cache_write = _integer(input_details.get("cache_write_tokens"))
    output_tokens = _integer(payload.get("output_tokens"))
    reasoning_output = _integer(payload.get("reasoning_output_tokens"))
    if reasoning_output is None:
        reasoning_output = _integer(output_details.get("reasoning_tokens"))
    total_tokens = _integer(payload.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if total_tokens is None:
        return None

    return TokenUsage(
        source="codex_jsonl",
        model=model,
        reasoning_effort=reasoning_effort,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input,
        cache_write_input_tokens=cache_write,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output,
        total_tokens=total_tokens,
    )


def parse_agent_usage(
    stdout: str,
    stderr: str,
    *,
    declared_model: str | None = None,
) -> TokenUsage | None:
    """Parse Codex JSONL usage or its final human-readable token summary."""
    model_match = MODEL_PATTERN.search(stderr)
    reasoning_match = REASONING_PATTERN.search(stderr)
    model = model_match.group(1) if model_match else declared_model
    reasoning_effort = reasoning_match.group(1) if reasoning_match else None

    candidates = _json_usage_candidates(stdout) + _json_usage_candidates(stderr)
    for candidate in reversed(candidates):
        usage = _token_usage_from_dict(
            candidate,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        if usage is not None:
            return usage

    matches = list(TOTAL_PATTERN.finditer(stderr))
    if not matches:
        return None
    return TokenUsage(
        source="codex_stderr_summary",
        model=model,
        reasoning_effort=reasoning_effort,
        total_tokens=int(matches[-1].group(1).replace(",", "")),
    )


def backfill_usage(run_root: Path) -> dict[str, Any]:
    """Add token usage and pricing to existing run manifests using retained agent logs."""
    summary: dict[str, Any] = {
        "scanned": 0,
        "updated": 0,
        "already_complete": 0,
        "no_usage_found": 0,
        "not_run_manifest": 0,
        "invalid_manifests": [],
    }
    for manifest_path in sorted(run_root.rglob("manifest.json")):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            summary["invalid_manifests"].append(
                {"path": str(manifest_path), "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        if not isinstance(payload, dict) or not {"run_id", "task_id", "command"} <= payload.keys():
            summary["not_run_manifest"] += 1
            continue
        summary["scanned"] += 1

        token_payload = payload.get("token_usage")
        try:
            usage = TokenUsage.model_validate(token_payload) if token_payload else None
        except ValidationError:
            usage = None
        if usage is None:
            stdout_path = manifest_path.parent / "agent.stdout.log"
            stderr_path = manifest_path.parent / "agent.stderr.log"
            try:
                stdout = stdout_path.read_text(encoding="utf-8") if stdout_path.is_file() else ""
                stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.is_file() else ""
            except OSError as exc:
                summary["invalid_manifests"].append(
                    {"path": str(manifest_path), "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            declared_model = payload.get("agent", {}).get("model")
            usage = parse_agent_usage(stdout, stderr, declared_model=declared_model)
        cost = estimate_cost(usage) if usage is not None else None
        desired_usage = usage.model_dump(mode="json") if usage else None
        desired_cost = cost.model_dump(mode="json") if cost else None
        desired_schema = (
            "0.3"
            if payload.get("schema_version") == "0.3" or payload.get("audit") is not None
            else "0.2"
        )
        if usage is None:
            summary["no_usage_found"] += 1
            continue
        if (
            payload.get("schema_version") == desired_schema
            and payload.get("token_usage") == desired_usage
            and payload.get("cost_estimate") == desired_cost
        ):
            summary["already_complete"] += 1
            continue

        payload["schema_version"] = desired_schema
        payload["token_usage"] = desired_usage
        payload["cost_estimate"] = desired_cost
        try:
            RunManifest.model_validate(payload)
        except ValidationError as exc:
            summary["invalid_manifests"].append(
                {"path": str(manifest_path), "error": f"ValidationError: {exc}"}
            )
            continue
        temporary_path = manifest_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(manifest_path)
        summary["updated"] += 1
    return summary
