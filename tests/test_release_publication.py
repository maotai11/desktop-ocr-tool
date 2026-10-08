"""Local fixtures and mocked GitHub responses only; never use live credentials."""
import copy
import hashlib
import io
import json
import zipfile
from urllib import error, parse

import pytest
from model_profile_fixtures import fixture_profiles, fixture_validation, profile_report

from scripts import publish_prerelease as publisher
from scripts.verify_candidate_bundle import verify_candidate

COMMIT = "a" * 40
CONTEXT = publisher.RunnerContext(COMMIT, "1234", "1")


@pytest.fixture
def runner(tmp_path):
    event = {"repository": {"full_name": publisher.REPOSITORY}, "ref": publisher.BRANCH_REF,
             "after": COMMIT, "deleted": False,
             "head_commit": {"id": COMMIT, "message": "Release candidate\n\n" + publisher.PUBLISH_MARKER}}
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": publisher.REPOSITORY,
           "GITHUB_REF": publisher.BRANCH_REF, "GITHUB_SHA": COMMIT, "GITHUB_RUN_ID": "1234",
           "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_PATH": str(event_path)}
    return env, event, event_path


def test_exact_marked_push_is_the_only_runtime_entry(runner):
    env, _, _ = runner
    assert publisher.validate_runner_context(env) == CONTEXT


@pytest.mark.parametrize("field,value", [
    ("GITHUB_ACTIONS", "false"), ("GITHUB_EVENT_NAME", "pull_request"),
    ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_REPOSITORY", "attacker/desktop-ocr-tool"),
    ("GITHUB_REF", "refs/heads/master"), ("GITHUB_REF", "refs/heads/fix/ocr-model-upgrade"),
    ("GITHUB_SHA", "bad"),
    ("GITHUB_RUN_ID", ""), ("GITHUB_RUN_ATTEMPT", "0"),
])
def test_runtime_entry_rejects_other_repositories_events_and_refs(runner, field, value):
    env, _, _ = runner
    env[field] = value
    with pytest.raises(publisher.PublicationError):
        publisher.validate_runner_context(env)


@pytest.mark.parametrize("message", ["", "publish-prerelease: v1.6.2-rc.1", "prefix " + publisher.PUBLISH_MARKER,
                                    publisher.PUBLISH_MARKER + " suffix", publisher.PUBLISH_MARKER + " "])
def test_publication_marker_must_be_an_exact_head_commit_line(runner, message):
    env, event, path = runner
    event["head_commit"]["message"] = message
    path.write_text(json.dumps(event))
    with pytest.raises(publisher.PublicationError, match="marker"):
        publisher.validate_runner_context(env)


@pytest.mark.parametrize("defect", ["repo", "ref", "after", "head", "deleted"])
def test_push_event_and_environment_must_agree(runner, defect):
    env, event, path = runner
    if defect == "repo": event["repository"]["full_name"] = "attacker/repository"
    if defect == "ref": event["ref"] = "refs/heads/master"
    if defect == "after": event["after"] = "b" * 40
    if defect == "head": event["head_commit"]["id"] = "b" * 40
    if defect == "deleted": event["deleted"] = True
    path.write_text(json.dumps(event))
    with pytest.raises(publisher.PublicationError):
        publisher.validate_runner_context(env)


@pytest.fixture
def prepared(tmp_path):
    names = (publisher.EXE_NAME, publisher.ZIP_NAME, publisher.CHECKSUM_NAME, publisher.METADATA_NAME)
    assets = []
    for name in names:
        path = tmp_path / name
        path.write_bytes((name + " fixture bytes").encode())
        assets.append(publisher.Asset.from_file(path, "application/octet-stream"))
    assets = tuple(assets)
    return publisher.PreparedRelease(CONTEXT, assets, publisher.release_notes(CONTEXT, assets))


