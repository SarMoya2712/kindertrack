from dataclasses import dataclass
from typing import Protocol

@dataclass
class LivenessResult:
    status: str
    score: float | None = None

class LivenessProvider(Protocol):
    def check(self, image, face) -> LivenessResult: ...

class UnavailableLiveness:
    """Explicit extension point; image quality is not proof of liveness."""
    def check(self, image, face):
        return LivenessResult('unavailable')
