"""Environment config: config/<env>.toml, with ${VAR} / ${VAR:-default} filled from environment variables.

Sections are resolved when first used, so a run of @api scenarios needs only the API's secrets.
"""
import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(Exception):
    """The environment's config is missing or wrong: fix config/<env>.toml or set the variable."""


def load(env):
    path = ROOT / "config" / f"{env}.toml"
    if not path.is_file():
        raise ConfigError(f"no config for environment '{env}': create {path} from config/example.toml")
    with path.open("rb") as f:
        return Config(tomllib.load(f), env)


def resolve(value):
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v) for v in value]
    if not isinstance(value, str):
        return value

    def sub(match):
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise ConfigError(f"environment variable {name} is not set")

    return _VAR.sub(sub, value)


def need(section, section_name, key):
    value = section.get(key)
    if value in (None, ""):
        raise ConfigError(f"[{section_name}] {key} is required")
    return value


class Section(Mapping):
    """One config section; each value is resolved when read, so an unused key's unset variable never matters."""

    def __init__(self, raw):
        self._raw = raw

    def __getitem__(self, key):
        return resolve(self._raw[key])

    def __iter__(self):
        return iter(self._raw)

    def __len__(self):
        return len(self._raw)


class Config:
    def __init__(self, raw, name):
        self.raw, self.name = raw, name

    def section(self, name, required=True):
        if name not in self.raw:
            if required:
                raise ConfigError(f"[{name}] is missing from config/{self.name}.toml")
            return Section({})
        return Section(self.raw[name])

    def has(self, dotted):
        """True when the key resolves to a value, e.g. has("mft.archive"): drives the @requires.<key> tags.

        "${X:-}" (empty) counts as not set; "${X}" with X unset raises, because that is a broken config.
        """
        node = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return resolve(node) not in (None, "", False)
