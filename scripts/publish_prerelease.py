"""Publish one approved candidate from same-run, hash-bound Windows evidence.

This program is deliberately specific to v1.8.0-rc.5. Merely preparing files
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
BRANCH_REF = "refs/heads/master"
TAG = "v1.8.0-rc.5"
VERSION = TAG[1:]
PUBLISH_MARKER = "publish-prerelease: " + TAG
API_BASE = "https://api.github.com/repos/" + REPOSITORY
API_VERSION = "2026-03-10"
EXE_NAME = "InkPick-" + TAG + ".exe"
ZIP_NAME = "InkPick-" + TAG + ".zip"
CHECKSUM_NAME = "InkPick-" + TAG + "-SHA256SUMS.txt"
METADATA_NAME = "InkPick-" + TAG + "-validation-metadata.zip"
README_NAME = "InkPick-" + TAG + "-README.txt"
MAX_GUIDE_BYTES = 256 * 1024
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
    return f"""## InkPick 拾字筆 {TAG} 候選測試版

本版修正設定下拉選單的文字對比與手寫時畫布跳動，並補齊文件套版的清除與縮放操作。

### 本次修正
- 設定下拉選單使用明確底色、文字與選取配色，避免系統配色讓選項看不清楚。
- 手寫候選區更新時保留高度，書寫中不再自動捲動，單筆筆畫也固定座標，避免畫布變動讓筆畫跳位。展開、收合及選字複製維持原操作。
- 文件套版新增「清除框選」：「所有頁共用」清掉所選欄位的共用框及各頁例外；「只改這一頁」只清當前頁，不會自動退回第一頁的位置。重新框選或按「此頁套用第一頁」後再辨識。
- 「清空文件」確認後清除匯入頁面、全部位置與結果，保留欄位名稱及文字模板；不刪原檔、不改剪貼簿。文件預覽新增可見的「−」「＋」、比例、「100%」及「拖曳移動」，手動縮放可跨頁及視窗大小變動保留。
- 更新 Pillow 13.0.0、setuptools 84.2.1 與建置用 pip 26.2.2，改用不含 OpenEXR 的 OpenCV 5.0.0.93 並排除不需的影片 DLL。Linux／Windows 的實際 Python 套件清單須通過 pip-audit；有漏洞或漏掃即阻止發布。
- 手寫回傳候選後回收輔助程序，新筆畫取消過期要求；OCR 閒置時停用 CPU 忙等。系統匣、快捷鍵及載入模型仍保留，完全結束請從系統匣退出。正在執行的原生 PDF／OCR 呼叫仍需先返回。

螢幕擷取仍先框選再辨識，預設自動複製全文，也能改成先選文字再複製。手寫、歷史與檔案位置功能保留；公開封裝繼續排除原始診斷、建置路徑與使用者資料。

### 文件套版：PDF／圖片批次辨識
- 點浮動窗「文件套版」，匯入 PDF 或 PNG／JPG／BMP／TIFF／WebP；預設每個檔案最多 100 MB，可在文件視窗調整。整批最多 500 頁、每次最多 32 個欄位。
- 在第一頁框選並命名欄位，例如「姓名」「金額」。後續頁預設沿用第一頁的相對位置；版型不同的頁面可單獨調整，未調整的欄位仍繼承第一頁。
- 模板可輸入 `收件人：{{姓名}}，金額：{{金額}}`，也支援全形大括號。每頁產生一段文字，以空行分隔；辨識結果可先核對與修正，再按「複製」或匯出 UTF-8 TXT。
- PDFium 與 OCR 模型已包在 EXE，匯入檔案在本機唯讀處理，不上傳、不改寫原始文件，也不自動複製套版結果。
- 加密 PDF、XFA 動態表單或過大頁面會拒絕匯入；請先解密或列印／另存為一般 PDF。相對位置套用不等於版面自動對齊，第二頁以後仍應核對。

