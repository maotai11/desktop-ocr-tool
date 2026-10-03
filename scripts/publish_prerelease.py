"""Publish one approved candidate from same-run, hash-bound Windows evidence.

This program is deliberately specific to v1.6.2-rc.2. Merely preparing files
does not use credentials or network. The CLI can publish only on the explicitly
marked push in the fixed repository/branch; the workflow owns token permissions.
GitHub REST references: https://docs.github.com/en/rest/releases/releases and
https://docs.github.com/en/rest/releases/assets.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping
from urllib import error, parse, request
import zipfile


REPOSITORY = "maotai11/desktop-ocr-tool"
BRANCH_REF = "refs/heads/fix/offline-release-hardening"
TAG = "v1.6.2-rc.2"
VERSION = TAG[1:]
PUBLISH_MARKER = "publish-prerelease: " + TAG
API_BASE = "https://api.github.com/repos/" + REPOSITORY
API_VERSION = "2026-03-10"
EXE_NAME = "DesktopOCRTool-" + TAG + ".exe"
ZIP_NAME = "DesktopOCRTool-" + TAG + ".zip"
CHECKSUM_NAME = "DesktopOCRTool-" + TAG + "-SHA256SUMS.txt"
METADATA_NAME = "DesktopOCRTool-" + TAG + "-validation-metadata.zip"
MAX_JSON_BYTES = 4 * 1024 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class PublicationError(RuntimeError):
    """A fail-closed publication or provenance check."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicationError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), "Missing or redirected JSON evidence")
    require(path.stat().st_size <= MAX_JSON_BYTES, "JSON evidence is too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise PublicationError("Invalid JSON evidence") from exc
    require(isinstance(value, dict), "JSON evidence must be an object")
    return value


@dataclass(frozen=True)
class RunnerContext:
    source_commit: str
    run_id: str
    run_attempt: str


def validate_runner_context(environment: Mapping[str, str]) -> RunnerContext:
    """Reject other repositories, branches, events, unmarked pushes and stale SHAs."""
    require(environment.get("GITHUB_ACTIONS") == "true", "Publication requires GitHub Actions")
    require(environment.get("GITHUB_EVENT_NAME") == "push", "Publication requires a push event")
    require(environment.get("GITHUB_REPOSITORY") == REPOSITORY, "Wrong publication repository")
    require(environment.get("GITHUB_REF") == BRANCH_REF, "Wrong publication branch")
    commit = environment.get("GITHUB_SHA", "")
    require(COMMIT_RE.fullmatch(commit) is not None, "Invalid runner source commit")
    run_id, attempt = environment.get("GITHUB_RUN_ID", ""), environment.get("GITHUB_RUN_ATTEMPT", "")
    require(run_id.isdecimal() and int(run_id) > 0, "Invalid runner run ID")
    require(attempt.isdecimal() and int(attempt) > 0, "Invalid runner run attempt")
    event_path = environment.get("GITHUB_EVENT_PATH", "")
    require(bool(event_path), "Missing push-event evidence")
    event = read_json(Path(event_path))
    require(event.get("repository", {}).get("full_name") == REPOSITORY,
            "Push-event repository differs from runner")
    require(event.get("ref") == BRANCH_REF and event.get("after") == commit
            and event.get("deleted") is False, "Push-event ref/SHA differs from runner")
    head = event.get("head_commit")
    require(isinstance(head, dict) and head.get("id") == commit, "Missing matching push head commit")
    message = head.get("message")
    require(isinstance(message, str) and PUBLISH_MARKER in message.splitlines(),
            "Push head commit lacks the exact publication marker line")
    return RunnerContext(commit, run_id, attempt)


@dataclass(frozen=True)
class Asset:
    path: Path
    name: str
    sha256: str
    size: int
    content_type: str

    @classmethod
    def from_file(cls, path: Path, content_type: str) -> "Asset":
        require(path.is_file() and not path.is_symlink(), "Missing or redirected release asset")
        require(path.stat().st_size > 0, "Empty release asset")
        return cls(path, path.name, sha256_file(path), path.stat().st_size, content_type)

    def recheck(self) -> None:
        require(self.path.is_file() and not self.path.is_symlink()
                and self.path.stat().st_size == self.size
                and sha256_file(self.path) == self.sha256,
                "Prepared release asset changed before upload")


@dataclass(frozen=True)
class PreparedRelease:
    context: RunnerContext
    assets: tuple[Asset, ...]
    body: str


def release_notes(context: RunnerContext, assets: tuple[Asset, ...]) -> str:
    hashes = "\n".join(f"- `{asset.name}`: `{asset.sha256}`" for asset in assets)
    return f"""## Desktop OCR Tool {TAG} candidate prerelease

Windows x64 single-file executable with bundled Python, Qt, ONNX Runtime,
RapidOCR/PaddleOCR v4 ONNX models (det/rec/cls). No Python/pip installation or
first-run model download is required by the bundle.

Native Windows source checks, frozen OCR/database and application-lifecycle
candidate probes, and frozen probes under an outbound Windows Firewall block
passed for the EXE SHA256 below. These are bounded candidate checks.
No clean Windows runtime/installation/cache audit has passed. This is not a
stable-release acceptance gate or a claim of zero OCR errors/no data egress.

Known limits: rare characters and small text can be misrecognized; tile seams
and field holdouts still need review; mixed-DPI/multi-monitor behavior has not
been audited and capture currently uses the primary screen. A hung native
operation inside a QThread can still prevent bounded shutdown. Clipboard
monitoring defaults off for new settings; stored history/images are plaintext.

Source commit: `{context.source_commit}`
Validation evidence: same-run Windows artifact; see the validation metadata ZIP.
Download the portable ZIP and extract it, or use the separate bundled EXE.
Verify downloaded bytes against SHA256SUMS before running.

### Asset SHA256
{hashes}
"""


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # In particular, never forward an Authorization header to a Location.
        raise PublicationError("GitHub API redirect refused")


class GitHubClient:
    """Only official fixed-repository endpoints; no redirects or token logging."""
    def __init__(self, token: str, opener=None):
        require(bool(token) and "\r" not in token and "\n" not in token, "Missing or invalid runner GH_TOKEN")
        self._token = token
        self._opener = opener or request.build_opener(NoRedirect())

    @staticmethod
    def validate_url(url: str, *, upload: bool = False) -> None:
        parsed = parse.urlsplit(url)
        host = "uploads.github.com" if upload else "api.github.com"
        require(parsed.scheme == "https" and parsed.netloc == host
                and not parsed.fragment and parsed.username is None and parsed.password is None,
                "Non-official GitHub endpoint refused")
        require(parsed.path.startswith("/repos/" + REPOSITORY + "/"),
                "Endpoint outside the fixed repository refused")
        require(".." not in parse.unquote(parsed.path).split("/")
                and "\\" not in parse.unquote(parsed.path),
                "Endpoint path traversal refused")

    def call(self, method: str, endpoint: str, *, payload=None, asset: Asset | None = None,
             expected: int = 200, missing_ok: bool = False):
        url = endpoint if asset is not None else API_BASE + endpoint
        self.validate_url(url, upload=asset is not None)
        require(method in {"GET", "POST", "PATCH"}, "Unsupported publication method")
        headers = {"Accept": "application/vnd.github+json", "Authorization": "Bearer " + self._token,
                   "X-GitHub-Api-Version": API_VERSION, "User-Agent": "desktop-ocr-rc2-publisher"}
        stream = None
        data = None
        if asset is not None:
            asset.recheck()
            headers.update({"Content-Type": asset.content_type, "Content-Length": str(asset.size)})
            stream = asset.path.open("rb")
            data = stream
        elif payload is not None:
            data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=600 if asset else 90) as response:
                require(response.status == expected, "Unexpected GitHub API success status")
                raw = response.read(MAX_JSON_BYTES + 1)
                require(len(raw) <= MAX_JSON_BYTES, "GitHub API response is too large")
                return json.loads(raw.decode("utf-8"))
        except error.HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            # Do not echo request headers, token, response bodies or arbitrary URL data.
            suffix = ""
            if method == "POST" and endpoint == "/releases" and exc.code in {403, 404}:
                suffix = ("; GitHub may require Workflows:write for a target commit changing workflows; "
                          "automatic GITHUB_TOKEN cannot grant that permission. "
                          "No broader credential or token fallback is attempted")
            raise PublicationError(f"GitHub {method} failed (HTTP {exc.code}){suffix}; no credentials logged") from None
        except (error.URLError, OSError, ValueError) as exc:
            raise PublicationError("GitHub request failed; inspect the draft before retrying") from None
        finally:
            if stream is not None:
                stream.close()


