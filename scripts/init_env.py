"""Create local secrets without writing them to logs or terminal output."""

from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if target.exists():
    print(".env already exists; preserved existing configuration.")
else:
    template = (root / ".env.example").read_text(encoding="utf-8")
    template = template.replace(
        "POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n"
    )
    target.write_text(template, encoding="utf-8")
    print("Created .env with random local credentials. Keep this file private.")
