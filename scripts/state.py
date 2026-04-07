import json
import os
from typing import Set

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")


class StateManager:
    def __init__(self, path: str = STATE_FILE):
        self.path = path
        self.contacted: Set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.contacted = set(data.get("contacted", []))
            except (json.JSONDecodeError, KeyError):
                self.contacted = set()

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"contacted": sorted(self.contacted)}, f, ensure_ascii=False, indent=2)

    def is_contacted(self, job_id: str) -> bool:
        return job_id in self.contacted

    def mark_contacted(self, job_id: str):
        self.contacted.add(job_id)
        self._save()

    def total(self) -> int:
        return len(self.contacted)