def release_id(release: dict) -> int:
    value = release.get("id")
    require(type(value) is int and value > 0, "Invalid GitHub release ID")
    return value


def resolve_tag(client: GitHubClient) -> str | None:
    ref = client.call("GET", "/git/ref/tags/" + TAG, missing_ok=True)
    if ref is None:
        return None
    require(isinstance(ref, dict) and ref.get("ref") == "refs/tags/" + TAG, "Unexpected Git tag reference")
    obj = ref.get("object")
    seen = set()
    for _ in range(10):
        require(isinstance(obj, dict) and COMMIT_RE.fullmatch(obj.get("sha", "")) is not None,
                "Malformed Git tag target")
        sha = obj["sha"]
        if obj.get("type") == "commit":
            return sha
        require(obj.get("type") == "tag" and sha not in seen, "Unsupported or cyclic Git tag target")
        seen.add(sha)
        tag = client.call("GET", "/git/tags/" + sha)
        require(isinstance(tag, dict) and tag.get("sha") == sha, "Mismatched annotated tag")
        obj = tag.get("object")
    raise PublicationError("Annotated tag nesting exceeds bound")


def check_tag(client: GitHubClient, source_commit: str, *, required: bool = False) -> None:
    target = resolve_tag(client)
    require(target == source_commit or (target is None and not required),
            "Release tag already points to another commit or is missing; it will never be moved")