class FakeGitHub:
    """A stateful REST double which never has a token or opens a socket."""
    def __init__(self, prepared, release=None, assets=(), tag=None):
        self.prepared = prepared
        self.release = copy.deepcopy(release)
        self.assets = copy.deepcopy(list(assets))
        self.tag = tag
        self.calls = []
        self.next_asset = 10
        self.bad_upload = None
        self.fail_upload_number = None
        self.upload_count = 0
        self.tag_on_finalize = None

    def matching_release(self, draft=True):
        return {"id": 77, "tag_name": publisher.TAG, "target_commitish": COMMIT,
                "name": publisher.TAG, "body": self.prepared.body, "draft": draft, "prerelease": True,
                "upload_url": f"https://uploads.github.com/repos/{publisher.REPOSITORY}/releases/77/assets{{?name,label}}",
                "html_url": f"https://github.com/{publisher.REPOSITORY}/releases/tag/{publisher.TAG}"}

    def matching_asset(self, asset, asset_id=1):
        return {"id": asset_id, "name": asset.name, "state": "uploaded", "size": asset.size,
                "digest": "sha256:" + asset.sha256}

    def call(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        path = parse.urlsplit(endpoint).path
        if path.startswith("/git/ref/tags/"):
            return None if self.tag is None else {"ref": "refs/tags/" + publisher.TAG,
                                                  "object": {"type": "commit", "sha": self.tag}}
        if method == "GET" and path == "/releases":
            return [] if self.release is None else [copy.deepcopy(self.release)]
        if method == "POST" and path == "/releases":
            assert self.release is None
            assert kwargs["payload"]["draft"] is True
            assert kwargs["payload"]["make_latest"] == "false"
            assert kwargs["expected"] == 201
            self.release = self.matching_release()
            return copy.deepcopy(self.release)
        if method == "GET" and path == "/releases/77/assets":
            return copy.deepcopy(self.assets)
        if method == "GET" and path.startswith("/releases/assets/"):
            asset_id = int(path.rsplit("/", 1)[1])
            return copy.deepcopy(next(asset for asset in self.assets if asset["id"] == asset_id))
        if method == "POST" and path.endswith("/releases/77/assets"):
            asset = kwargs["asset"]
            assert parse.parse_qs(parse.urlsplit(endpoint).query) == {"name": [asset.name]}
            self.upload_count += 1
            if self.fail_upload_number == self.upload_count:
                raise publisher.PublicationError("mock interrupted upload")
            uploaded = self.matching_asset(asset, self.next_asset)
            self.next_asset += 1
            if self.bad_upload:
                key, value = self.bad_upload
                uploaded[key] = value
            self.assets.append(uploaded)
            return copy.deepcopy(uploaded)
        if method == "GET" and path == "/releases/77":
            return copy.deepcopy(self.release)
        if method == "PATCH" and path == "/releases/77":
            assert kwargs["payload"] == {"draft": False, "prerelease": True, "make_latest": "false"}
            assert {asset["name"] for asset in self.assets} == {asset.name for asset in self.prepared.assets}
            self.release["draft"] = False
            self.tag = self.tag_on_finalize or COMMIT
            return copy.deepcopy(self.release)
        raise AssertionError((method, endpoint, kwargs))


def test_new_release_is_draft_until_all_four_digests_are_verified(prepared):
    client = FakeGitHub(prepared)
    result = publisher.publish(client, prepared)
    assert result["draft"] is False and result["prerelease"] is True
    assert client.upload_count == 4
    mutations = [(method, endpoint) for method, endpoint, _ in client.calls if method != "GET"]
    assert mutations[0] == ("POST", "/releases")
    assert mutations[-1] == ("PATCH", "/releases/77")
    assert all(method != "DELETE" for method, _, _ in client.calls)


def test_existing_matching_tag_is_used_without_moving_it(prepared):
    client = FakeGitHub(prepared, tag=COMMIT)
    publisher.publish(client, prepared)
    assert not any(method != "GET" and "/git/" in endpoint for method, endpoint, _ in client.calls)


def test_wrong_existing_tag_fails_before_any_mutation(prepared):
    client = FakeGitHub(prepared, tag="b" * 40)
    with pytest.raises(publisher.PublicationError, match="never be moved"):
        publisher.publish(client, prepared)
    assert all(method == "GET" for method, _, _ in client.calls)


def test_exact_published_release_is_idempotent_and_read_only(prepared):
    client = FakeGitHub(prepared, tag=COMMIT)
    client.release = client.matching_release(draft=False)
    client.assets = [client.matching_asset(asset, i + 1) for i, asset in enumerate(prepared.assets)]
    publisher.publish(client, prepared)
    assert all(method == "GET" for method, _, _ in client.calls)


def test_interrupted_matching_draft_resumes_only_missing_assets(prepared):
    client = FakeGitHub(prepared)
    client.fail_upload_number = 2
    with pytest.raises(publisher.PublicationError, match="interrupted"):
        publisher.publish(client, prepared)
    assert client.release["draft"] is True and len(client.assets) == 1
    first_id = client.assets[0]["id"]
    client.fail_upload_number = None
    publisher.publish(client, prepared)
    assert len(client.assets) == 4 and client.assets[0]["id"] == first_id
    assert client.upload_count == 5


@pytest.mark.parametrize("field,value", [("target_commitish", "b" * 40), ("body", "other notes"),
                                        ("prerelease", False), ("name", "other release")])
def test_existing_mismatched_release_is_never_modified(prepared, field, value):
    client = FakeGitHub(prepared)
    client.release = client.matching_release()
    client.release[field] = value
    with pytest.raises(publisher.PublicationError, match="never be overwritten"):
        publisher.publish(client, prepared)
    assert all(method == "GET" for method, _, _ in client.calls)


@pytest.mark.parametrize("field,value", [("digest", None), ("digest", "sha256:" + "0" * 64),
                                        ("state", "starter"), ("size", 0), ("name", "renamed.exe")])
def test_upload_hash_state_size_and_name_must_match_before_publish(prepared, field, value):
    client = FakeGitHub(prepared)
    client.bad_upload = (field, value)
    with pytest.raises(publisher.PublicationError, match="no asset will be deleted"):
        publisher.publish(client, prepared)
    assert client.release["draft"] is True
    assert not any(method in {"PATCH", "DELETE"} for method, _, _ in client.calls)


def test_mismatched_existing_asset_fails_without_upload_or_delete(prepared):
    client = FakeGitHub(prepared)
    client.release = client.matching_release()
    bad = client.matching_asset(prepared.assets[0])
    bad["digest"] = "sha256:" + "0" * 64
    client.assets = [bad]
    with pytest.raises(publisher.PublicationError):
        publisher.publish(client, prepared)
    assert all(method == "GET" for method, _, _ in client.calls)


def test_unexpected_extra_asset_cannot_be_ignored(prepared):
    client = FakeGitHub(prepared)
    client.release = client.matching_release()
    client.assets = [{"name": "unexpected.zip"}]
    with pytest.raises(publisher.PublicationError, match="Unexpected"):
        publisher.publish(client, prepared)
    assert all(method == "GET" for method, _, _ in client.calls)


def test_incomplete_published_release_is_never_filled_in(prepared):
    client = FakeGitHub(prepared, tag=COMMIT)
    client.release = client.matching_release(draft=False)
    with pytest.raises(publisher.PublicationError, match="incomplete"):
        publisher.publish(client, prepared)
    assert all(method == "GET" for method, _, _ in client.calls)


def test_prepared_asset_tampering_fails_before_network(prepared):
    prepared.assets[0].path.write_bytes(b"changed")
    client = FakeGitHub(prepared)
    with pytest.raises(publisher.PublicationError, match="changed"):
        publisher.publish(client, prepared)
    assert client.calls == []


def test_annotated_tags_are_resolved_to_commits_and_cycles_are_refused():
    class Client:
        def __init__(self, cycle=False): self.cycle = cycle
        def call(self, method, endpoint, **kwargs):
            obj = {"type": "tag", "sha": "b" * 40}
            if "/ref/" in endpoint: return {"ref": "refs/tags/" + publisher.TAG, "object": obj}
            return {"sha": "b" * 40, "object": obj if self.cycle else {"type": "commit", "sha": COMMIT}}
    assert publisher.resolve_tag(Client()) == COMMIT
    with pytest.raises(publisher.PublicationError, match="cyclic"):
        publisher.resolve_tag(Client(cycle=True))


@pytest.mark.parametrize("url", ["http://uploads.github.com/", "https://uploads.github.com.evil.test/",
                               "https://user@uploads.github.com/", "https://uploads.github.com:443/",
                               "https://uploads.github.com/repos/attacker/repo/releases/77/assets{?name,label}",
                               f"https://uploads.github.com/repos/{publisher.REPOSITORY}/releases/78/assets{{?name,label}}",
                               f"https://uploads.github.com/repos/{publisher.REPOSITORY}/releases/77/assets?evil=1{{?name,label}}"])
def test_upload_url_must_be_official_and_belong_to_returned_release(prepared, url):
    client = FakeGitHub(prepared)
    release = client.matching_release()
    release["upload_url"] = url
    with pytest.raises(publisher.PublicationError):
        publisher.upload_url(release)


class FakeResponse(io.BytesIO):
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def test_http_errors_never_echo_token_or_server_body():
    token = "mock-runner-token-do-not-log"
    class Opener:
        def open(self, req, **kwargs):
            assert req.get_header("Authorization") == "Bearer " + token
            raise error.HTTPError(req.full_url, 403, token, {}, io.BytesIO(token.encode()))
    client = publisher.GitHubClient(token, Opener())
    with pytest.raises(publisher.PublicationError) as caught:
        client.call("GET", "/releases")
    assert token not in str(caught.value)
    assert "403" in str(caught.value)


@pytest.mark.parametrize("status", [403, 404])
def test_workflow_scope_failure_reports_no_broader_token_fallback(status):
    class Opener:
        def open(self, req, **kwargs):
            raise error.HTTPError(req.full_url, status, "not accessible", {}, io.BytesIO(b"{}"))
    client = publisher.GitHubClient("mock-runner-token", Opener())
    with pytest.raises(publisher.PublicationError) as caught:
        client.call("POST", "/releases", payload={"draft": True}, expected=201)
    assert "may require Workflows:write" in str(caught.value)
    assert "No broader credential or token fallback" in str(caught.value)


def test_http_client_refuses_nonofficial_requests_before_token_transmission():
    class Opener:
        def open(self, *args, **kwargs): raise AssertionError("must not send")
    client = publisher.GitHubClient("mock-token", Opener())
    with pytest.raises(publisher.PublicationError):
        client.call("GET", "/../../attacker/repo")


def test_redirects_are_refused_without_sending_authorization_elsewhere():
    with pytest.raises(publisher.PublicationError, match="redirect"):
        publisher.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.test")


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    # Unit fixtures use the one explicitly approved candidate namespace.
    from scripts import verify_candidate_bundle
    monkeypatch.setattr(verify_candidate_bundle, 'VERSION', publisher.VERSION)
    for name, value in {"GITHUB_REPOSITORY": publisher.REPOSITORY, "GITHUB_RUN_ID": CONTEXT.run_id,
                        "GITHUB_RUN_ATTEMPT": CONTEXT.run_attempt}.items():
        monkeypatch.setenv(name, value)
    root = tmp_path / "downloaded-windows"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    firewall = artifacts / "firewall-validation"
    firewall.mkdir()
    frozen = artifacts / "candidate-validation"
    frozen.mkdir()
    source = {"commit": COMMIT, "head_tree": "b" * 40, "working_tree_sha256": "c" * 64,
              "dirty": False, "files": {"src/main.py": {"sha256": "d" * 64}}}
    encoded = lambda data: json.dumps(data, sort_keys=True).encode()
    source_bytes = encoded(source)
    exe = b"same-exe-fixture"
    profiles, _ = fixture_profiles()
    models = profiles['v6-small']
    common = {"schema": 2, "passed": True, "frozen": True, "platform": "win32", "qt_platform": "windows",
              "version": publisher.VERSION, "clean_machine_verified": False, "database_integrity": "ok",
              "executable_sha256": hashlib.sha256(exe).hexdigest()}
    probes = {"frozen-selftest.json": dict(common, probe="ocr_database", telemetry_env="1", **profile_report(profiles),
                                         checks={"qt": True, "models": True, "ocr": True, "database": True}),
              "frozen-app-smoke.json": dict(common, probe="application_lifecycle", phase_a=True,
                                          engine_ready=True, shutdown_clean=True,
                                          threads_stopped={"capture": True, "ocr": True, "database": True, "hotkeys": True})}
    manifest = {"schema": 2, "platform": "win32", "version": publisher.VERSION,
                "executable_name": publisher.EXE_NAME, "executable_sha256": hashlib.sha256(exe).hexdigest(),
                "models": models, "model_profiles": profiles, "default_model_profile": "v6-small",
                "ocr_validation_fixtures": fixture_validation()[0], "source_manifest_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "source": {key: value for key, value in source.items() if key != "files"},
                "clean_machine_gate": "NOT_RUN", "mixed_dpi_gate": "NOT_RUN",
                "candidate_probes": {name: {"status": "PASSED", "sha256": hashlib.sha256(encoded(probe)).hexdigest()}
                                     for name, probe in probes.items()}}
    contents = {"BUILD_MANIFEST.json": encoded(manifest), "SOURCE_MANIFEST.json": source_bytes,
                publisher.EXE_NAME: exe, **{"candidate-validation/" + name: encoded(probe) for name, probe in probes.items()}}
    bundle = artifacts / publisher.ZIP_NAME
    def write_bundle():
        with zipfile.ZipFile(bundle, "w") as archive:
            for name, raw in contents.items(): archive.writestr("release-" + publisher.TAG + "/" + name, raw)
        bundle.with_suffix(".zip.sha256").write_text(publisher.sha256_file(bundle) + "  " + bundle.name)
    write_bundle()
    for name, probe in probes.items():
        (frozen / name).write_bytes(encoded(probe))
        (firewall / name).write_bytes(encoded(probe))
        source_name = "source-selftest.json" if name == "frozen-selftest.json" else "source-app-smoke.json"
        (root / source_name).write_bytes(encoded(dict(probe, frozen=False, executable_sha256=None)))
    gate = {"schema": 1, "passed": True, "version": publisher.VERSION,
            "executable_sha256": manifest["executable_sha256"], "clean_machine_verified": False,
            "probes": {name: {"status": "PASSED", "sha256": publisher.sha256_file(firewall / name)} for name in probes}}
    (firewall / "candidate-gate.json").write_bytes(encoded(gate))
    (root / "test-results.xml").write_text('<testsuites><testsuite failures="0" errors="0"><testcase name="one"/></testsuite></testsuites>')
    report_path = artifacts / "verified-bundle.json"
    def save_report():
        report = verify_candidate(root, COMMIT)
        report["workflow"] = {"repository": publisher.REPOSITORY, "run_id": "1234", "run_attempt": "1", "source_commit": COMMIT}
        report["frozen_payload"] = {"inspected": True, "embedded_model_hashes_verified": True,
                                    "required_payload": ["python312.dll", "qwindows.dll", "onnxruntime_pybind11_state.pyd"],
                                    "system_dll_closure": "NOT_A_CLEAN_HOST_CERTIFICATION"}
        report_path.write_bytes(encoded(report))
        return report
    save_report()
    return root, report_path, contents, write_bundle, save_report


def test_preparation_reverifies_actual_zip_exe_and_all_candidate_evidence(candidate, tmp_path):
    root, report, _, _, _ = candidate
    prepared = publisher.prepare_release(root, report, tmp_path / "upload", CONTEXT)
    assert {asset.name for asset in prepared.assets} == {
        publisher.EXE_NAME, publisher.ZIP_NAME, publisher.CHECKSUM_NAME, publisher.METADATA_NAME}
    assert (tmp_path / "upload" / publisher.EXE_NAME).read_bytes() == b"same-exe-fixture"
    sums = (tmp_path / "upload" / publisher.CHECKSUM_NAME).read_text()
    for asset in prepared.assets:
        if asset.name != publisher.CHECKSUM_NAME: assert f"{asset.sha256}  {asset.name}\n" in sums
    with zipfile.ZipFile(tmp_path / "upload" / publisher.METADATA_NAME) as archive:
        assert "verified-bundle.json" in archive.namelist()
        assert "artifacts/firewall-validation/candidate-gate.json" in archive.namelist()
    assert "No clean Windows runtime" in prepared.body
    assert "primary screen" in prepared.body and "QThread" in prepared.body
    assert 'marked needs_review now auto-copy' in prepared.body
    assert 'All three default off' in prepared.body and 'affect copied output only' in prepared.body
    assert 'Removing line breaks joins different' in prepared.body
    assert 'Uncertain results do not auto-copy' not in prepared.body


def test_prepared_metadata_assets_are_deterministic(candidate, tmp_path):
    root, report, _, _, _ = candidate
    first = publisher.prepare_release(root, report, tmp_path / "first", CONTEXT)
    second = publisher.prepare_release(root, report, tmp_path / "second", CONTEXT)
    assert [(asset.name, asset.sha256) for asset in first.assets] == [(asset.name, asset.sha256) for asset in second.assets]
    assert first.body == second.body


def test_failed_publisher_rerun_reuses_earlier_verified_same_run_artifact(candidate, tmp_path, monkeypatch):
    root, report, _, _, _ = candidate
    original = report.read_bytes()
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    context = publisher.RunnerContext(COMMIT, CONTEXT.run_id, '2')
    prepared = publisher.prepare_release(root, report, tmp_path / 'retry-upload', context)
    assert len(prepared.assets) == 4
    assert report.read_bytes() == original
    with zipfile.ZipFile(tmp_path / 'retry-upload' / publisher.METADATA_NAME) as archive:
        saved = json.loads(archive.read('verified-bundle.json'))
        assert saved['workflow_run_attempt'] == '1'
        assert saved['workflow']['source_commit'] == COMMIT


@pytest.mark.parametrize("defect", ["sha", "run", "attempt", "repository", "payload", "metadata", "firewall", "exe", "external"])
def test_preparation_rejects_wrong_run_stale_metadata_and_changed_bytes(candidate, tmp_path, defect):
    root, report_path, contents, write_bundle, _ = candidate
    report = json.loads(report_path.read_text())
    if defect == "sha": report["source_commit"] = "b" * 40
    if defect == "run": report["workflow"]["run_id"] = "9999"
    if defect == "attempt": report["workflow"]["run_attempt"] = "2"
    if defect == "repository": report["workflow"]["repository"] = "attacker/repository"
    if defect == "payload": report["frozen_payload"]["inspected"] = False
    if defect == "metadata": report["executable_bytes"] += 1
    if defect == "firewall": (root / "artifacts/firewall-validation/frozen-selftest.json").write_text("{}")
    if defect == "exe":
        contents[publisher.EXE_NAME] += b"changed"
        write_bundle()
    if defect == "external": (root / "artifacts/candidate-validation/frozen-selftest.json").write_text("{}")
    report_path.write_text(json.dumps(report))
    with pytest.raises(publisher.PublicationError):
        publisher.prepare_release(root, report_path, tmp_path / "upload", CONTEXT)
    assert not (tmp_path / "upload").exists()


def test_preparation_refuses_dirty_source_even_if_hashes_are_consistent(candidate, tmp_path):
    root, report, contents, write_bundle, _save_report = candidate
    source = json.loads(contents["SOURCE_MANIFEST.json"])
    source["dirty"] = True
    contents["SOURCE_MANIFEST.json"] = json.dumps(source).encode()
    manifest = json.loads(contents["BUILD_MANIFEST.json"])
    manifest["source"] = {key: value for key, value in source.items() if key != "files"}
    manifest["source_manifest_sha256"] = hashlib.sha256(contents["SOURCE_MANIFEST.json"]).hexdigest()
    contents["BUILD_MANIFEST.json"] = json.dumps(manifest).encode()
    write_bundle()
    # The independently hardened verifier now rejects dirtiness before the
    # saved/fresh comparison; do not regenerate evidence for an invalid build.
    with pytest.raises(publisher.PublicationError, match="verification"):
        publisher.prepare_release(root, report, tmp_path / "upload", CONTEXT)


def test_cli_invalid_runner_does_not_read_token_or_open_network(tmp_path, monkeypatch, capsys):
    class Environment(dict):
        def get(self, name, default=None):
            if name == "GH_TOKEN": raise AssertionError("must not read token")
            return super().get(name, default)
    monkeypatch.setattr(publisher.os, "environ", Environment())
    monkeypatch.setattr(publisher.request, "build_opener", lambda *args: pytest.fail("must not build network client"))
    assert publisher.main(["--artifact-root", str(tmp_path), "--verified-bundle", str(tmp_path / "report"),
                           "--output-dir", str(tmp_path / "out")]) == 1
    assert "GitHub Actions" in capsys.readouterr().err


def test_prepare_only_cli_does_not_read_runner_token(candidate, runner, tmp_path, monkeypatch, capsys):
    root, report, _, _, _ = candidate
    env, _, _ = runner
    class Environment(dict):
        def get(self, name, default=None):
            if name == "GH_TOKEN": raise AssertionError("prepare-only must not read token")
            return super().get(name, default)
    monkeypatch.setattr(publisher.os, "environ", Environment(env))
    monkeypatch.setattr(publisher.request, "build_opener", lambda *args: pytest.fail("must not build network client"))
    assert publisher.main(["--artifact-root", str(root), "--verified-bundle", str(report),
                           "--output-dir", str(tmp_path / "upload"), "--prepare-only"]) == 0
    assert "no network or token access" in capsys.readouterr().out


def test_api_upload_streams_raw_bytes_and_checks_content_length(prepared):
    asset = prepared.assets[0]
    class Opener:
        def open(self, req, **kwargs):
            assert req.method == "POST"
            assert req.get_header("Content-length") == str(asset.size)
            assert req.get_header("Content-type") == asset.content_type
            assert req.data.read() == asset.path.read_bytes()
            response = FakeResponse(json.dumps({"id": 1}).encode())
            response.status = 201
            return response
    client = publisher.GitHubClient("mock-runner-token", Opener())
    endpoint = f"https://uploads.github.com/repos/{publisher.REPOSITORY}/releases/77/assets?name={asset.name}"
    assert client.call("POST", endpoint, asset=asset, expected=201) == {"id": 1}


def test_duplicate_zip_members_are_rejected_before_preparing_outputs(candidate, tmp_path):
    root, report, contents, _, _ = candidate
    bundle = root / "artifacts" / publisher.ZIP_NAME
    with pytest.warns(UserWarning, match="Duplicate"), zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr("release-" + publisher.TAG + "/SOURCE_MANIFEST.json", contents["SOURCE_MANIFEST.json"])
    bundle.with_suffix(".zip.sha256").write_text(publisher.sha256_file(bundle) + "  " + bundle.name)
    with pytest.raises(publisher.PublicationError, match="verification"):
        publisher.prepare_release(root, report, tmp_path / "upload", CONTEXT)
    assert not (tmp_path / "upload").exists()


def test_evidence_mutation_during_verify_is_rejected(candidate, tmp_path, monkeypatch):
    root, report, _, _, _ = candidate
    from scripts import verify_candidate_bundle
    original = verify_candidate_bundle.verify_candidate
    def verify_then_mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        source_report = root / "source-selftest.json"
        source_report.write_bytes(source_report.read_bytes() + b" ")
        return result
    monkeypatch.setattr(verify_candidate_bundle, "verify_candidate", verify_then_mutate)
    with pytest.raises(publisher.PublicationError, match="changed during preparation"):
        publisher.prepare_release(root, report, tmp_path / "upload", CONTEXT)



@pytest.mark.parametrize('other_version', ['1.7.0-rc.1', '1.7.0-rc.3'])
def test_other_candidate_marker_is_outside_publication_authorization(runner, other_version):
    from src.core.version import APP_VERSION
    env, event, path = runner
    assert publisher.VERSION == '1.7.0-rc.2'
    assert APP_VERSION == publisher.VERSION
    event['head_commit']['message'] = 'publish-prerelease: v' + other_version
    path.write_text(json.dumps(event))
    with pytest.raises(publisher.PublicationError, match='marker'):
        publisher.validate_runner_context(env)


@pytest.mark.parametrize('other_version', ['1.7.0-rc.1', '1.7.0-rc.3'])
def test_new_candidate_metadata_is_rejected_before_upload_preparation(candidate, tmp_path, other_version):
    root, report_path, _, _, _ = candidate
    report = json.loads(report_path.read_text())
    report['version'] = other_version
    report_path.write_text(json.dumps(report))
    with pytest.raises(publisher.PublicationError, match='does not match this release/source'):
        publisher.prepare_release(root, report_path, tmp_path / 'upload', CONTEXT)
    assert not (tmp_path / 'upload').exists()
