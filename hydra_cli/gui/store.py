"""Persisted projects and threads. Plain JSON on disk, no Qt dependency."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional


def default_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".hydra", "desktop", "state.json")


class Store:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or default_path()
        self.data: Dict[str, Any] = {"projects": [], "threads": [], "active_thread": None, "settings": {}}
        if os.path.isfile(self.path):
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            self.data.update(loaded)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1)
        os.replace(tmp, self.path)

    # projects
    def projects(self) -> List[Dict[str, Any]]:
        return self.data["projects"]

    def add_project(self, path: str) -> Dict[str, Any]:
        path = os.path.abspath(path)
        for proj in self.projects():
            if os.path.normcase(proj["path"]) == os.path.normcase(path):
                return proj
        proj = {"id": uuid.uuid4().hex[:10], "name": os.path.basename(path) or path, "path": path}
        self.projects().append(proj)
        self.save()
        return proj

    def remove_project(self, pid: str) -> None:
        self.data["projects"] = [p for p in self.projects() if p["id"] != pid]
        self.data["threads"] = [t for t in self.data["threads"] if t["project_id"] != pid]
        self.save()

    def project(self, pid: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.projects() if p["id"] == pid), None)

    # threads
    def threads(self, pid: str) -> List[Dict[str, Any]]:
        return sorted((t for t in self.data["threads"] if t["project_id"] == pid), key=lambda t: -t["updated"])

    def new_thread(self, pid: str, mode: str = "recall") -> Dict[str, Any]:
        tid = uuid.uuid4().hex[:10]
        thread = {
            "id": tid,
            "project_id": pid,
            "title": "New thread",
            "session_id": f"gui_{int(time.time())}_{tid}",
            "mode": mode,
            "messages": [],
            "diffs": [],
            "updated": time.time(),
        }
        self.data["threads"].append(thread)
        self.save()
        return thread

    def thread(self, tid: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self.data["threads"] if t["id"] == tid), None)

    def delete_thread(self, tid: str) -> None:
        self.data["threads"] = [t for t in self.data["threads"] if t["id"] != tid]
        self.save()

    def add_message(self, tid: str, role: str, text: str) -> None:
        thread = self.thread(tid)
        if thread is None:
            return
        thread["messages"].append({"role": role, "text": text})
        if role == "user" and thread["title"] == "New thread":
            thread["title"] = text.strip().splitlines()[0][:48] or "New thread"
        thread["updated"] = time.time()
        self.save()

    def add_diff(self, tid: str, path: str, text: str) -> None:
        thread = self.thread(tid)
        if thread is not None:
            thread["diffs"].append({"path": path, "text": text})
            self.save()