def list_items(client: GitHubClient, endpoint: str) -> list[dict]:
    items = []
    for page in range(1, 101):
        batch = client.call("GET", f"{endpoint}?per_page=100&page={page}")
        require(isinstance(batch, list) and all(isinstance(item, dict) for item in batch),
                "Malformed GitHub list response")
        items.extend(batch)
        if len(batch) < 100:
            return items
    raise PublicationError("GitHub pagination exceeds bounded inspection")


def find_release(client: GitHubClient) -> dict | None:
    # Listing includes drafts for the job token; the by-tag endpoint is published-only.
    matches = [item for item in list_items(client, "/releases") if item.get("tag_name") == TAG]
    require(len(matches) <= 1, "Multiple releases use the fixed candidate tag")
    return matches[0] if matches else None


def check_release(release: dict, prepared: PreparedRelease) -> None:
    require(isinstance(release, dict), "Malformed GitHub release")
    release_id(release)
    require(release.get("tag_name") == TAG
            and release.get("target_commitish") == prepared.context.source_commit
            and release.get("name") == TAG and release.get("body") == prepared.body
            and release.get("prerelease") is True and type(release.get("draft")) is bool,
            "Existing release target/notes/type differs; it will never be overwritten")


def upload_url(release: dict) -> str:
    value = release.get("upload_url")
    require(isinstance(value, str) and value.endswith("{?name,label}"), "Malformed release upload URL")
    base = value.removesuffix("{?name,label}")
    GitHubClient.validate_url(base, upload=True)
    parsed = parse.urlsplit(base)
    require(parsed.path == f"/repos/{REPOSITORY}/releases/{release_id(release)}/assets"
            and not parsed.query, "Upload URL does not identify the returned release")
    return base


def check_asset(actual: dict, expected: Asset) -> None:
    require(isinstance(actual, dict) and type(actual.get("id")) is int and actual["id"] > 0
            and actual.get("name") == expected.name and actual.get("state") == "uploaded"
            and type(actual.get("size")) is int and actual["size"] == expected.size
            and actual.get("digest") == "sha256:" + expected.sha256,
            "GitHub asset name/state/size/SHA256 differs; no asset will be deleted or overwritten")


