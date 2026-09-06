from __future__ import annotations

from pathlib import Path


def load_system_prompt(path: str, inline_override: str | None = None) -> str:
    """Load the default prompt without making process cwd a hidden requirement."""
    if inline_override and inline_override.strip():
        return inline_override.strip()

    requested = Path(path).expanduser()
    candidates = [requested]
    if not requested.is_absolute():
        package_root = Path(__file__).resolve().parent.parent
        candidates.append(package_root / requested)

    for candidate in candidates:
        if candidate.is_file():
            prompt = candidate.read_text(encoding="utf-8").strip()
            if not prompt:
                raise ValueError(f"System prompt is empty: {candidate}")
            return prompt
    attempted = ", ".join(str(item) for item in candidates)
    raise FileNotFoundError(f"System prompt not found; tried: {attempted}")
