"""Validate render.yaml against JSON Schema with actionable error messages.

Usage:
    python -m mandi_rdd.scripts.validate_render_yaml [--schema SCHEMA.json] [render.yaml]

Returns exit code 0 on success, 1 on validation failure.
"""

import argparse
import json
import os
import sys
import traceback


def load_yaml(path: str) -> dict:
    """Load YAML file, using PyYAML or falling back to JSON."""
    try:
        import yaml
    except ImportError:
        print("::error::PyYAML not installed. Run: pip install pyyaml")
        sys.exit(1)

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"::error file={path}::Failed to parse YAML: {e}")
        sys.exit(1)

    if data is None:
        print(f"::error file={path}::File is empty or contains only comments")
        sys.exit(1)

    return data


def validate_with_schema(data: dict, schema_path: str | None) -> list[str]:
    """Validate loaded YAML data against the JSON Schema."""
    if schema_path is None:
        # jsonschema not available or schema file not found — fall back to basic checks
        errors: list[str] = []
        _check_basic(data, errors)
        return errors

    try:
        from jsonschema import validate as js_validate
        from jsonschema.exceptions import ValidationError as JsSchemaError
    except ImportError:
        print("::warning::jsonschema not installed; falling back to basic checks.")
        errors = []
        _check_basic(data, errors)
        return errors

    try:
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
    except Exception as e:
        print(f"::error file={schema_path}::Failed to load schema: {e}")
        sys.exit(1)

    errors: list[str] = []

    try:
        js_validate(instance=data, schema=schema)
    except JsSchemaError as e:
        path_str = " → ".join(str(p) for p in e.absolute_path) if e.absolute_path else "root"
        msg = str(e.message)

        if "required" in msg.lower():
            missing = _extract_required(e)
            if missing:
                errors.append(f"Missing required field '{missing}' at {path_str}")
            else:
                errors.append(f"Required field missing at {path_str}: {msg}")
        elif "enum" in msg.lower() and "anyOf" not in msg.lower():
            allowed = _extract_enum_values(e)
            errors.append(
                f"Invalid value at {path_str}. Must be one of: "
                + ", ".join(f"'{v}'" for v in allowed)
            )
        elif "type" in msg.lower() and "null" not in msg.lower():
            expected_type = _extract_type(e)
            errors.append(f"Wrong type at {path_str}. Expected {expected_type}.")
        else:
            errors.append(f"Validation error at {path_str}: {msg}")

    except Exception as e:
        errors.append(f"Unexpected validation error: {e}")

    return errors


def _extract_required(e) -> str:
    """Try to extract the missing property name from a jsonschema error."""
    try:
        import re
        m = re.search(r"'(\w+)'", str(e.message).split("required")[-1])
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _extract_enum_values(e) -> list[str]:
    """Try to extract allowed enum values from a jsonschema error."""
    try:
        import re
        matches = re.findall(r"'([^']+)'", str(e.message))
        return matches
    except Exception:
        return []


def _extract_type(e) -> str:
    """Try to extract the expected type from a jsonschema error."""
    try:
        import re
        m = re.search(r"is not of type '(\w+)'", str(e.message))
        if m:
            return m.group(1)
    except Exception:
        pass
    return "unknown"


def _check_basic(data: dict, errors: list[str]) -> None:
    """Basic structural checks when jsonschema library is not available."""
    services = data.get("services", [])
    if not services:
        errors.append("No 'services' defined in render.yaml")
        return

    for i, svc in enumerate(services):
        prefix = f"services[{i}]"
        if not svc.get("name"):
            errors.append(f"{prefix}: missing 'name'")
        if not svc.get("runtime"):
            errors.append(f"{prefix}: missing 'runtime'")
        if not svc.get("plan"):
            errors.append(f"{prefix}: missing 'plan'")
        if svc.get("runtime") == "python":
            if not svc.get("buildCommand"):
                errors.append(f"{prefix}: python runtime requires 'buildCommand'")
            if not svc.get("startCommand"):
                errors.append(f"{prefix}: python runtime requires 'startCommand'")
        if svc.get("type") == "web" and not svc.get("healthCheckPath"):
            errors.append(f"{prefix}: web service should have a 'healthCheckPath'")
        if svc.get("runtime") == "docker":
            if not svc.get("dockerfilePath"):
                errors.append(f"{prefix}: docker runtime requires 'dockerfilePath'")
            if svc.get("plan", "free") != "free" and not svc.get("disk"):
                errors.append(
                    f"{prefix}: non-free plan should have a 'disk' config for persistence"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate render.yaml against schema")
    parser.add_argument("yaml_path", nargs="?", default="render.yaml", help="Path to render.yaml")
    parser.add_argument("--schema", default="render.schema.json", help="Path to JSON Schema file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full error details")
    args = parser.parse_args()

    if not os.path.exists(args.yaml_path):
        print(f"::error::File not found: {args.yaml_path}")
        return 1

    # Determine schema path — fall back to None if missing
    schema_path: str | None = args.schema if os.path.exists(args.schema) else None
    if schema_path is None:
        print(f"::warning::Schema file not found: {args.schema} — falling back to basic checks")

    data = load_yaml(args.yaml_path)
    errors = validate_with_schema(data, schema_path)

    if not errors:
        services = data.get("services", [])
        print(f"render.yaml: valid — {len(services)} service(s)")
        for svc in services:
            name = svc.get("name", "?")
            rtype = svc.get("type", "?")
            runtime = svc.get("runtime", "?")
            plan = svc.get("plan", "?")
            has_disk = "yes" if svc.get("disk") else "no"
            print(f"  [OK] {name} ({rtype}, {runtime}, {plan}, disk={has_disk})")
        return 0
    else:
        print(f"render.yaml: FAILED — {len(errors)} error(s)")
        for err in errors:
            print(f"  ::error:: {err}")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"::error::Unexpected error: {e}")
        if "--verbose" in sys.argv or "-v" in sys.argv:
            traceback.print_exc()
        sys.exit(1)
