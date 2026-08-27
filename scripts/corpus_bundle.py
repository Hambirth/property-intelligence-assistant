"""Create and verify the private, immutable corpus release artifact."""

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

CORPUS_SCHEMA_VERSION = 1
EXPECTED_DOCUMENTS = {"darglobal": 10, "wasalt": 10}
EXPECTED_CHUNKS = 212
MAX_BUNDLE_BYTES = 500_000_000
MAX_MEMBER_BYTES = 50_000_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 500_000_000
EXPECTED_MEMBER_COUNT = 1 + (sum(EXPECTED_DOCUMENTS.values()) * 2)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_release_files(import_root: Path) -> list[Path]:
    files: list[Path] = []
    for source, suffix in (("darglobal", ".pdf"), ("wasalt", ".html")):
        source_root = import_root / source
        if source_root.is_symlink() or not source_root.is_dir():
            raise ValueError(f"Missing or unsafe corpus source directory: {source_root}")
        payloads = sorted(source_root.glob(f"*{suffix}"))
        if len(payloads) != EXPECTED_DOCUMENTS[source]:
            raise ValueError(
                f"Expected {EXPECTED_DOCUMENTS[source]} {source} payloads, found {len(payloads)}"
            )
        for payload in payloads:
            sidecar = Path(f"{payload}.metadata.json")
            for candidate in (payload, sidecar):
                if candidate.is_symlink() or not candidate.is_file():
                    raise ValueError(f"Missing or unsafe corpus file: {candidate}")
                if candidate.stat().st_size > MAX_MEMBER_BYTES:
                    raise ValueError(f"Corpus file exceeds {MAX_MEMBER_BYTES} bytes: {candidate}")
                files.append(candidate)
    return sorted(files, key=lambda path: path.relative_to(import_root).as_posix())


def create_bundle(import_root: Path, output: Path, version: str) -> dict[str, Any]:
    files = collect_release_files(import_root)
    manifest_files = []
    payloads: dict[str, bytes] = {}
    for path in files:
        relative = path.relative_to(import_root).as_posix()
        data = path.read_bytes()
        payloads[relative] = data
        manifest_files.append({"path": relative, "sha256": sha256_bytes(data), "size": len(data)})

    manifest = {
        "acquisition_method": "MANUAL_PUBLIC_IMPORT",
        "corpus_version": version,
        "expected_chunks": EXPECTED_CHUNKS,
        "expected_documents": EXPECTED_DOCUMENTS,
        "files": manifest_files,
        "schema_version": CORPUS_SCHEMA_VERSION,
    }
    manifest_data = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    with (
        temporary.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        _add_bytes(archive, "corpus-manifest.json", manifest_data)
        for relative in sorted(payloads):
            _add_bytes(archive, relative, payloads[relative])
    temporary.replace(output)
    return {
        "bundle": str(output),
        "bundle_sha256": sha256_file(output),
        "documents": sum(EXPECTED_DOCUMENTS.values()),
        "files": len(files),
        "version": version,
    }


def verify_bundle(bundle: Path, extract_to: Path | None = None) -> dict[str, Any]:
    if bundle.is_symlink() or not bundle.is_file() or bundle.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("Corpus bundle is missing, symlinked, or oversized")
    members: dict[str, bytes] = {}
    total_uncompressed = 0
    with tarfile.open(bundle, mode="r:gz") as archive:
        archive_members = archive.getmembers()
        if len(archive_members) != EXPECTED_MEMBER_COUNT:
            raise ValueError("Corpus bundle has an unexpected member count")
        for member in archive_members:
            relative = PurePosixPath(member.name)
            if (
                not member.isfile()
                or relative.is_absolute()
                or ".." in relative.parts
                or member.size > MAX_MEMBER_BYTES
                or member.name in members
            ):
                raise ValueError(f"Unsafe corpus bundle member: {member.name}")
            total_uncompressed += member.size
            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise ValueError("Corpus bundle expands beyond the allowed size")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"Unreadable corpus bundle member: {member.name}")
            members[member.name] = extracted.read(MAX_MEMBER_BYTES + 1)

    manifest_data = members.pop("corpus-manifest.json", None)
    if manifest_data is None or len(manifest_data) > 64_000:
        raise ValueError("Corpus manifest is missing or oversized")
    manifest = json.loads(manifest_data)
    if (
        manifest.get("schema_version") != CORPUS_SCHEMA_VERSION
        or manifest.get("expected_documents") != EXPECTED_DOCUMENTS
        or manifest.get("expected_chunks") != EXPECTED_CHUNKS
        or manifest.get("acquisition_method") != "MANUAL_PUBLIC_IMPORT"
    ):
        raise ValueError("Corpus manifest contract does not match this release")

    declared_items = manifest.get("files")
    if not isinstance(declared_items, list) or len(declared_items) != len(members):
        raise ValueError("Corpus manifest has an invalid file list")
    declared: dict[str, dict[str, Any]] = {}
    for item in declared_items:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise TypeError("Corpus manifest has an invalid file entry")
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts or item["path"] in declared:
            raise ValueError("Corpus manifest has an unsafe or duplicate path")
        declared[item["path"]] = item
    if set(declared) != set(members):
        raise ValueError("Corpus bundle members do not match the manifest")
    for relative, data in members.items():
        item = declared[relative]
        if item.get("size") != len(data) or item.get("sha256") != sha256_bytes(data):
            raise ValueError(f"Corpus bundle hash mismatch: {relative}")

    if extract_to is not None:
        if extract_to.exists() and any(extract_to.iterdir()):
            raise ValueError("Extraction directory must be absent or empty")
        extract_to.mkdir(parents=True, exist_ok=True)
        for relative, data in members.items():
            target = extract_to.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (extract_to / "corpus-manifest.json").write_bytes(manifest_data)

    return {
        "bundle_sha256": sha256_file(bundle),
        "documents": sum(EXPECTED_DOCUMENTS.values()),
        "files": len(members),
        "valid": True,
        "version": manifest["corpus_version"],
    }


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    archive.addfile(info, io.BytesIO(data))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--input", type=Path, default=Path("data/import"))
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--version", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--extract", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_bundle(args.input, args.output, args.version)
        else:
            result = verify_bundle(args.bundle, args.extract)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