### 下載與使用
- 適用 Windows 64 位元。建議下載可攜版 ZIP、解壓後執行；也可直接使用本頁的 EXE。
- 已內附執行環境、兩組 OCR 模型與手寫引擎，不會在首次啟動下載模型，也不需安裝 Windows 手寫語言套件。
- ZIP 內附 README.md、OFFLINE_README.txt、README.txt、文件套版圖文操作指南與六張示範介面圖，另含第三方授權說明及手寫模型來源。也可單獨下載 `{README_NAME}` 帶到離線電腦；其位元組與 ZIP 內的離線說明相同，並列入 SHA256SUMS。
- 使用選取模式時，辨識完成不會先動剪貼簿；按「複製」才寫入所選內容，取消不改剪貼簿，完整辨識結果仍保存在歷史。
- 手寫候選字只有點選後才會複製，不會自動輸入其他程式；較早開始的 OCR 不會蓋掉之後在拾字筆選字或手動複製的文字。
- 原有三項複製清理選項預設皆關閉，只影響複製內容。移除換行會把同一筆紀錄的多行文字接在一起，請按需要開啟。

### 請先知道的限制
- 手寫字表有 11,853 字，包含「龘」，不含「尞、𠮷、𠀋」。剪貼簿能保留擴充字，不代表模型能辨識；候選排序分數也不是正確率。
- 本版仍是候選測試版。尚未完成乾淨、斷網 Windows 10／11 的現場驗收，也沒有真人手寫準確率結果。
- 多螢幕與混合縮放比例仍待驗收；截圖目前使用主螢幕。既有 OCR 若在底層運算卡住，仍可能拖延關閉。
- 剪貼簿監看預設關閉；歷史及圖片以未加密檔案儲存。若已開啟 Windows 雲端剪貼簿同步，作業系統可能傳送複製的內容。

### 技術驗證與來源
發布檢查綁定下列同一組檔案雜湊與來源版本：Windows 原生測試、EXE 內的 OCR／資料庫／手寫／啟閉測試，以及主程式和同次解壓的手寫引擎都被阻擋對外連線時的測試。
兩組 OCR 模型各完成 500 次 EXE 推論。手寫的 16 筆模型原始筆跡重播只驗證基本運作，不能當作未見過的真人筆跡準確率；這些檢查也不證明零連線嘗試或零錯誤。
GitHub Windows 測試機不等於無安裝、無快取的乾淨電腦。獨立手寫程序有逾時終止與父程序退出檢查；既有 QThread 內的 OCR 卡住限制仍在。完整第三方授權審查尚未完成。
另外以單獨 EXE、全新資料與暫存目錄、僅含 Windows 系統路徑的 PATH、清除 Python／Qt 外部環境且阻擋主程式連線，執行 OCR、啟閉、兩種手寫及文件套版探針。來源／frozen／隔離模式的文件探針使用合成 PDF 實際載入 PDFium，驗證圖片渲染、欄位繼承／覆寫、模板合併與原檔唯讀；不冒稱 OCR 準確率或模型推論測試。這是可攜性檢查，不宣稱已在實體乾淨電腦驗收。

來源版本：`{context.source_commit}`
套件與背景工作範圍見 [資安檢查](https://github.com/maotai11/desktop-ocr-tool/blob/{context.source_commit}/docs/SECURITY_REVIEW.md)。Linux 五組合成操作未觀察到網際網路系統呼叫；這不代表所有 GUI 路徑或原生 DLL 已完成漏洞認證。
同次建置的測試報告與檔案對應，請見 validation metadata ZIP。

### 檔案 SHA256
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
                   "X-GitHub-Api-Version": API_VERSION, "User-Agent": "inkpick-candidate-publisher"}
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
        except (error.URLError, OSError, ValueError):
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
    require(len(prepared.assets) == 5
            and {a.name for a in prepared.assets} == {EXE_NAME, ZIP_NAME, CHECKSUM_NAME, METADATA_NAME, README_NAME},
            "Expected exactly five unique prepared release assets")
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


