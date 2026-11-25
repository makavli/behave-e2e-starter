#!/usr/bin/env python3
"""One entry point for laptops and both pipelines: venv, inventory check, framework self-tests, behave.

  python ci/run.py                           every scenario, against the local simulators
  python ci/run.py --env dev --tags @smoke   smoke scenarios against config/dev.toml
  python ci/run.py --inventory               only list and check the scenario inventory
  python ci/run.py -- --stop -D timeout_s=300  anything after -- goes to behave

  python ci/run.py --secrets                 first read the config's [secrets] from OpenBao (ops/openbao)
  python ci/run.py --publish NAME            upload content/NAME to your channel and keep it (scheduled when its
                                             meta.toml has publish_at): --secrets --tags @publish in one word

E2E_ENV, E2E_TAGS and E2E_SECRETS give the defaults for --env, --tags and --secrets (the pipeline sets them).
Reports land in reports/: inventory.md and junit/*.xml.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
REPORTS = ROOT / "reports"


def run(*cmd, env=None):
    print("+", " ".join(map(str, cmd)), flush=True)
    return subprocess.call([str(c) for c in cmd], cwd=ROOT, env={**os.environ, **(env or {}), "PYTHONUTF8": "1"})


def venv_python():
    """The repo's own .venv, (re)installed whenever requirements.txt changes."""
    python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    stamp = VENV / "installed-requirements.txt"
    wanted = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    if python.exists() and stamp.exists() and stamp.read_text(encoding="utf-8") == wanted:
        return python
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(VENV)
    if run(python, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "-r", ROOT / "requirements.txt"):
        sys.exit("pip install failed")
    stamp.write_text(wanted, encoding="utf-8")
    return python


def load_secrets(env):
    """The config's [secrets], for behave's environment only (not pip's, not the self-tests'). On an Azure agent
    each value is first registered with task.setsecret: the agent then masks it in every log line that follows."""
    sys.path.insert(0, str(ROOT))
    from e2e import config, secrets
    try:
        found = secrets.fetch(config.load(env))
    except config.ConfigError as exc:
        sys.exit(f"secrets: {exc}")
    for name, value in found.items():
        if os.environ.get("TF_BUILD"):
            print("\n".join(secrets.mask_commands(value)), flush=True)
        print(f"secret {name}: read from OpenBao ({len(value)} characters)", flush=True)
    return found


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=os.environ.get("E2E_ENV") or "local", help="config/<env>.toml (default: local)")
    parser.add_argument("--tags", default=os.environ.get("E2E_TAGS", ""), help='e.g. "@smoke and not @mft"')
    parser.add_argument("--secrets", action="store_true",
                        default=os.environ.get("E2E_SECRETS", "").lower() in ("1", "true"),
                        help="read the config's [secrets] from OpenBao into the environment first")
    parser.add_argument("--inventory", action="store_true", help="only list and check the inventory")
    parser.add_argument("--publish", metavar="NAME",
                        help="upload content/NAME to your channel and keep it: --secrets --tags @publish in one word")
    parser.add_argument("behave_args", nargs="*", help="extra behave arguments, after --")
    args = parser.parse_args(argv)
    if args.publish:
        args.secrets, args.tags = True, args.tags.strip() or "@publish"
    return args


def uploaded(junit=REPORTS / "junit"):
    """True when behave ran the publish scenario. Skipped (no refresh token stored yet), the run would end green."""
    result = junit / "TESTS-api.youtube_publish.xml"  # behave names it after features/api/youtube_publish.feature
    return result.is_file() and ' skipped="0"' in result.read_text(encoding="utf-8")


def main():
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or newer is needed (tomllib)")
    if struct.calcsize("P") == 4:  # 32-bit: no wheels for paramiko's crypto, pip falls back to a Rust build
        sys.exit(f"a 64-bit Python is needed, this one is 32-bit ({sys.executable}); on Windows: py -3-64 ci/run.py")
    args = options()

    found = load_secrets(args.env) if args.secrets and not args.inventory else {}
    python = venv_python()
    REPORTS.mkdir(exist_ok=True)
    rc = run(python, "-m", "e2e.inventory", "--out", REPORTS / "inventory.md", *(["--print"] if args.inventory else []))
    if rc or args.inventory:
        return rc
    if run(python, "-m", "unittest", "discover", "-s", "tests", "-t", "."):
        return 1
    for old in ("junit", "allure-results"):  # never publish an earlier run's results
        shutil.rmtree(REPORTS / old, ignore_errors=True)
    cmd = [python, "-m", "behave", "-D", f"env={args.env}", "--junit", "--junit-directory", REPORTS / "junit",
           "-f", "allure_behave.formatter:AllureFormatter", "-o", REPORTS / "allure-results", "-f", "pretty"]
    tags = args.tags.strip()
    if tags:  # behave.ini's default_tags only apply when no --tags is given: keep them unless the tag is asked for
        for held in ("@wip", "@keep"):  # @keep leaves what it made in place (an upload): only when named
            if held not in tags:
                tags = f"({tags}) and not {held}"
        cmd += ["--tags", tags]
    rc = run(*cmd, *args.behave_args, env={**found, **({"E2E_PUBLISH": args.publish} if args.publish else {})})
    if args.publish and not rc and not uploaded():
        print(f"--publish {args.publish}: nothing was uploaded, the publish scenario was skipped. It needs your "
              "consent stored in OpenBao: python ops/openbao/google_oauth.py behave/youtube-oauth", flush=True)
        rc = 1
    if found:  # load_secrets put the repo on sys.path
        from e2e import secrets
        for path in secrets.redact(REPORTS, found.values()):
            print(f"a secret value was in {path.relative_to(ROOT)}: replaced with ***", flush=True)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
