from dataclasses import dataclass, field


@dataclass
class Event:
    kind: str                       # loitering | restricted | fight | theft
    key: str                        # used for de-duplication / cooldown
    message: str
    track_ids: list = field(default_factory=list)