def verified_assets(client: GitHubClient, release: dict, prepared: PreparedRelease,
                    *, complete: bool = False) -> dict[str, dict]:
    expected = {asset.name: asset for asset in prepared.assets}
    existing = list_items(client, f"/releases/{release_id(release)}/assets")
    found = {}
    for asset in existing:
        name = asset.get("name")
        require(name in expected and name not in found, "Unexpected or duplicate existing release asset")
        check_asset(asset, expected[name])
        found[name] = asset
    if complete:
        require(set(found) == set(expected), "Release asset set is incomplete")
    return found


def publish(client: GitHubClient, prepared: PreparedRelease) -> dict:
    """No clobber/delete/retag; resume matching drafts and accept exact releases."""
    require(len(prepared.assets) == 4
            and {a.name for a in prepared.assets} == {EXE_NAME, ZIP_NAME, CHECKSUM_NAME, METADATA_NAME},
            "Expected exactly four unique prepared release assets")
    for asset in prepared.assets:
        asset.recheck()
    check_tag(client, prepared.context.source_commit)
    release = find_release(client)
    if release is None:
        release = client.call("POST", "/releases", payload={
            "tag_name": TAG, "target_commitish": prepared.context.source_commit,
            "name": TAG, "body": prepared.body, "draft": True, "prerelease": True,
            "make_latest": "false", "generate_release_notes": False,
        }, expected=201)
        check_release(release, prepared)
        require(release["draft"] is True, "New release was not created as a draft")
    else:
        check_release(release, prepared)
    existing = verified_assets(client, release, prepared, complete=not release["draft"])
    if release["draft"] is False:
        check_tag(client, prepared.context.source_commit, required=True)
        return release
    base = upload_url(release)
    for asset in prepared.assets:
        if asset.name in existing:
            continue
        uploaded = client.call("POST", base + "?" + parse.urlencode({"name": asset.name}),
                               asset=asset, expected=201)
        check_asset(uploaded, asset)
        confirmed = client.call("GET", f"/releases/assets/{uploaded['id']}")
        check_asset(confirmed, asset)
    # Re-read everything, including the tag, before the only publish transition.
    current = client.call("GET", f"/releases/{release_id(release)}")
    check_release(current, prepared)
    require(current["draft"] is True, "Draft changed during publication")
    verified_assets(client, current, prepared, complete=True)
    check_tag(client, prepared.context.source_commit)
    for asset in prepared.assets:
        asset.recheck()
    result = client.call("PATCH", f"/releases/{release_id(current)}", payload={
        "draft": False, "prerelease": True, "make_latest": "false",
    })
    check_release(result, prepared)
    require(result["draft"] is False, "GitHub did not publish the prerelease")
    verified_assets(client, result, prepared, complete=True)
    check_tag(client, prepared.context.source_commit, required=True)
    return result


def canonical_json(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def deterministic_metadata_zip(path: Path, files: Mapping[str, bytes]) -> None:
    require(sum(len(data) for data in files.values()) <= MAX_JSON_BYTES,
            "Validation metadata ZIP would exceed the small-evidence limit")
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            require(not name.startswith("/") and ".." not in Path(name).parts and "\\" not in name,
                    "Unsafe validation metadata name")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name])


