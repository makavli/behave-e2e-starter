"""The scenario inventory: every scenario with its pattern, suite and needs, plus a tag check.

    python -m e2e.inventory [--out reports/inventory.md]

Each scenario needs exactly one pattern tag and one suite tag; the exit code is 1 when one does not.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

from behave.parser import parse_file

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = ("api", "mft", "idoc", "workato", "e2e")
SUITES = ("smoke", "regression")


def scan(features_dir):
    features_dir = Path(features_dir)
    rows, problems = [], []
    for path in sorted(features_dir.rglob("*.feature")):
        feature = parse_file(str(path))
        rel = path.relative_to(features_dir.parent).as_posix()
        for scenario in feature.walk_scenarios():
            tags = {str(t) for t in scenario.effective_tags}
            pattern = [p for p in PATTERNS if p in tags]
            suite = [s for s in SUITES if s in tags]
            where = f"{rel}:{scenario.line}"
            if len(pattern) != 1:
                problems.append(f"{where} '{scenario.name}': needs one pattern tag of @{', @'.join(PATTERNS)}")
            if len(suite) != 1:
                problems.append(f"{where} '{scenario.name}': needs one suite tag of @{', @'.join(SUITES)}")
            rows.append({"pattern": ",".join(pattern) or "?", "suite": ",".join(suite) or "?",
                         "negative": "negative" in tags, "name": scenario.name, "where": where,
                         "needs": sorted(t.removeprefix("requires.") for t in tags if t.startswith("requires."))})
    return rows, problems


def markdown(rows):
    counts = Counter(row["pattern"] for row in rows)
    lines = ["# Scenario inventory", "",
             " · ".join(f"{p}: {counts[p]}" for p in PATTERNS if counts[p]) + f" · total: {len(rows)}", "",
             "| Pattern | Suite | Neg. | Scenario | Needs config | Location |", "|---|---|---|---|---|---|"]
    for row in sorted(rows, key=lambda r: (PATTERNS.index(r["pattern"]) if r["pattern"] in PATTERNS else 99, r["where"])):
        lines.append(f"| {row['pattern']} | {row['suite']} | {'yes' if row['negative'] else ''} | "
                     f"{row['name'].replace('|', '/')} | {', '.join(row['needs'])} | {row['where']} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="also write the inventory as Markdown here")
    parser.add_argument("--print", action="store_true", help="print the whole table, not just the counts")
    args = parser.parse_args(argv)
    rows, problems = scan(ROOT / "features")
    counts = Counter(row["pattern"] for row in rows)
    print(markdown(rows) if args.print else
          "inventory: " + ", ".join(f"{p} {counts[p]}" for p in PATTERNS) + f" | total {len(rows)}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown(rows), encoding="utf-8")
    for problem in problems:
        print("UNCLASSIFIED", problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
