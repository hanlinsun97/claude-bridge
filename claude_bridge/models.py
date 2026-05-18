from __future__ import annotations
import uuid
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkflowConfig:
    pre_skills: list[str] = field(default_factory=list)
    post_skills: list[str] = field(default_factory=list)
    codex_iterations: int = 0
    validation: Optional[str] = None
    custom_instructions: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> WorkflowConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SelfHealingConfig:
    mode: str = "time_bounded"  # "always" | "time_bounded" | "single_session"
    max_hours: Optional[float] = 8.0
    max_resets: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SelfHealingConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Job:
    prompt: str
    cwd: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "task"
    status: str = "pending"
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    model: str = "claude-sonnet-4-6"
    workspace: str = ""
    source_files: list[str] = field(default_factory=list)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    self_healing: SelfHealingConfig = field(default_factory=SelfHealingConfig)
    error: Optional[str] = None
    session_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Job:
        wf = WorkflowConfig.from_dict(d.pop("workflow", {}))
        sh = SelfHealingConfig.from_dict(d.pop("self_healing", {}))
        known = {k for k in cls.__dataclass_fields__} - {"workflow", "self_healing"}
        return cls(
            **{k: v for k, v in d.items() if k in known},
            workflow=wf,
            self_healing=sh,
        )


@dataclass
class Queue:
    schema_version: str = "1.0"
    jobs: list[Job] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {"schema_version": self.schema_version, "jobs": [j.to_dict() for j in self.jobs]},
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> Queue:
        d = json.loads(raw)
        jobs = [Job.from_dict(j) for j in d.get("jobs", [])]
        return cls(schema_version=d.get("schema_version", "1.0"), jobs=jobs)
