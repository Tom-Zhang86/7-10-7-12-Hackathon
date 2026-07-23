import argparse
import json
from pathlib import Path
import shlex
import stat
import sys


HOST_NAME = "com.ai_desk.activity"


def install(
    project_root: Path,
    database_path: Path,
    extension_id: str,
    base_dir: Path | None = None,
) -> tuple[Path, Path]:
    root = base_dir or (
        Path.home() / "Library" / "Application Support" / "AI Desk"
    )
    bin_dir = root / "bin"
    manifest_dir = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Google"
        / "Chrome"
        / "NativeMessagingHosts"
        if base_dir is None
        else root / "NativeMessagingHosts"
    )
    bin_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    runner = bin_dir / "ai-desk-native-host"
    runner.write_text(
        "#!/bin/sh\n"
        f"export PYTHONPATH={shlex.quote(str(project_root))}\n"
        f"exec {shlex.quote(sys.executable)} -m application.browser.native_host "
        f"--db {shlex.quote(str(database_path))}\n",
        encoding="utf-8",
    )
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR)

    manifest = manifest_dir / f"{HOST_NAME}.json"
    manifest.write_text(
        json.dumps(
            {
                "name": HOST_NAME,
                "description": "AI Desk local activity bridge",
                "path": str(runner.resolve()),
                "type": "stdio",
                "allowed_origins": [f"chrome-extension://{extension_id}/"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return runner, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("extension_id")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.cwd() / "ai_desk_presence.db",
    )
    args = parser.parse_args()
    runner, manifest = install(
        args.project_root.resolve(),
        args.db.resolve(),
        args.extension_id,
    )
    print(f"Installed runner: {runner}")
    print(f"Installed manifest: {manifest}")


if __name__ == "__main__":
    main()
