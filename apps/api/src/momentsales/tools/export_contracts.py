"""Write the committed API contracts (ADR-0009):

- docs/openapi.json                               (OpenAPI spec)
- packages/shared/src/generated/permissions.json  (role → action matrix for UI gating)

Run:  uv run python -m momentsales.tools.export_contracts      (write)
      uv run python -m momentsales.tools.export_contracts --check  (CI drift check)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[5]
OPENAPI_PATH = REPO / "docs" / "openapi.json"
PERMISSIONS_PATH = REPO / "packages" / "shared" / "src" / "generated" / "permissions.json"


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build() -> dict[Path, str]:
    from momentsales.core.config import Env, ProviderMode, Settings
    from momentsales.main import create_app
    from momentsales.tenancy.permissions import export_matrix

    # Spec is generated without dev-only routes, as production would serve it.
    app = create_app(Settings(env=Env.LOCAL, provider_mode=ProviderMode.LIVE))
    return {OPENAPI_PATH: _dump(app.openapi()), PERMISSIONS_PATH: _dump(export_matrix())}


def main(argv: list[str]) -> int:
    outputs = build()
    if "--check" in argv:
        stale = [
            str(p.relative_to(REPO))
            for p, content in outputs.items()
            if not p.exists() or p.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("Contracts out of date; run export_contracts:", ", ".join(stale))
            return 1
        print("Contracts up to date.")
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
