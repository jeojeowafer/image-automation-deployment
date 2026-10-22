
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


def git(args: list[str], repo: Path, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip()
        )
    return result.stdout.strip() if result.returncode == 0 else ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob(repo: Path, ref: str, path: str) -> str | None:
    """Return a file's Git blob ID at a commit/tag, or None if absent."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}:{path}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def file_git_history(
    repo: Path,
    commit: str,
    path: str,
    previous_tag: str | None,
) -> dict:
    """Describe the file's Git identity and change since the prior release."""
    blob = git_blob(repo, commit, path)
    if blob is None:
        raise RuntimeError(
            f"{path} is not tracked in Git at {commit}. "
            "Commit the file before releasing."
        )

    last_commit = git(
        ["log", "-1", "--format=%H", commit, "--", path],
        repo,
    )
    last_date = git(
        ["show", "-s", "--format=%cI", last_commit],
        repo,
    )

    changed = None
    if previous_tag is not None:
        old_blob = git_blob(repo, previous_tag, path)
        changed = old_blob != blob

    return {
        "git_blob": blob,
        "last_changed_commit": last_commit,
        "last_changed_date": last_date,
        "changed_from_previous_release": changed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a versioned customer deployment release."
    )
    parser.add_argument("--customer", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--deploy-to", required=True)
    args = parser.parse_args()

    customer = args.customer
    version = args.version

    # Prevent path traversal and malformed release tags.
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", customer):
        raise ValueError("Customer must be a simple folder name.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", version):
        raise ValueError("Version must be a simple version string.")

    repo = Path(
        git(["rev-parse", "--show-toplevel"], Path.cwd())
    ).resolve()

    status = git(["status", "--porcelain"], repo)
    if status:
        raise RuntimeError(
            "Git working tree is not clean. Commit intended changes "
            "or configure local exclusions for files you do not track.\n"
            + status
        )

    commit = git(["rev-parse", "HEAD"], repo)
    branch = git(["branch", "--show-current"], repo)
    remote = git(["config", "--get", "remote.origin.url"], repo)
    release_tag = f"release/{customer}/{version}"

    existing_tags = git(
        ["tag", "--list", release_tag], repo
    )
    if existing_tags:
        raise RuntimeError(
            f"Release tag already exists: {release_tag}. "
            "Choose a new release version."
        )

    # Identify the latest previous release tag for this customer.
    tags_output = git(
        [
            "tag",
            "--list",
            f"release/{customer}/*",
            "--sort=-version:refname",
        ],
        repo,
    )
    previous_tag = tags_output.splitlines()[0] if tags_output else None

    exe = repo / "app" / "main.exe"
    customer_dir = repo / "configs" / customer
    mask_dir = customer_dir / "Mask"

    if not exe.is_file():
        raise FileNotFoundError(f"Executable not found: {exe}")
    if not customer_dir.is_dir():
        raise FileNotFoundError(
            f"Customer folder not found: {customer_dir}"
        )
    if not mask_dir.is_dir():
        raise FileNotFoundError(f"Mask folder not found: {mask_dir}")

    # Retrieve actual file contents for Git LFS files.
    git(["lfs", "pull"], repo)

    with exe.open("rb") as file:
        if file.read(200).startswith(
            b"version https://git-lfs.github.com/spec/v1"
        ):
            raise RuntimeError(
                "Executable is an LFS pointer, not the real file. "
                "Check Git LFS access and download."
            )

    deploy_root = Path(args.deploy_to).expanduser().resolve()
    release_dir = deploy_root / customer / version

    if release_dir.exists():
        raise FileExistsError(
            f"Release already exists: {release_dir}"
        )

    release_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{version}.staging-",
            dir=release_dir.parent,
        )
    )

    try:
        (staging / "app").mkdir()
        shutil.copy2(exe, staging / "app" / "main.exe")
        shutil.copytree(
            customer_dir,
            staging / "configs" / customer,
        )

        all_files = []
        config_files = []
        mask_files = []

        for path in sorted(staging.rglob("*")):
            if not path.is_file():
                continue

            relative = path.relative_to(staging).as_posix()
            source_relative = relative

            if relative.startswith("configs/"):
                source = repo / Path(relative)
            else:
                source = repo / Path(relative)

            # Only source files committed at the release commit are valid.
            history = file_git_history(
                repo,
                commit,
                source_relative,
                previous_tag,
            )

            is_executable = relative == "app/main.exe"
            is_mask = (
                relative.startswith(f"configs/{customer}/Mask/")
            )
            is_config = (
                relative.startswith(f"configs/{customer}/")
                and not is_mask
                and path.suffix.lower() in {".yaml", ".yml"}
            )

            entry = {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                **history,
            }

            if is_executable:
                entry["component"] = "executable"
            elif is_mask:
                entry["component"] = "mask"
            elif is_config:
                entry["component"] = "configuration"
            else:
                entry["component"] = "support_file"

            all_files.append(entry)

            if is_config:
                config_files.append(entry)
            if is_mask:
                mask_files.append(entry)

        if not config_files:
            raise RuntimeError(
                f"No YAML configuration files found in {customer_dir}"
            )
        if not mask_files:
            raise RuntimeError(
                f"No files found under {mask_dir}"
            )

        exe_entry = next(
            item for item in all_files
            if item["path"] == "app/main.exe"
        )

        manifest = {
            "release_id": version,
            "customer": customer,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "release_tag": release_tag,
            "previous_release_tag": previous_tag,
            "source_control": {
                # "repository": remote,
                # "branch": branch,
                "git_commit": commit,
            },
            "application": {
                "file": "app/main.exe",
                "sha256": exe_entry["sha256"],
                "git_blob": exe_entry["git_blob"],
                "last_changed_commit": exe_entry[
                    "last_changed_commit"
                ],
                "last_changed_date": exe_entry[
                    "last_changed_date"
                ],
            },
            "configuration": {
                "directory": f"configs/{customer}",
                "files": config_files,
            },
            "masks": {
                "directory": f"configs/{customer}/Mask",
                "files": mask_files,
            },
            "files": all_files,
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

        # Publish the completed package without overwriting prior releases.
        staging.rename(release_dir)

    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Tag the exact Git commit represented by the release.
    try:
        git(
            [
                "tag",
                "-a",
                release_tag,
                commit,
                "-m",
                f"Release {version} for {customer}",
            ],
            repo,
        )
    except Exception as exc:
        print(
            f"WARNING: Package was created, but Git tagging failed: {exc}",
            file=sys.stderr,
        )
        print(f"Package: {release_dir}", file=sys.stderr)
        return 1

    print("\nRelease created successfully.")
    print(f"Customer       : {customer}")
    print(f"Version        : {version}")
    print(f"Git commit     : {commit}")
    print(f"Previous tag   : {previous_tag or 'None (first release)'}")
    print(f"Release tag    : {release_tag}")
    print(f"Configuration  : {len(config_files)} YAML files")
    print(f"Mask/support   : {len(mask_files)} mask files")
    print(f"Total files    : {len(all_files)}")
    print(f"Package        : {release_dir}")
    print(f"Manifest       : {release_dir / 'release.yaml'}")

    print(
        "\nTo publish the release tag to GitHub, run:\n"
        f'git push origin "{release_tag}"'
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
