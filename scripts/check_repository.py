"""Cheap repository hygiene checks shared by developers and CI."""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from installer.common import ROOT, source_files


def main():
    errors = []
    required = ["AGENTS.md", "VERSION", "install.py", "docs/INSTALLATION.md", "docs/DEVELOPMENT.md",
        "docs/ARCHITECTURE.md", "docs/SPEECH_PIPELINE.md", "docs/RELEASES.md"]
    for name in required:
        if not (ROOT / name).is_file():
            errors.append(f"Missing {name}")
    for path in source_files():
        if path.suffix not in {".py", ".json", ".md", ".sh", ".toml", ".txt"}:
            continue
        text = path.read_text()
        if "-----BEGIN PRIVATE KEY-----" in text or "-----BEGIN RSA PRIVATE KEY-----" in text:
            errors.append(f"Private key embedded in {path.relative_to(ROOT)}")
        if path.suffix == ".md":
            for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                link = (path.parent / target.split("#")[0]).resolve()
                if not link.exists():
                    errors.append(f"Broken documentation link: {path.relative_to(ROOT)} -> {target}")
    if list((ROOT / "scripts").glob("package_*")):
        errors.append("Release-specific packaging scripts must not return")
    import ast
    import tomllib
    package_version = tomllib.loads((ROOT / "services/voice-backend/pyproject.toml").read_text())["project"]["version"]
    module = ast.parse((ROOT / "services/voice-backend/voice_agent/__init__.py").read_text())
    versions = [n.value.value for n in module.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__version__" for t in n.targets)]
    if versions != [package_version]:
        errors.append("Backend runtime version and pyproject.toml disagree")
    if errors:
        raise SystemExit("\n".join(errors))
    print("Repository structure, deployable source and documentation links: PASS")


if __name__ == "__main__":
    main()
