"""Preserve the exact content of transient files an agent writes, uses, and deletes.

Agents routinely put the business logic of a run in short-lived helper scripts and
intermediate data files that they delete before exiting. Recording only the file name
in the audit trail leaves the run unreproducible, so the runner copies those files into
a run-local content store at the moment it observes them.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import (
    ArtifactContentCapture,
    AuditContentStore,
    ContentCaptureObservation,
    SkippedContentCapture,
)

STORE_DIR_NAME = "captured-files"

DEFAULT_MAX_FILE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 256 * 1024 * 1024
DEFAULT_SWEEP_DEPTH = 4
DEFAULT_SWEEP_FILE_LIMIT = 20_000
DEFAULT_SWEEP_INTERVAL_SECONDS = 0.5

# Directories that never hold agent-authored working files but can hold very many files.
SKIP_DIRECTORY_NAMES = frozenset(
    {
        ".cache",
        ".git",
        ".gradle",
        ".hg",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".terraform",
        ".tox",
        ".venv",
        ".vscode",
        "__pycache__",
        "node_modules",
        "site-packages",
        "venv",
    }
)

_TOKEN_SPLIT = re.compile(r"[\s'\"`;|&<>()\[\]{}:,=]+")
_PATH_CANDIDATE = re.compile(r"^[\w~./][\w./+@-]*$")
_UNSAFE_NAME = re.compile(r"[^\w.-]+")
_MAX_COMMAND_TOKENS = 400


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _flag(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "off", "no"}


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


@dataclass(frozen=True)
class CaptureConfig:
    """Bounds on how much transient content a single run may preserve."""

    enabled: bool = True
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    sweep_depth: int = DEFAULT_SWEEP_DEPTH
    sweep_file_limit: int = DEFAULT_SWEEP_FILE_LIMIT
    sweep_interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> CaptureConfig:
        source = os.environ if environ is None else environ
        return cls(
            enabled=_flag(source.get("OPENMAPBENCH_AUDIT_CAPTURE"), True),
            max_file_bytes=_positive_int(
                source.get("OPENMAPBENCH_AUDIT_CAPTURE_MAX_FILE_BYTES"),
                DEFAULT_MAX_FILE_BYTES,
            ),
            max_total_bytes=_positive_int(
                source.get("OPENMAPBENCH_AUDIT_CAPTURE_MAX_TOTAL_BYTES"),
                DEFAULT_MAX_TOTAL_BYTES,
            ),
            sweep_depth=_positive_int(
                source.get("OPENMAPBENCH_AUDIT_CAPTURE_SWEEP_DEPTH"),
                DEFAULT_SWEEP_DEPTH,
            ),
        )

    def as_policy(self) -> dict[str, Any]:
        return {
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
            "sweep_depth": self.sweep_depth,
            "sweep_file_limit": self.sweep_file_limit,
            "skipped_directory_names": sorted(SKIP_DIRECTORY_NAMES),
        }


@dataclass
class _Version:
    sha256: str
    size_bytes: int
    stored_path: str
    encoding: str
    line_count: int | None
    media_type: str | None
    first_observed_at: str
    last_observed_at: str
    observations: list[ContentCaptureObservation] = field(default_factory=list)


def _content_shape(data: bytes) -> tuple[str, int | None]:
    if b"\x00" in data:
        return "binary", None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "binary", None
    if not text:
        return "utf-8", 0
    return "utf-8", text.count("\n") + (0 if text.endswith("\n") else 1)


def _store_name(path: Path, sha256: str) -> str:
    name = _UNSAFE_NAME.sub("_", path.name).strip("_") or "file"
    return f"{sha256[:12]}-{name[:80]}"


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def command_path_tokens(command: str | list[str] | None) -> list[str]:
    """Extract path-like tokens from a command line, heredoc bodies included."""
    if command is None:
        return []
    text = " ".join(str(part) for part in command) if isinstance(command, list) else str(command)
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_SPLIT.split(text):
        if len(tokens) >= _MAX_COMMAND_TOKENS:
            break
        token = raw.strip().strip("\\")
        if not token or token in seen:
            continue
        seen.add(token)
        if not _PATH_CANDIDATE.match(token):
            continue
        if "/" not in token and "." not in token[1:]:
            continue
        tokens.append(token)
    return tokens


class ContentCapture:
    """Copy observed transient files into ``<run-dir>/captured-files`` as they appear."""

    def __init__(
        self,
        *,
        run_dir: Path,
        execution_cwd: Path,
        started_at: datetime,
        config: CaptureConfig | None = None,
        exempt_paths: list[Path] | None = None,
        exempt_dirs: list[Path] | None = None,
    ) -> None:
        self.config = config or CaptureConfig()
        self.run_dir = run_dir.resolve()
        self.store_dir = self.run_dir / STORE_DIR_NAME
        self.execution_cwd = execution_cwd.resolve()
        self._start_epoch = started_at.timestamp()
        self._exempt_paths = {path.resolve() for path in exempt_paths or []}
        self._exempt_dirs = [self.run_dir, *(path.resolve() for path in exempt_dirs or [])]
        self._lock = threading.RLock()
        self._versions: dict[str, dict[str, _Version]] = {}
        self._skipped: dict[tuple[str, str], SkippedContentCapture] = {}
        self._total_bytes = 0
        self._last_sweep = 0.0
        self._sweep_truncated = False
        self._store_created = False

    # -- observation entry points -------------------------------------------------

    def observe_codex_event(self, payload: dict[str, Any]) -> None:
        """React to one parsed Codex JSONL line while the agent is still running."""
        if not self.config.enabled:
            return
        item = payload.get("item")
        if str(payload.get("type") or "").startswith("item.") and isinstance(item, dict):
            item_id = str(item.get("id") or "")
            event_id = f"codex:{item_id}" if item_id else None
            item_type = str(item.get("type") or "")
            if item_type == "file_change":
                self.observe_file_changes(item.get("changes"), event_id=event_id)
            elif item_type == "command_execution":
                self.observe_command(item.get("command"), event_id=event_id)
        self.sweep()

    def observe_file_changes(self, changes: Any, *, event_id: str | None) -> None:
        if not isinstance(changes, list):
            return
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("path", change.get("file_path"))
            if not isinstance(value, str) or not value.strip():
                continue
            kind = str(change.get("kind") or change.get("type") or "")
            self.capture(
                self._resolve(value),
                reason="file_change",
                event_id=event_id,
                relationship="referenced_by" if kind == "delete" else "produced_by",
            )

    def observe_command(self, command: str | list[str] | None, *, event_id: str | None) -> None:
        for token in command_path_tokens(command):
            self.capture(
                self._resolve(token),
                reason="command_reference",
                event_id=event_id,
                relationship="referenced_by",
            )

    def sweep(self, *, reason: str = "directory_sweep", force: bool = False) -> None:
        """Preserve files under the agent working directory that changed during the run."""
        if not self.config.enabled:
            return
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_sweep < self.config.sweep_interval_seconds:
                return
            self._last_sweep = now
        remaining = self.config.sweep_file_limit
        stack: list[tuple[Path, int]] = [(self.execution_cwd, 0)]
        while stack and remaining > 0:
            directory, depth = stack.pop()
            try:
                with os.scandir(directory) as scan:
                    entries = list(scan)
            except OSError:
                continue
            for entry in entries:
                if remaining <= 0:
                    self._sweep_truncated = True
                    break
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if depth >= self.config.sweep_depth:
                            continue
                        if entry.name in SKIP_DIRECTORY_NAMES:
                            continue
                        child = Path(entry.path)
                        if any(_within(child, exempt) for exempt in self._exempt_dirs):
                            continue
                        stack.append((child, depth + 1))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    remaining -= 1
                    if entry.stat(follow_symlinks=False).st_mtime < self._start_epoch:
                        continue
                except OSError:
                    continue
                self.capture(
                    Path(entry.path),
                    reason=reason,
                    event_id=None,
                    relationship="produced_by",
                )

    # -- capture ------------------------------------------------------------------

    def capture(
        self,
        path: Path,
        *,
        reason: str,
        event_id: str | None = None,
        relationship: str = "referenced_by",
    ) -> None:
        """Store the current content of ``path`` unless it is exempt or already stored."""
        if not self.config.enabled:
            return
        try:
            resolved = path.expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return
        if not self._eligible(resolved):
            return
        try:
            stat = resolved.stat()
        except OSError:
            return
        observed_at = _now_iso()
        if stat.st_size > self.config.max_file_bytes:
            self._skip(
                resolved,
                "exceeds_max_file_bytes",
                observed_at,
                size_bytes=stat.st_size,
                detail=f"file limit is {self.config.max_file_bytes} bytes",
            )
            return
        try:
            data = resolved.read_bytes()
        except OSError as exc:
            self._skip(
                resolved,
                "unreadable",
                observed_at,
                size_bytes=stat.st_size,
                detail=f"{type(exc).__name__}: {exc}",
            )
            return

        sha256 = hashlib.sha256(data).hexdigest()
        observation = ContentCaptureObservation(
            observed_at=observed_at,
            reason=reason,
            relationship=relationship,
            event_id=event_id,
        )
        key = str(resolved)
        with self._lock:
            existing = self._versions.get(key, {}).get(sha256)
            if existing is not None:
                existing.last_observed_at = observed_at
                if not any(
                    item.reason == reason and item.event_id == event_id
                    for item in existing.observations
                ):
                    existing.observations.append(observation)
                return
            if self._total_bytes + len(data) > self.config.max_total_bytes:
                self._skip(
                    resolved,
                    "capture_budget_exhausted",
                    observed_at,
                    size_bytes=len(data),
                    detail=f"run content store limit is {self.config.max_total_bytes} bytes",
                )
                return
            stored = self._write(resolved, data, sha256)
            if stored is None:
                self._skip(
                    resolved,
                    "unreadable",
                    observed_at,
                    size_bytes=len(data),
                    detail="could not write into the run content store",
                )
                return
            self._total_bytes += len(data)
            encoding, line_count = _content_shape(data)
            media_type, _ = mimetypes.guess_type(resolved.name)
            self._versions.setdefault(key, {})[sha256] = _Version(
                sha256=sha256,
                size_bytes=len(data),
                stored_path=stored,
                encoding=encoding,
                line_count=line_count,
                media_type=media_type,
                first_observed_at=observed_at,
                last_observed_at=observed_at,
                observations=[observation],
            )

    def _write(self, path: Path, data: bytes, sha256: str) -> str | None:
        target = self.store_dir / _store_name(path, sha256)
        try:
            if not self._store_created:
                self.store_dir.mkdir(parents=True, exist_ok=True)
                self._store_created = True
            if not target.exists():
                target.write_bytes(data)
        except OSError:
            return None
        return target.relative_to(self.run_dir).as_posix()

    def _skip(
        self,
        path: Path,
        reason: str,
        observed_at: str,
        *,
        size_bytes: int | None = None,
        detail: str | None = None,
    ) -> None:
        record = SkippedContentCapture(
            path=str(path),
            reason=reason,  # type: ignore[arg-type]
            observed_at=observed_at,
            size_bytes=size_bytes,
            detail=detail,
        )
        with self._lock:  # re-entrant: capture() already holds it on the budget path
            self._skipped.setdefault((str(path), reason), record)

    def _eligible(self, path: Path) -> bool:
        if path in self._exempt_paths:
            return False
        if any(_within(path, exempt) for exempt in self._exempt_dirs):
            return False
        if SKIP_DIRECTORY_NAMES.intersection(path.parts[:-1]):
            return False
        if not path.is_file():
            return False
        try:
            return path.stat().st_mtime >= self._start_epoch
        except OSError:
            return False

    def _resolve(self, value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else self.execution_cwd / candidate

    # -- results ------------------------------------------------------------------

    def captures_by_path(self) -> dict[str, list[ArtifactContentCapture]]:
        with self._lock:
            return {
                path: [
                    ArtifactContentCapture(
                        sha256=version.sha256,
                        size_bytes=version.size_bytes,
                        stored_path=version.stored_path,
                        encoding=version.encoding,  # type: ignore[arg-type]
                        line_count=version.line_count,
                        media_type=version.media_type,
                        first_observed_at=version.first_observed_at,
                        last_observed_at=version.last_observed_at,
                        observations=list(version.observations),
                    )
                    for version in sorted(
                        versions.values(), key=lambda item: item.first_observed_at
                    )
                ]
                for path, versions in self._versions.items()
            }

    def store_summary(self) -> AuditContentStore:
        with self._lock:
            policy = self.config.as_policy()
            policy["enabled"] = self.config.enabled
            policy["exempt"] = (
                "task file, declared inputs, reference, and everything already kept "
                "inside the run directory"
            )
            if self._sweep_truncated:
                policy["sweep_truncated"] = True
            return AuditContentStore(
                path=STORE_DIR_NAME,
                file_count=len(self._versions),
                version_count=sum(len(versions) for versions in self._versions.values()),
                total_bytes=self._total_bytes,
                policy=policy,
                skipped=sorted(self._skipped.values(), key=lambda item: item.path),
            )
