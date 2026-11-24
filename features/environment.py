"""behave hooks: pick the environment, start the simulators for local runs, isolate and clean up each scenario.

    behave -D env=dev            config/dev.toml (default: $E2E_ENV, else local)
    behave -D timeout_s=300      override [run] timeout_s for slow landscapes
"""
import logging
import os
import sys

import parse
from behave import register_type

from e2e import config
from e2e.session import Session, new_vars

PATTERN_SECTIONS = ("api", "mft", "idoc", "workato")


@parse.with_pattern(r'[^"]*')
def _quoted(text):
    return text


register_type(S=_quoted)  # {name:S} = anything between double quotes, never across them

# A legacy console code page (Windows cp1252) must not crash the run on non-ASCII scenario text.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


def before_all(context):
    userdata = context.config.userdata
    cfg = config.load(userdata.get("env") or os.environ.get("E2E_ENV") or "local")
    context.sims = None
    if cfg.raw.get("run", {}).get("simulators"):
        from e2e.sim import Simulators
        context.sims = Simulators(cfg.raw)
    run = cfg.section("run", required=False)
    context.e2e = Session(cfg, timeout_s=float(userdata.get("timeout_s", run.get("timeout_s", 60))),
                          poll_s=float(userdata.get("poll_s", run.get("poll_s", 2))))
    logging.getLogger("paramiko").setLevel(logging.WARNING)


def before_scenario(context, scenario):
    cfg = context.e2e.cfg
    for tag in scenario.effective_tags:
        if tag in PATTERN_SECTIONS and tag not in cfg.raw:
            return scenario.skip(f"no [{tag}] section in config/{cfg.name}.toml")
        if tag.startswith("requires.") and not cfg.has(tag.removeprefix("requires.")):
            return scenario.skip(f"needs '{tag.removeprefix('requires.')}' in config/{cfg.name}.toml")
    context.vars = new_vars()
    context.cleanups = []


def after_scenario(context, scenario):
    for cleanup in reversed(getattr(context, "cleanups", [])):
        try:
            cleanup()
        except Exception as exc:  # cleanup is best effort; the scenario's own result stands
            logging.warning("cleanup failed: %s", exc)
    context.e2e.close()


def after_all(context):
    if getattr(context, "sims", None):
        context.sims.stop()