def public_validation_report(name: str, raw: bytes) -> bytes:
    """Keep raw verification local; exported reports contain fixed fields only.

    This protects the metadata asset only. The portable bundle still requires
    its own public-file review before a new publication can be enabled.
    """
    from scripts.prepare_public_validation import strict_json, safe_fields, junit_counts
    require(len(raw) <= MAX_JSON_BYTES, "Oversized public validation input")
    if name == "test-results.xml":
        try:
            counts = junit_counts(raw)
        except (ValueError, TypeError) as exc:
            raise PublicationError("Invalid public JUnit report") from exc
        # No testcase names, hostnames, properties, stdout or failure text.
        import xml.etree.ElementTree as ET
        suite = ET.Element("testsuite", {key: str(counts[key])
                           for key in ("tests", "failures", "errors", "skipped")})
        states = (["failure"] * counts["failures"] + ["error"] * counts["errors"]
                  + ["skipped"] * counts["skipped"])
        for index in range(counts["tests"]):
            case = ET.SubElement(suite, "testcase")
            if index < len(states):
                ET.SubElement(case, states[index])
        return ET.tostring(suite, encoding="utf-8", xml_declaration=True)
    try:
        original = strict_json(raw)
        require(isinstance(original, dict), "Invalid public validation object")
        result = safe_fields(original)
    except (ValueError, TypeError, RecursionError) as exc:
        raise PublicationError("Invalid public validation report") from exc
    result["original_report_sha256"] = hashlib.sha256(raw).hexdigest()
    result["original_report_bytes"] = len(raw)
    # This provenance is checked against the runner before preparation. Still
    # validate each field here so this helper cannot publish arbitrary strings.
    if name == "verified-bundle.json":
        workflow = original.get("workflow")
        require(isinstance(workflow, dict) and workflow.get("repository") == REPOSITORY,
                "Invalid public workflow provenance")
        require(COMMIT_RE.fullmatch(str(workflow.get("source_commit", ""))) is not None,
                "Invalid public workflow commit")
        for key in ("run_id", "run_attempt"):
            value = workflow.get(key)
            require(type(value) is str and value.isascii() and value.isdecimal()
                    and 0 < len(value) <= 20 and int(value) > 0,
                    "Invalid public workflow identifier")
        result["workflow"] = {key: workflow[key] for key in
                              ("repository", "run_id", "run_attempt", "source_commit")}
        result["workflow_run_id"] = workflow["run_id"]
        result["workflow_run_attempt"] = workflow["run_attempt"]
    return canonical_json(result)


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


def _check_document_probe(report: dict, manifest: dict, frozen: bool) -> None:
    """Require the fixed non-GUI PDFium/region/template lifecycle contract."""
    expected = {
        'schema': 1, 'probe': 'document_template_lifecycle', 'passed': True,
        'version': manifest['version'], 'frozen': frozen, 'platform': 'win32',
        'executable_sha256': manifest['executable_sha256'] if frozen else None,
        'clean_machine_verified': False, 'fixture_kind': 'SYNTHETIC_SOLID_RECTANGLES',
        'ocr_accuracy_tested': False, 'model_inference_tested': False,
    }
    require(type(report) is dict and all(type(report.get(key)) is type(value)
            and report.get(key) == value for key, value in expected.items()),
            'Incomplete document lifecycle identity')
    checks = report.get('checks')
    required = {'pdf_rendering', 'image_rendering', 'region_inheritance',
                'page_override', 'template_merge', 'source_unchanged', 'temporary_files_cleaned'}
    require(type(checks) is dict and set(checks) == required
            and all(value is True for value in checks.values()),
            'Incomplete document lifecycle checks')
    require(not any(key in report for key in ('error', 'error_code', 'traceback')),
            'Document lifecycle includes a failure diagnostic')


def check_portable_profile(snapshots: Mapping[str, bytes], manifest: dict) -> None:
    """Bind standalone/empty-profile probes to the exact candidate EXE."""
    from scripts.prepare_public_validation import strict_json
    from scripts.verify_candidate_bundle import (
        _check_probe, check_handwriting_image_probe, check_handwriting_probe,
    )
    prefix = 'artifacts/portable-validation/'
    try:
        gate = strict_json(snapshots[prefix + 'isolated-profile-gate.json'])
        require(type(gate) is dict and gate.get('schema') == 1
                and gate.get('probe') == 'isolated_profile' and gate.get('passed') is True
                and gate.get('clean_machine_verified') is False
                and gate.get('version') == VERSION
                and gate.get('executable_sha256') == manifest['executable_sha256'],
                'Incomplete portable-profile gate identity')
        require(all(gate.get(key) is True for key in (
            'profile_empty', 'copied_executable_only', 'path_isolated',
            'python_env_cleared', 'qt_env_cleared', 'outer_network_blocked')),
            'Incomplete portable-profile isolation')
        expected = {}
        for name, check in (
            ('isolated-selftest.json', _check_probe),
            ('isolated-app-smoke.json', _check_probe),
            ('isolated-handwriting.json', check_handwriting_probe),
            ('isolated-handwriting-image.json', check_handwriting_image_probe),
            ('isolated-documents.json', _check_document_probe),
        ):
            raw = snapshots[prefix + name]
            report = strict_json(raw)
            check(report, manifest, True)
            expected[name] = {'status': 'PASSED', 'sha256': hashlib.sha256(raw).hexdigest()}
        require(gate.get('probes') == expected, 'Portable-profile report hashes differ')
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise PublicationError('Invalid portable-profile evidence') from exc


