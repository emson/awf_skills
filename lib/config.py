"""Layered credential resolution (A6).

For each key, first match wins, in this order:
  1. process environment
  2. <project_root>/.env  (if a project root is given)
  3. $AWF_HOME/.env
  4. ~/.config/awf/.env

`Config.layered(...)` returns a resolver. `cfg.get(key)` returns the
value; `cfg.source(key)` returns the layer it came from. `awf-doctor`
uses `source()` to print where each variable resolved from.

Standard-library only (the suite assumes uv-script handles deps for the
*caller*, not the resolver itself).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        out[key] = val
    return out


@dataclass
class Layer:
    name: str
    path: Path | None  # None for process env
    values: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    layers: list[Layer]

    @classmethod
    def layered(
        cls,
        *,
        project_root: Path | None = None,
        awf_home: Path | None = None,
    ) -> "Config":
        layers: list[Layer] = [Layer(name="env", path=None, values=dict(os.environ))]
        if project_root is not None:
            p = project_root / ".env"
            layers.append(Layer(name="project", path=p, values=_parse_env_file(p)))
        if awf_home is not None:
            p = awf_home / ".env"
            layers.append(Layer(name="awf_home", path=p, values=_parse_env_file(p)))
        user = Path("~/.config/awf/.env").expanduser()
        layers.append(Layer(name="user", path=user, values=_parse_env_file(user)))
        return cls(layers=layers)

    def get(self, key: str, default: str | None = None) -> str | None:
        for layer in self.layers:
            if key in layer.values and layer.values[key] != "":
                return layer.values[key]
        return default

    def source(self, key: str) -> str | None:
        """Which layer produced the value, or None if absent."""
        for layer in self.layers:
            if key in layer.values and layer.values[key] != "":
                if layer.path is None:
                    return "env"
                return f"{layer.name}:{layer.path}"
        return None

    def require(self, key: str) -> str:
        v = self.get(key)
        if v is None:
            raise KeyError(f"Required credential not set: {key}")
        return v
