
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

def last_changed_commit(repo: Path, relative_path: str) -> str:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", relative_path],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def run_git(args: list[str], repo: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and deploy a versioned customer release."
    )
    parser.add_argument("--customer", required=True,
                        help="Folder name under configs/")
    parser.add_argument("--version", required=True,
                        help="Release version, e.g. 1.2.0")
    parser.add_argument("--deploy-to", required=True,
                        help="Destination root outside the Git repository")
    args = parser.parse_args()

    # Run this script from inside the repository.
    repo = Path(run_git(["rev-parse", "--show-toplevel"], Path.cwd()))
    customer = args.customer
    version = args.version

    # Validate required source files and folders.
    exe = repo / "app" / "main.exe"
    customer_config = repo / "configs" / customer
    mask_dir = customer_config / "Mask"

    if not exe.is_file():
        raise FileNotFoundError(f"Executable not found: {exe}")
    if not customer_config.is_dir():
        raise FileNotFoundError(
            f"Customer configuration folder not found: {customer_config}"
        )
    if not mask_dir.is_dir():
        raise FileNotFoundError(f"Mask folder not found: {mask_dir}")

    # Require a committed, reproducible source state.
    status = run_git(["status", "--porcelain"], repo)
    if status:
        raise RuntimeError(
            "Working tree is not clean. Commit or safely stash your changes "
            "before creating a release."
        )

    commit = run_git(["rev-parse", "HEAD"], repo)
    branch = run_git(["branch", "--show-current"], repo)

    # Download the real contents of Git LFS files.
    run_git(["lfs", "pull"], repo)

    # A missing LFS download can leave a small pointer file in place.
    with exe.open("rb") as file:
        if file.read(200).startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise RuntimeError(
                "app/main.exe is still an LFS pointer. Check Git LFS download."
            )

    deploy_root = Path(args.deploy_to).expanduser().resolve()
    release_dir = deploy_root / customer / version

    if release_dir.exists():
        raise FileExistsError(
            f"Release already exists: {release_dir}\n"
            "Choose a new version to preserve existing releases."
        )

    release_dir.parent.mkdir(parents=True, exist_ok=True)

    # Stage everything beside the destination, then rename when complete.
    staging = Path(tempfile.mkdtemp(
        prefix=f".{version}.staging-",
        dir=release_dir.parent,
    ))

    try:
        (staging / "app").mkdir()
        shutil.copy2(exe, staging / "app" / "main.exe")

        shutil.copytree(
            customer_config,
            staging / "configs" / customer,
        )

        # Inventory and checksum every delivered file.
        files = []
        for path in sorted(staging.rglob("*")):
            if not path.is_file():
                continue

            relative = path.relative_to(staging).as_posix()
            is_mask = "/Mask/" in f"/{relative}/"

            files.append({
                "path": relative,
                "component": (
                    "executable" if relative == "app/main.exe"
                    else "mask" if is_mask
                    else "configuration_or_support_file"
                ),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })

        manifest = {
            "release_id": version,
            "customer": customer,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "last_changed_commit": last_changed_commit(repo, relative),
            "source_control": {
                "repository": run_git(
                    ["config", "--get", "remote.origin.url"], repo
                ),
                "branch": branch,
                "git_commit": commit,
            },
            "application": {
                "file": "app/main.exe",
                "sha256": sha256_file(staging / "app" / "main.exe"),
            },
            "configuration": {
                "directory": f"configs/{customer}",
            },
            "masks": {
                "directory": f"configs/{customer}/Mask",
            },
            "files": files,
        }

        with (staging / "release.yaml").open(
            "w", encoding="utf-8", newline="\n"
        ) as file:
            yaml.safe_dump(
                manifest,
                file,
                sort_keys=False,
                allow_unicode=True,
            )

        # Publish the complete versioned release.
        staging.rename(release_dir)

    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    print("\nRelease created and deployed successfully.")
    print(f"Customer : {customer}")
    print(f"Version  : {version}")
    print(f"Git SHA  : {commit}")
    print(f"Location : {release_dir}")
    print(f"Manifest : {release_dir / 'release.yaml'}")
    print(f"Files    : {len(files)}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
