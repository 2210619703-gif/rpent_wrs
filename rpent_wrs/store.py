"""Persistent memory + per-run transcript / recipe / audit."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = PROJECT_ROOT / "memory"
RUNS_DIR = PROJECT_ROOT / "runs"

DEFAULT_MAX_CHARS = 40_000
RECIPE_SKIP = frozenset({"read_text_file", "list_dir", "view_state", "get_pose"})


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


class RunStore:
    def __init__(self, run_dir: Path, *, memory_dir: Path = MEMORY_DIR) -> None:
        self.memory_dir = memory_dir.resolve()
        self.run_dir = run_dir.resolve()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_path = self.run_dir / "transcript.jsonl"
        self.recipe_path = self.run_dir / "recipe.jsonl"
        self.audit_path = self.run_dir / "audit.json"

    @classmethod
    def create(cls, *, root: Path | None = None) -> "RunStore":
        base = (root or PROJECT_ROOT).resolve()
        run_dir = base / "runs" / _timestamp()
        return cls(run_dir, memory_dir=base / "memory")

    def display(self, path: Path) -> str:
        for root, prefix in ((self.memory_dir, "memory"), (self.run_dir, "run")):
            try:
                return f"{prefix}/{path.relative_to(root).as_posix()}"
            except ValueError:
                continue
        return str(path)

    def resolve(self, raw: str) -> Path:
        text = (raw or "").strip()
        if not text or text in {".", "./"}:
            return self.memory_dir
        path = Path(text)
        if path.is_absolute():
            resolved = path.resolve()
        else:
            parts = path.parts
            head = parts[0] if parts else ""
            if head == "memory":
                resolved = (self.memory_dir / Path(*parts[1:])).resolve()
            elif head in {"run", "runs"}:
                rest = parts[1:]
                if rest and rest[0] == self.run_dir.name:
                    rest = rest[1:]
                resolved = (self.run_dir / Path(*rest)).resolve()
            else:
                resolved = (self.memory_dir / path).resolve()
        for root in (self.memory_dir, self.run_dir):
            if resolved == root or resolved.is_relative_to(root):
                return resolved
        raise ValueError("path must stay under memory/ or this run/")

    def memory_listing(self) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for path in sorted(self.memory_dir.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            preview = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            items.append(
                {
                    "path": self.display(path),
                    "preview": preview[0][:120] if preview else "",
                }
            )
        return items

    def list_dir(self, path: str = "") -> dict[str, Any]:
        try:
            target = self.resolve(path)
        except ValueError as exc:
            return {"error": str(exc)}
        if not target.exists():
            return {"error": f"missing: {self.display(target)}"}
        if not target.is_dir():
            return {"error": f"not a directory: {self.display(target)}"}
        names = sorted(
            p.name + ("/" if p.is_dir() else "")
            for p in target.iterdir()
            if not p.name.startswith(".")
        )
        return {"path": self.display(target), "entries": names}

    def read_text(self, path: str, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
        limit = max(1, int(max_chars or DEFAULT_MAX_CHARS))
        try:
            target = self.resolve(path)
        except ValueError as exc:
            return {"error": str(exc)}
        if not target.is_file():
            return {"error": f"not a file: {self.display(target)}"}
        text = target.read_text(encoding="utf-8", errors="replace")
        return {
            "path": self.display(target),
            "content": text[:limit],
            "truncated": len(text) > limit,
        }

    def write_text(self, path: str, content: str) -> dict[str, Any]:
        try:
            target = self.resolve(path)
        except ValueError as exc:
            return {"error": str(exc)}
        if target in {self.memory_dir, self.run_dir}:
            return {"error": "path must be a file, not a directory"}
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (
            target.parent == self.memory_dir
            or target.parent == self.run_dir
            or target.parent.is_relative_to(self.memory_dir)
            or target.parent.is_relative_to(self.run_dir)
        ):
            return {"error": "path must stay under memory/ or this run/"}
        target.write_text(content if content is not None else "", encoding="utf-8")
        return {"ok": True, "path": self.display(target), "bytes": target.stat().st_size}

    def append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def log_event(self, payload: dict[str, Any]) -> None:
        self.append_jsonl(self.transcript_path, payload)

    def log_tool(self, name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        event = {"tool": name, "args": args, "result": result}
        self.log_event({"type": "tool", **event})
        if name not in RECIPE_SKIP:
            self.append_jsonl(self.recipe_path, event)

    def write_audit(self, payload: dict[str, Any]) -> Path:
        self.audit_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.log_event({"type": "audit", **payload})
        if not payload.get("scene_success") and self.recipe_path.exists():
            # Keep the attempted steps in transcript; only solved runs keep recipe.
            leftover = self.recipe_path.read_text(encoding="utf-8")
            if leftover.strip():
                self.append_jsonl(
                    self.transcript_path,
                    {"type": "unsolved_recipe", "recipe": leftover},
                )
            self.recipe_path.unlink()
        return self.audit_path
