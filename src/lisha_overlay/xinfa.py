"""Confirmed specialization labels for spoken commands."""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path


ASSETS = Path(__file__).parent / 'assets' / 'xinfa'


@dataclass(frozen=True, slots=True)
class Specialization:
    name: str
    nickname: str
    school: str
    role: str
    ranged: bool


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Specialization]:
    content = json.loads((ASSETS / 'catalog.json').read_text(encoding='utf-8'))
    return {code: Specialization(**row) for code, row in content['specializations'].items()}


def guide_cost(player) -> int:
    if player.is_tank:
        return 5
    spec = catalogue().get(player.xinfa) if player.xinfa else None
    if spec is None:
        return 2
    if spec.role == 'healer' or spec.ranged:
        return 0
    return 4 if spec.role == 'tank' else 3
