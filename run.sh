#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p results logs
rm -f results/*.json results/*.jsonl logs/*.txt

PYTHON="${PYTHON:-python3}"
"$PYTHON" repro/repro.py | tee logs/baseline.txt
"$PYTHON" fixed/idempotent_resume.py | tee logs/idempotent.txt
"$PYTHON" fixed/structured_error_handler.py | tee logs/error-handler.txt

"$PYTHON" - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
def last_json(path):
    text = path.read_text(encoding="utf-8")
    return json.loads(text[text.index("{"):])
baseline = last_json(root / "logs/baseline.txt")
idempotent = last_json(root / "logs/idempotent.txt")
handler = last_json(root / "logs/error-handler.txt")
summary = {
    "baseline_reproduced": baseline["reproduced"],
    "idempotent_contained": idempotent["contained"],
    "structured_error_handler": handler["structured"],
    "result": "PASS" if all([baseline["reproduced"], idempotent["contained"], handler["structured"]]) else "FAIL",
}
(root / "results/verification.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2))
PY
