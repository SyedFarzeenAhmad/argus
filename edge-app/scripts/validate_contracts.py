"""Validates the edge app's sample messages against contracts/schemas/.

Run after `./gradlew testDebugUnitTest`, which writes app/build/contract-samples/.
    uv run --with jsonschema --with referencing python scripts/validate_contracts.py
"""
import json
import pathlib
import sys

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

here = pathlib.Path(__file__).resolve().parent.parent
schemas = here.parent / "contracts" / "schemas"
samples = here / "app" / "build" / "contract-samples"

registry = Registry()
for p in schemas.glob("*.json"):
    doc = json.loads(p.read_text())
    res = Resource.from_contents(doc)
    registry = registry.with_resource(doc["$id"], res).with_resource(p.name, res)

which = {"observation": "observation.schema.json", "telemetry": "telemetry.schema.json"}
failed = 0
for s in sorted(samples.glob("*.json")):
    schema_name = next(v for k, v in which.items() if s.name.startswith(k))
    schema = json.loads((schemas / schema_name).read_text())
    v = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    errors = list(v.iter_errors(json.loads(s.read_text())))
    print(("FAIL " if errors else "ok   ") + s.name)
    for e in errors:
        print("     ", list(e.path), e.message)
    failed += bool(errors)
sys.exit(1 if failed else 0)
