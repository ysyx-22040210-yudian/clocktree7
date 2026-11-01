import json
from pathlib import Path
import sys

actual = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
observed_edges = sorted([e["source"], e["source_pin"], e["target"], e["target_pin"]] for e in actual["edges"])
failures = []
for name, observed in [("nodes", sorted(n["id"] for n in actual["nodes"])), ("edges", observed_edges),
                       ("terminals", sorted(n["id"] for n in actual["nodes"] if n["terminal"]))]:
    if observed != expected[name]:
        failures.append({"field": name, "expected": len(expected[name]), "observed": len(observed),
                         "missing": [x for x in expected[name] if x not in observed][:20],
                         "extra": [x for x in observed if x not in expected[name]][:20]})
if not actual["summary"]["connectivity_complete"]:
    failures.append({"field": "completeness", "diagnostics": actual["diagnostics"][:20], "unresolved": actual["summary"]["unresolved"]})
print(json.dumps({"pass": not failures, "summary": actual["summary"], "failures": failures}, indent=2))
raise SystemExit(1 if failures else 0)