def prepare_release(artifact_root: Path, verified_bundle: Path, output_dir: Path,
                    context: RunnerContext) -> PreparedRelease:
    """Reverify downloaded bytes, then prepare four immutable local upload files.

    This is a credential-free/network-free function. The saved verifier's
    PyInstaller payload inspection is bound by the independently rehashed ZIP
    and EXE. Everything else must equal the freshly recomputed verification.
    """
    # Running as ``python scripts/publish_prerelease.py`` must work without pip.
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from scripts.verify_candidate_bundle import verify_candidate

    artifact_root = Path(artifact_root)
    verified_bundle = Path(verified_bundle)
    output_dir = Path(output_dir)
    require(artifact_root.is_dir() and not artifact_root.is_symlink(), "Missing or redirected Windows artifact")
    require(not any(path.is_symlink() for path in artifact_root.rglob("*")),
            "Symlink in downloaded Windows artifact")
    root = artifact_root.resolve()
    require(verified_bundle.resolve() == root / "artifacts" / "verified-bundle.json",
            "Verified metadata must come from the same downloaded Windows artifact")
    require(output_dir.resolve() != root and root not in output_dir.resolve().parents,
            "Upload preparation directory must be outside the downloaded artifact")
    external_names = ["source-selftest.json", "source-app-smoke.json", "test-results.xml",
                      "artifacts/firewall-validation/candidate-gate.json",
                      "artifacts/firewall-validation/frozen-selftest.json",
                      "artifacts/firewall-validation/frozen-app-smoke.json"]
    snapshot_names = ["artifacts/verified-bundle.json", *external_names,
                      "artifacts/candidate-validation/frozen-selftest.json",
                      "artifacts/candidate-validation/frozen-app-smoke.json",
                      "artifacts/" + ZIP_NAME + ".sha256"]
    snapshots = {}
    for name in snapshot_names:
        path = root / name
        require(path.is_file() and path.stat().st_size <= MAX_JSON_BYTES,
                "Missing or oversized validation evidence")
        snapshots[name] = path.read_bytes()
    saved = read_json(verified_bundle)
    require(saved.get("schema") == 1 and saved.get("passed") is True
            and saved.get("version") == VERSION and saved.get("source_commit") == context.source_commit,
            "Verified-bundle metadata does not match this release/source")
    evidence_attempt = saved.get("workflow_run_attempt")
    require(isinstance(evidence_attempt, str) and evidence_attempt.isdecimal()
            and 0 < int(evidence_attempt) <= int(context.run_attempt),
            "Verified artifact attempt is invalid or newer than this runner")
    require(saved.get("workflow") == {
        "repository": REPOSITORY, "run_id": context.run_id,
        "run_attempt": evidence_attempt, "source_commit": context.source_commit,
    }, "Verified-bundle evidence belongs to another workflow run/attempt")
    require(saved.get("workflow_run_id") == context.run_id
            and saved.get("workflow_run_attempt") == evidence_attempt,
            "Conflicting verified-bundle run/attempt provenance")
    payload = saved.get("frozen_payload")
    require(isinstance(payload, dict) and payload.get("inspected") is True
            and payload.get("embedded_model_hashes_verified") is True
            and payload.get("required_payload") ==
            ["python312.dll", "qwindows.dll", "onnxruntime_pybind11_state.pyd"]
            and payload.get("system_dll_closure") == "NOT_A_CLEAN_HOST_CERTIFICATION",
            "Missing same-EXE frozen payload inspection")
    try:
        actual = verify_candidate(root, context.source_commit, inspect_payload=False)
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
        raise PublicationError("Downloaded Windows artifact failed candidate verification") from exc
    provenance = {"frozen_payload", "workflow", "workflow_run_id", "workflow_run_attempt"}
    saved_binding = {key: value for key, value in saved.items() if key not in provenance}
    actual_binding = {key: value for key, value in actual.items() if key not in provenance}
    require(saved_binding == actual_binding, "Verified metadata differs from downloaded artifact bytes")
    require(actual.get("bundle_filename") == ZIP_NAME and actual.get("executable_filename") == EXE_NAME,
            "Unexpected release asset names")
    source = actual.get("source_manifest", {})
    manifest = actual.get("build_manifest", {})
    require(source.get("dirty") is False
            and COMMIT_RE.fullmatch(source.get("head_tree", "")) is not None
            and SHA256_RE.fullmatch(source.get("working_tree_sha256", "")) is not None,
            "Build source was dirty or source tree identity is invalid")
    require(manifest.get("source") == {key: value for key, value in source.items() if key != "files"},
            "Build/source provenance differs")
    require(actual.get("windows_tests", {}).get("collected", 0) > 0,
            "Windows test evidence contains no collected tests")
    require(actual.get("clean_machine_verified") is False and actual.get("mixed_dpi_gate") == "NOT_RUN"
            and manifest.get("clean_machine_gate") == "NOT_RUN"
            and manifest.get("mixed_dpi_gate") == "NOT_RUN",
            "Unexpected clean-machine or mixed-DPI certification claim")
    bundle = root / "artifacts" / ZIP_NAME
    prefix = "release-" + TAG + "/"
    metadata_files = {"verified-bundle.json": canonical_json(saved)}
    for name in external_names:
        metadata_files[name] = snapshots[name]
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate member in portable ZIP")
        require(all((info.external_attr >> 16) & 0o170000 != 0o120000
                    for info in archive.infolist()), "Symlink member in portable ZIP")
        for name in ["BUILD_MANIFEST.json", "SOURCE_MANIFEST.json",
                     "candidate-validation/frozen-selftest.json", "candidate-validation/frozen-app-smoke.json"]:
            require(archive.getinfo(prefix + name).file_size <= MAX_JSON_BYTES,
                    "Oversized embedded validation metadata")
            metadata_files[name] = archive.read(prefix + name)
            if name.startswith("candidate-validation/"):
                require(snapshots["artifacts/" + name] == metadata_files[name],
                        "External/embedded frozen candidate reports differ")
        require(not output_dir.exists() and not output_dir.is_symlink(),
                "Upload output directory already exists; use a fresh directory")
        require(not any(parent.is_symlink() for parent in output_dir.parents),
                "Redirected upload output directory")
        output_dir.mkdir(parents=True, exist_ok=False)
        # Extract only the exact fixed EXE, never arbitrary archive members.
        with archive.open(prefix + EXE_NAME) as incoming, (output_dir / EXE_NAME).open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    with bundle.open("rb") as incoming, (output_dir / ZIP_NAME).open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    exe = Asset.from_file(output_dir / EXE_NAME, "application/octet-stream")
    portable = Asset.from_file(output_dir / ZIP_NAME, "application/zip")
    require(exe.sha256 == actual["executable_sha256"] and exe.size == actual["executable_bytes"]
            and portable.sha256 == actual["bundle_sha256"] and portable.size == actual["bundle_bytes"],
            "Actual copied ZIP/EXE bytes differ from verified candidate")
    require(all((root / name).read_bytes() == raw for name, raw in snapshots.items()),
            "Validation evidence changed during preparation")
    deterministic_metadata_zip(output_dir / METADATA_NAME, metadata_files)
    evidence = Asset.from_file(output_dir / METADATA_NAME, "application/zip")
    checksum = output_dir / CHECKSUM_NAME
    with checksum.open("x", encoding="ascii", newline="\n") as stream:
        stream.write("".join(f"{asset.sha256}  {asset.name}\n" for asset in (exe, portable, evidence)))
    sums = Asset.from_file(checksum, "text/plain")
    assets = (exe, portable, sums, evidence)
    return PreparedRelease(context, assets, release_notes(context, assets))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--verified-bundle", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prepare-only", action="store_true", help="Verify/prepare without reading GH_TOKEN or using network")
    args = parser.parse_args(argv)
    try:
        context = validate_runner_context(os.environ)
        prepared = prepare_release(args.artifact_root, args.verified_bundle, args.output_dir, context)
        if args.prepare_only:
            print("Four candidate assets prepared; no network or token access")
            return 0
        # Read this single automatically supplied Actions token only after all
        # runner/event/artifact checks. No local credential discovery/fallback.
        client = GitHubClient(os.environ.get("GH_TOKEN", ""))
        published = publish(client, prepared)
        expected_url = f"https://github.com/{REPOSITORY}/releases/tag/{TAG}"
        require(published.get("html_url") == expected_url, "Unexpected published release URL")
        print("Verified prerelease published: " + expected_url)
        # A small non-secret result for the workflow's evidence artifact.
        result_path = args.output_dir / "published-release.json"
        with result_path.open("x", encoding="utf-8") as stream:
            stream.write(canonical_json({"schema": 1, "published": True, "prerelease": True,
                                         "tag": TAG, "release_id": release_id(published),
                                         "url": expected_url, "source_commit": context.source_commit,
                                         "assets": {asset.name: {"sha256": asset.sha256, "bytes": asset.size}
                                                    for asset in prepared.assets}}).decode("utf-8"))
        return 0
    except PublicationError as exc:
        print("Publication stopped: " + str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Avoid leaking arbitrary event/evidence/API data from exception text.
        print("Publication stopped: unexpected validation failure; no credentials logged", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
