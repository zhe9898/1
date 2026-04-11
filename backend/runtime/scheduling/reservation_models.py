from __future__ import annotations

import datetime
from dataclasses import dataclass


def resolve_reservation_tenant_id(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return "default"


@dataclass(slots=True)
class ResourceReservation:
    """A reservation of resources on a specific node for a future job."""

    job_id: str
    node_id: str
    start_at: datetime.datetime
    end_at: datetime.datetime
    priority: int
    cpu_cores: float = 0.0
    memory_mb: float = 0.0
    gpu_vram_mb: float = 0.0
    slots: int = 1
    tenant_id: str = "default"

    def overlaps(self, start: datetime.datetime, end: datetime.datetime) -> bool:
        return start < self.end_at and end > self.start_at

    def is_expired(self, now: datetime.datetime) -> bool:
        return now >= self.end_at

    def resource_conflicts(
        self,
        cpu: float = 0.0,
        memory: float = 0.0,
        gpu: float = 0.0,
    ) -> bool:
        return (self.cpu_cores > 0 and cpu > 0) or (self.memory_mb > 0 and memory > 0) or (self.gpu_vram_mb > 0 and gpu > 0)

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "node_id": self.node_id,
            "start_at": self.start_at.isoformat(),
            "end_at": self.end_at.isoformat(),
            "priority": self.priority,
            "cpu_cores": self.cpu_cores,
            "memory_mb": self.memory_mb,
            "gpu_vram_mb": self.gpu_vram_mb,
            "slots": self.slots,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ResourceReservation:
        return cls(
            job_id=str(data["job_id"]),
            tenant_id=str(data.get("tenant_id", "default")),
            node_id=str(data["node_id"]),
            start_at=datetime.datetime.fromisoformat(str(data["start_at"])),
            end_at=datetime.datetime.fromisoformat(str(data["end_at"])),
            priority=int(str(data.get("priority", 50))),
            cpu_cores=float(str(data.get("cpu_cores", 0.0))),
            memory_mb=float(str(data.get("memory_mb", 0.0))),
            gpu_vram_mb=float(str(data.get("gpu_vram_mb", 0.0))),
            slots=int(str(data.get("slots", 1))),
        )


__all__ = ("ResourceReservation", "resolve_reservation_tenant_id")