def prepare_release(artifact_root: Path, verified_bundle: Path, output_dir: Path,
                    context: RunnerContext) -> PreparedRelease:
    """Reverify downloaded bytes, then prepare five immutable local upload files.

    This is a credential-free/network-free function. The saved verifier's
    PyInstaller payload inspection is bound by the independently rehashed ZIP
    and EXE. Everything else must equal the freshly recomputed verification.
    """
    # Running as ``python scripts/publish_prerelease.py`` must work without pip.
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from scripts.verify_candidate_bundle import verify_candidate, verify_handwriting_source_distribution
    from scripts.public_release_bundle import approved_release_files, prepare_public_bundle
    from scripts.archive_paths import validate_zip_names

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
    external_names = ["source-selftest.json", "source-app-smoke.json", "source-handwriting.json", "source-handwriting-image.json", "test-results.xml",
                      "artifacts/security-audit.json", "artifacts/dependency-inventory.json",
                      "artifacts/firewall-validation/candidate-gate.json",
                      "artifacts/firewall-validation/frozen-selftest.json",
                      "artifacts/firewall-validation/frozen-app-smoke.json",
                      "artifacts/firewall-validation/frozen-handwriting.json",
                      "artifacts/firewall-validation/frozen-handwriting-image.json",
                      "artifacts/firewall-validation/handwriting-firewall-gate.json",
                      "artifacts/firewall-validation/frozen-runtime-stress.json",
                      "artifacts/portable-validation/isolated-profile-gate.json",
                      "artifacts/portable-validation/isolated-selftest.json",
                      "artifacts/portable-validation/isolated-app-smoke.json",
                      "artifacts/portable-validation/isolated-handwriting.json",
                      "artifacts/portable-validation/isolated-handwriting-image.json",
                      "artifacts/portable-validation/isolated-documents.json"]
    snapshot_names = ["artifacts/verified-bundle.json", *external_names,
                      "artifacts/candidate-validation/frozen-selftest.json",
                      "artifacts/candidate-validation/frozen-app-smoke.json",
                      "artifacts/candidate-validation/frozen-handwriting.json",
                      "artifacts/candidate-validation/frozen-handwriting-image.json",
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
            and payload.get("embedded_handwriting_verified") is True
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
    audit = json.loads(snapshots['artifacts/security-audit.json'])
    inventory_bytes = snapshots['artifacts/dependency-inventory.json']
    inventory_hash = hashlib.sha256(inventory_bytes).hexdigest()
    installed = json.loads(inventory_bytes).get('installed')
    require(type(audit) is dict and type(audit.get('schema')) is int and audit.get('schema') == 1
            and audit.get('probe') == 'dependency_vulnerabilities'
            and audit.get('passed') is True
            and audit.get('source_commit') == context.source_commit
            and audit.get('scope') == 'PYTHON_INSTALLED_DISTRIBUTIONS'
            and audit.get('scanner') == 'pip-audit'
            and audit.get('scanner_version') == '2.10.1'
            and audit.get('dependency_inventory_sha256') == inventory_hash
            and type(installed) is dict and bool(installed)
            and type(audit.get('packages_count')) is int and audit.get('packages_count') == len(installed)
            and type(audit.get('vulnerabilities_count')) is int
            and audit['vulnerabilities_count'] == 0 and audit.get('findings') == []
            and 'error_code' not in audit,
            'Missing, inconsistent or failed dependency vulnerability audit')
    if 'dependency_inventory' in manifest:
        require(manifest['dependency_inventory'].get('sha256') == inventory_hash,
                'Security audit inventory differs from candidate')
    check_portable_profile(snapshots, manifest)
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
    try:
        approved = approved_release_files(
            executable=EXE_NAME, executable_sha256=actual["executable_sha256"],
            identity=manifest["handwriting"], source_files=source["files"])
    except ValueError as exc:
        raise PublicationError('Missing or invalid committed public files') from exc
    metadata_files = {"verified-bundle.json": canonical_json(saved)}
    for name in external_names:
        metadata_files[name] = snapshots[name]
    with zipfile.ZipFile(bundle) as archive:
        try:
            validate_zip_names(archive.infolist())
        except ValueError as exc:
            raise PublicationError('Unsafe ZIP path before output preparation') from exc
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate member in portable ZIP")
        require(all((info.external_attr >> 16) & 0o170000 != 0o120000
                    for info in archive.infolist()), "Symlink member in portable ZIP")
        for name in ["BUILD_MANIFEST.json", "SOURCE_MANIFEST.json",
                     "candidate-validation/frozen-selftest.json", "candidate-validation/frozen-app-smoke.json",
                     "candidate-validation/frozen-handwriting.json", "candidate-validation/frozen-handwriting-image.json"]:
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
    public_bundle = prepare_public_bundle(
        bundle, output_dir / ZIP_NAME, prefix=prefix, approved=approved,
        executable=EXE_NAME, candidate_sha256=actual["bundle_sha256"],
        executable_sha256=actual["executable_sha256"], source_commit=context.source_commit)
    with zipfile.ZipFile(output_dir / ZIP_NAME) as archive:
        public_sources = verify_handwriting_source_distribution(
            archive, prefix, manifest["handwriting"], source["files"])
        guide_info = archive.getinfo(prefix + 'OFFLINE_README.txt')
        require(0 < guide_info.file_size <= MAX_GUIDE_BYTES, 'Offline guide is missing or oversized')
        guide_bytes = archive.read(guide_info)
        require(hashlib.sha256(guide_bytes).hexdigest() == approved['OFFLINE_README.txt'],
                'Offline guide differs from committed public source')
        try:
            guide_bytes.decode('utf-8-sig')
        except UnicodeError as exc:
            raise PublicationError('Offline guide is not UTF-8 text') from exc
        with (output_dir / README_NAME).open('xb') as stream:
            stream.write(guide_bytes)
    require(public_sources == actual["handwriting_source_distribution"],
            "Public source distribution differs from verified candidate")
    exe = Asset.from_file(output_dir / EXE_NAME, "application/octet-stream")
    portable = Asset.from_file(output_dir / ZIP_NAME, "application/zip")
    require(exe.sha256 == actual["executable_sha256"] and exe.size == actual["executable_bytes"]
            and portable.sha256 == public_bundle["public_bundle_sha256"]
            and portable.size == public_bundle["public_bundle_bytes"],
            "Prepared ZIP/EXE bytes differ from verified public bundle")
    require(all((root / name).read_bytes() == raw for name, raw in snapshots.items()),
            "Validation evidence changed during preparation")
    deterministic_metadata_zip(output_dir / METADATA_NAME,
                               {name: public_validation_report(name, data)
                                for name, data in metadata_files.items()})
    evidence = Asset.from_file(output_dir / METADATA_NAME, "application/zip")
    guide = Asset.from_file(output_dir / README_NAME, 'text/plain')
    checksum = output_dir / CHECKSUM_NAME
    with checksum.open("x", encoding="ascii", newline="\n") as stream:
        stream.write("".join(f"{asset.sha256}  {asset.name}\n" for asset in (exe, portable, evidence, guide)))
    sums = Asset.from_file(checksum, "text/plain")
    assets = (exe, portable, sums, evidence, guide)
    return PreparedRelease(context, assets, release_notes(context, assets))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--public-input", type=Path)
    parser.add_argument("--verified-bundle", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prepare-only", action="store_true", help="Verify/prepare without reading GH_TOKEN or using network")
    args = parser.parse_args(argv)
    try:
        context = validate_runner_context(os.environ)
        if args.public_input is not None:
            require(args.artifact_root is None and args.verified_bundle is None,
                    "Conflicting public/private artifact inputs")
            project_root = str(Path(__file__).resolve().parents[1])
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from scripts.public_release_receipt import load_prepared
            prepared = load_prepared(args.public_input, context)
            require(not args.output_dir.exists() and not args.output_dir.is_symlink()
                    and not any(x.is_symlink() for x in args.output_dir.parents),
                    "Publication evidence output must be fresh")
            args.output_dir.mkdir(parents=True, exist_ok=False)
        else:
            require(args.artifact_root is not None and args.verified_bundle is not None,
                    "Missing private candidate inputs")
            prepared = prepare_release(args.artifact_root, args.verified_bundle, args.output_dir, context)
        if args.prepare_only:
            print("Five candidate assets prepared; no network or token access")
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
