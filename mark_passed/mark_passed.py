#!/usr/bin/env python3
"""
mark_passed.py — 从 device.json 读取 IP，自动从 DUT 采集设备信息，
向 TestRail 批量写入 Passed 结果，不执行任何测试，elapsed 留空。

支持三种目标范围（与 roku_test 参数对齐）：
  --testrail-run       对某个 run 下的全部 case 写 Passed
  --testrail-plan      对某个 plan 下所有 run 的全部 case 写 Passed
  --testrail-milestone 对某个 milestone 下所有 run/plan 的全部 case 写 Passed

用法示例：
  python mark_passed.py -d device.json --testrail-run 239549
  python mark_passed.py -d device.json --testrail-plan 98765 --dry-run
  python mark_passed.py -d device.json --testrail-milestone 512 --testrail-project 24 -v
"""

import argparse
import io as _io
import json
import os as _os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── 将 roku_automation 加入 sys.path，复用框架的 TestRailClient ──────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
for _candidate in [
    _SCRIPT_DIR / "roku_automation",
    _SCRIPT_DIR.parent / "roku_automation",
    _SCRIPT_DIR.parent.parent / "roku_automation",
    Path.cwd() / "roku_automation",
]:
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate.parent))  # 插入 roku_automation 的父目录
        break

try:
    from roku_common.testrail_client import TestRailClient as _FrameworkTRClient
    _USE_FRAMEWORK_CLIENT = True
except ImportError:
    _USE_FRAMEWORK_CLIENT = False

# ──────────────────────────────────────────────────────────────────────────────
# Terminal helpers
# ──────────────────────────────────────────────────────────────────────────────

if sys.platform == "win32":
    sys.stdout = _io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    sys.stderr = _io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

_USE_COLOR = sys.stdout.isatty() and (
    sys.platform != "win32"
    or "WT_SESSION" in _os.environ
    or "TERM" in _os.environ
)


def _c(t: str, code: str) -> str:
    return f"\033[{code}m{t}\033[0m" if _USE_COLOR else t


def green(t: str) -> str:  return _c(str(t), "32")
def red(t: str) -> str:    return _c(str(t), "31")
def yellow(t: str) -> str: return _c(str(t), "33")
def cyan(t: str) -> str:   return _c(str(t), "36")
def bold(t: str) -> str:   return _c(str(t), "1")
def dim(t: str) -> str:    return _c(str(t), "2")


TICK  = green("[OK]")
CROSS = red("[!!]")
WARN  = yellow("[??]")

# ──────────────────────────────────────────────────────────────────────────────
# TestRail constants
# ──────────────────────────────────────────────────────────────────────────────

STATUS_PASSED = 1
TR_PAGE_SIZE  = 250
TESTRAIL_DEFAULT_URL = "https://testrail.eswat.roku.com/testrail/index.php?/api/v2/"

# ──────────────────────────────────────────────────────────────────────────────
# ECP device info fetcher
# ──────────────────────────────────────────────────────────────────────────────

ECP_PORT    = 8060
ECP_TIMEOUT = 10   # seconds


@dataclass
class DeviceInfo:
    """All fields that can be fetched from ECP or device.json."""
    ip_address: str = ""

    # From /query/device-info
    serial_number:    str = ""
    device_id:        str = ""
    model_number:     str = ""
    vendor_name:      str = ""   # brand name
    screen_size:      str = ""   # inches, e.g. "55"
    is_tv:            bool = False
    build_number:     str = ""   # firmware version in TeamCity format
    platform_version: str = ""   # software-version (e.g. "14.0.0")
    manufacturer:     str = ""   # hardware manufacturer
    brand_name:       str = ""   # brand name (odmIdStr / vendor-name)
    storage_type:     str = ""   # storageType ECP attribute
    storage_model:    str = ""   # storageInfo ECP attribute
    account_region:   str = ""   # country ECP attribute
    project_id:       str = ""   # projectid from persistent config
    is_linked:        bool = False  # Connected Mode

    # From /query/device/secret
    model_region: str = ""

    # From /query/plugins/secret  (lib name -> version)
    libraries: dict = field(default_factory=dict)

    def describe(self) -> list[str]:
        """Return list of printable key: value lines for display."""
        rows = [
            ("ip_address",      self.ip_address),
            ("serial_number",   self.serial_number),
            ("device_id",       self.device_id),
            ("model_number",    self.model_number),
            ("vendor_name",     self.vendor_name),
            ("model_region",    self.model_region),
            ("platform_version",self.platform_version),
            ("build_number",    self.build_number),
            ("is_tv",           str(self.is_tv)),
            ("screen_size",     self.screen_size if self.is_tv else "(n/a)"),
        ]
        return [f"  {k:<18}: {v}" for k, v in rows if v and v != "(n/a)"]


def _ecp_get_xml(ip: str, path: str) -> ET.Element | None:
    url = f"http://{ip}:{ECP_PORT}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/xml"})
    try:
        with urllib.request.urlopen(req, timeout=ECP_TIMEOUT) as resp:
            return ET.fromstring(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _tag(root: ET.Element, name: str, default: str = "") -> str:
    el = root.find(name)
    return (el.text or "").strip() if el is not None else default


def _tag_bool(root: ET.Element, name: str) -> bool:
    return _tag(root, name).lower() == "true"


def _ecp_get_json(ip: str, path: str) -> dict | None:
    url = f"http://{ip}:{ECP_PORT}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=ECP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def fetch_device_info(ip: str) -> DeviceInfo:
    """
    Hit multiple ECP endpoints to collect all fields needed for the TestRail comment.
    Mirrors roku_device.py _store_testrail_device_info().
    """
    info = DeviceInfo(ip_address=ip)

    # ── /query/device-info (public ECP) ──────────────────────────────────────
    root = _ecp_get_xml(ip, "/query/device-info")
    if root is None:
        raise ConnectionError(
            f"无法连接到设备 ECP ({ip}:{ECP_PORT})。\n"
            f"请检查 IP 地址是否正确、设备是否开机联网。"
        )

    info.serial_number    = _tag(root, "serial-number")
    info.device_id        = _tag(root, "device-id")
    info.model_number     = _tag(root, "model-number")
    info.vendor_name      = _tag(root, "vendor-name")
    info.manufacturer     = _tag(root, "manufacturer") or info.vendor_name
    info.brand_name       = _tag(root, "vendor-name")
    info.screen_size      = _tag(root, "screen-size")
    info.is_tv            = _tag_bool(root, "is-tv")
    info.build_number     = _tag(root, "build-number")
    info.platform_version = _tag(root, "software-version") or _tag(root, "software-build")
    info.storage_type     = _tag(root, "storage-type") or _tag(root, "storageType")
    info.storage_model    = _tag(root, "storage-model") or _tag(root, "storageInfo")
    info.account_region   = _tag(root, "country")
    info.is_linked        = _tag_bool(root, "is-device-registered")

    # ── /query/device/secret (private ECP) ───────────────────────────────────
    priv = _ecp_get_xml(ip, "/query/device/secret")
    if priv is not None:
        info.model_region = _tag(priv, "modelRegion")
        # project_id from persistent config not available via ECP; try secret screen
        info.project_id   = _tag(priv, "projectId") or _tag(priv, "projectid")

    # ── /query/plugins/secret — installed libraries ───────────────────────────
    # 框架判断依据：plugin["title"].lower() 包含 important_plugins 中的关键词
    IMPORTANT_PLUGINS = [
        "4k spotlight channel", "acr", "airplay", "dfp", "drm",
        "ecosystem", "epop", "freeview play", "freeview uk",
        "gdpr modular legal agreements", "grand central", "luna",
        "live tv", "my offers", "roku ads library", "roku analytics library",
        "roku browser", "roku cfui home screen", "roku dynamic menu",
        "roku featured free prod", "roku legal agreements", "roku livetv",
        "roku pay", "roku search gc", "roku titan library",
        "roku ui data", "the roku channel",
    ]
    plugins_root = _ecp_get_xml(ip, "/query/plugins/secret")
    if plugins_root is not None:
        libs: dict[str, str] = {}
        for child in plugins_root:
            # 每个 child 是 <plugin> 或包含 plugin 信息的容器
            # 框架读法：子元素 text 构成 dict
            plugin_info: dict[str, str] = {}
            for el in child:
                if el.text:
                    plugin_info[el.tag] = el.text.strip()
            title = plugin_info.get("title", "")
            version = plugin_info.get("version", "")
            if title and any(kw in title.lower() for kw in IMPORTANT_PLUGINS):
                libs[title] = version
        info.libraries = dict(sorted(libs.items()))

    return info


# ──────────────────────────────────────────────────────────────────────────────
# device.json loader
# ──────────────────────────────────────────────────────────────────────────────

def load_ip_from_device_json(path: Path) -> str:
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Support {"roku": {...}} or {"rokus": {"name": {...}}} or bare spec
    spec: dict
    if "roku" in raw:
        spec = raw["roku"]
    elif "rokus" in raw:
        spec = next(iter(raw["rokus"].values()))
    else:
        spec = raw
    ip = spec.get("ip_address", "")
    if not ip:
        raise ValueError("device.json 中未找到 ip_address 字段")
    return ip


# ──────────────────────────────────────────────────────────────────────────────
# Comment builder  (mirrors roku_device.py _store_testrail_device_info)
# ──────────────────────────────────────────────────────────────────────────────

def build_comment(info: DeviceInfo) -> str:
    """Mirrors _store_testrail_device_info() + tearDown library table."""
    lines: list[str] = []

    # 基本设备信息（与框架顺序一致）
    if info.serial_number:
        lines.append(f"ESN: {info.serial_number}")
    if info.device_id:
        lines.append(f"Device ID: {info.device_id}")
    if info.model_number:
        lines.append(f"Roku Model: {info.model_number}")
    if info.project_id:
        lines.append(f"Project ID: {info.project_id}")
    if info.model_region:
        lines.append(f"Model Region: {info.model_region}")
    if info.account_region:
        lines.append(f"Account Region: {info.account_region}")

    # Device Info: platform_version [manufacturer] [brand] [size"]
    device_info_parts: list[str] = []
    if info.platform_version:
        device_info_parts.append(info.platform_version)
    if info.manufacturer and info.manufacturer.lower() not in ("roku", ""):
        device_info_parts.append(info.manufacturer)
    if info.brand_name and info.brand_name.lower() not in ("roku", "") \
            and info.brand_name != info.manufacturer:
        device_info_parts.append(info.brand_name)
    if info.is_tv and info.screen_size:
        device_info_parts.append(f'{info.screen_size}"')
    if device_info_parts:
        lines.append(f"Device Info: {' '.join(device_info_parts)}")

    # Storage
    if info.storage_type:
        lines.append(f"Storage Type: {info.storage_type}")
    if info.storage_model:
        lines.append(f"Storage Model: {info.storage_model}")

    # Connected Mode（在 tearDown 末尾追加，与框架一致）
    lines.append(f"Connected Mode: {info.is_linked}")

    # Library 表格（TestRail Markdown 格式）
    if info.libraries:
        table = ["||| :Library | :Version"]
        table.extend(f"|| {lib} | {ver}" for lib, ver in info.libraries.items())
        lines.append("\n".join(table))

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Result payload builder
# ──────────────────────────────────────────────────────────────────────────────

def build_result(info: DeviceInfo, case_id: int) -> dict:
    """
    Mirrors the framework's test_results dict for a passing test.
    elapsed is intentionally omitted (left empty per requirement).
    """
    result: dict[str, Any] = {
        "case_id":   case_id,
        "status_id": STATUS_PASSED,
        # elapsed: not set — TestRail treats absent field as empty
    }
    comment = build_comment(info)
    if comment:
        result["comment"] = comment
    if info.build_number:
        result["version"] = info.build_number   # already TeamCity format from ECP
    return result


# ──────────────────────────────────────────────────────────────────────────────
# TestRail client wrapper
# ──────────────────────────────────────────────────────────────────────────────

class TestRailError(Exception):
    pass


class TestRailClient:
    """薄封装：优先使用框架 TestRailClient，否则回退到 stdlib 实现。"""

    def __init__(self, base_url: str, user: str = "", password: str = "") -> None:
        if _USE_FRAMEWORK_CLIENT:
            # 框架会自动读 user_config.json / .ra_config，
            # 并根据 partner_testrail=true 切换到正确的 URL
            self._fw = _FrameworkTRClient(url=None)
            self._fw_mode = True
        else:
            # 回退：stdlib Basic auth
            import base64 as _b64
            self.base_url = base_url.rstrip("/") + "/"
            creds = _b64.b64encode(f"{user}:{password}".encode()).decode()
            self._auth = f"Basic {creds}"
            self._fw_mode = False

    # ── stdlib 回退方法 ────────────────────────────────────────────────────────
    def _request(self, method: str, endpoint: str, body: dict | None = None) -> Any:
        url = self.base_url + endpoint
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Authorization": self._auth, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            txt = exc.read().decode(errors="replace")
            raise TestRailError(f"HTTP {exc.code} {method} {url}: {txt[:300]}") from exc
        except urllib.error.URLError as exc:
            raise TestRailError(f"连接错误 {url}: {exc.reason}") from exc

    def _get(self, ep: str) -> Any:
        return self._request("GET", ep)

    def _post(self, ep: str, body: dict) -> Any:
        return self._request("POST", ep, body)

    def _get_paginated(self, ep: str, key: str) -> list[dict]:
        results: list[dict] = []
        offset = 0
        while True:
            paged = f"{ep}&limit={TR_PAGE_SIZE}&offset={offset}"
            data = self._get(paged)
            if isinstance(data, dict):
                items = data.get(key, [])
                results.extend(items)
                if not data.get("_links", {}).get("next") or len(items) < TR_PAGE_SIZE:
                    break
            else:
                results.extend(data)
                if len(data) < TR_PAGE_SIZE:
                    break
            offset += TR_PAGE_SIZE
        return results

    # ── 统一接口 ──────────────────────────────────────────────────────────────
    def get_run(self, run_id: int) -> dict:
        if self._fw_mode:
            return self._fw.get_run(run_id)
        return self._get(f"get_run/{run_id}")

    def get_plan(self, plan_id: int) -> dict:
        if self._fw_mode:
            return self._fw.get_plan(plan_id)
        return self._get(f"get_plan/{plan_id}")

    def get_milestone(self, ms_id: int) -> dict:
        if self._fw_mode:
            return self._fw.get_milestone(ms_id)
        return self._get(f"get_milestone/{ms_id}")

    def get_tests(self, run_id: int) -> list[dict]:
        if self._fw_mode:
            return self._fw.get_tests(run_id)
        return self._get_paginated(f"get_tests/{run_id}?", "tests")

    def get_runs_for_plan(self, plan_id: int) -> list[dict]:
        if self._fw_mode:
            return self._fw.get_runs_from_plan(plan_id)
        plan = self.get_plan(plan_id)
        runs: list[dict] = []
        for entry in plan.get("entries", []):
            runs.extend(entry.get("runs", []))
        return runs

    def get_runs_for_milestone(self, project_id: int, ms_id: int) -> list[dict]:
        if self._fw_mode:
            return self._fw.get_runs_from_milestone(project_id=project_id, milestone_id=ms_id)
        runs = self._get_paginated(f"get_runs/{project_id}?milestone_id={ms_id}", "runs")
        plans = self._get_paginated(f"get_plans/{project_id}?milestone_id={ms_id}", "plans")
        for plan in plans:
            runs.extend(self.get_runs_for_plan(plan["id"]))
        return runs

    def add_results_for_cases(self, run_id: int, results: list[dict]) -> list[dict]:
        if self._fw_mode:
            # 框架 add_result_for_run 期望 {case_id: result_dict}，这里转换
            test_results = {r["case_id"]: {k: v for k, v in r.items() if k != "case_id"}
                            for r in results}
            self._fw.add_result_for_run(run_id=run_id, test_results=test_results, has_ids=True)
            return []
        return self._post(f"add_results_for_cases/{run_id}", {"results": results})

    def add_result_for_case(self, run_id: int, case_id: int, result: dict) -> dict:
        if self._fw_mode:
            return self._fw.add_result_for_case(run_id=run_id, case_id=case_id,
                                                 test_results=result)
        return self._post(f"add_result_for_case/{run_id}/{case_id}", result)


# ──────────────────────────────────────────────────────────────────────────────
# Post results for one run
# ──────────────────────────────────────────────────────────────────────────────

BULK_SIZE = 250


@dataclass
class RunSummary:
    run_id:   int
    run_name: str
    total:    int = 0
    posted:   int = 0
    skipped:  int = 0
    errors:   list[str] = field(default_factory=list)


def post_run(
    client:   TestRailClient,
    run_id:   int,
    run_name: str,
    info:     DeviceInfo,
    dry_run:  bool,
    verbose:  bool,
    filter_test_ids: set[int] | None = None,   # --test-ids 白名单（对应 /tests/view/ID），None=不过滤
    only_untested:   bool = False,              # --only-untested
) -> RunSummary:
    summary = RunSummary(run_id=run_id, run_name=run_name)

    try:
        tests = client.get_tests(run_id)
    except Exception as exc:
        summary.errors.append(f"get_tests 失败: {exc}")
        return summary

    # status_id=3 是 Untested（testrail_client.py test_statuses）
    UNTESTED = 3

    original_count = len(tests)
    if filter_test_ids is not None:
        tests = [t for t in tests if t["id"] in filter_test_ids]
        skipped_by_id = original_count - len(tests)
    else:
        skipped_by_id = 0

    if only_untested:
        before = len(tests)
        tests = [t for t in tests if t.get("status_id", UNTESTED) == UNTESTED]
        skipped_by_status = before - len(tests)
    else:
        skipped_by_status = 0

    case_ids = [t["case_id"] for t in tests]
    summary.total = len(case_ids)

    filter_notes: list[str] = []
    if skipped_by_id:
        filter_notes.append(f"{skipped_by_id} 不在 --test-ids 中")
    if skipped_by_status:
        filter_notes.append(f"{skipped_by_status} 已有结果（--only-untested）")
    filter_str = f"  {dim('(' + '，'.join(filter_notes) + ' 已跳过)')}" if filter_notes else ""

    if not case_ids:
        print(f"  {WARN}  Run #{run_id} \"{run_name}\" — 无符合条件的 case，跳过{filter_str}")
        return summary

    print(f"  {cyan(bold(f'Run #{run_id}'))}  \"{run_name}\"  ({summary.total} cases){filter_str}")

    if dry_run:
        print(f"    {dim('[dry-run] 将写入')} {summary.total} 条 Passed 结果")
        summary.posted = summary.total
        return summary

    results = [build_result(info, cid) for cid in case_ids]
    chunks = [results[i:i + BULK_SIZE] for i in range(0, len(results), BULK_SIZE)]

    for idx, chunk in enumerate(chunks):
        start = idx * BULK_SIZE + 1
        end   = start + len(chunk) - 1
        label = f"cases {start}–{end}"
        try:
            resp = client.add_results_for_cases(run_id, chunk)
            n = len(resp) if isinstance(resp, list) else len(chunk)
            summary.posted += n
            print(f"    {TICK}  {label}  →  {n} 条已写入", flush=True)
            if verbose:
                for item in chunk:
                    print(f"      {dim(str(item['case_id']))}  status=1  version={info.build_number}")
        except Exception as exc:
            msg = f"批量提交失败 ({label}): {exc}"
            summary.errors.append(msg)
            print(f"    {WARN}  {yellow(msg)}")
            print(f"    {dim('逐条重试中...')}")
            for item in chunk:
                cid = item["case_id"]
                single = {k: v for k, v in item.items() if k != "case_id"}
                try:
                    client.add_result_for_case(run_id, cid, single)
                    summary.posted += 1
                    if verbose:
                        print(f"      {TICK}  case {cid}")
                except TestRailError as exc2:
                    summary.skipped += 1
                    summary.errors.append(f"case {cid}: {exc2}")
                    print(f"      {CROSS}  case {cid}  {red(str(exc2)[:120])}")

        if idx < len(chunks) - 1:
            time.sleep(0.3)

    return summary


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mark_passed.py",
        description="从 device.json 读 IP，从 DUT 自动采集设备信息，向 TestRail 批量写 Passed 结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  python mark_passed.py -d device.json --testrail-run 239549
  python mark_passed.py -d device.json --testrail-plan 98765 --dry-run
  python mark_passed.py -d device.json --testrail-milestone 512 --testrail-project 24 -v

凭据自动读取优先级:
  1. ~/.ra_config  [Roku Automation]  testrail_user / testrail_pass
  2. roku_automation/shared_resources/user_config.json  (MTK 默认账号)
  3. 环境变量 TESTRAIL_USER / TESTRAIL_PASSWORD
""",
    )

    # ── device.json ───────────────────────────────────────────────────────────
    parser.add_argument(
        "-d", "--device", required=True, type=Path, metavar="device.json",
        help="设备配置文件（与 roku_test -d 相同）",
    )

    # ── TestRail target ───────────────────────────────────────────────────────
    target = parser.add_argument_group("目标范围（三选一）")
    target_ex = target.add_mutually_exclusive_group(required=True)
    target_ex.add_argument("--testrail-run",       type=int, metavar="RUN_ID")
    target_ex.add_argument("--testrail-plan",      type=int, metavar="PLAN_ID")
    target_ex.add_argument("--testrail-milestone", type=int, metavar="MILESTONE_ID")
    parser.add_argument(
        "--testrail-project", type=int, metavar="PROJECT_ID",
        help="Project ID（--testrail-milestone 时必填）",
    )

    # ── TestRail connection ───────────────────────────────────────────────────
    conn = parser.add_argument_group("TestRail 连接")
    conn.add_argument(
        "--testrail-url", default=TESTRAIL_DEFAULT_URL, metavar="URL",
        help=f"TestRail 地址（默认: {TESTRAIL_DEFAULT_URL}）",
    )

    # ── Filter ────────────────────────────────────────────────────────────────
    flt = parser.add_argument_group("过滤（可选，可同时使用）")
    flt.add_argument(
        "--test-ids", metavar="ID,ID,...",
        help="只对指定 test ID（逗号分隔，即 /tests/view/XXXXX 里的数字）写 Passed",
    )
    flt.add_argument(
        "--only-untested", action="store_true",
        help="只对当前状态为 Untested 的 case 写 Passed，已有结果的不覆盖",
    )

    # ── Behavior ──────────────────────────────────────────────────────────────
    parser.add_argument(
        "--dry-run", action="store_true",
        help="预演模式：采集设备信息并显示，但不实际写入 TestRail",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="详细输出：逐条打印每个 case 的写入结果",
    )

    args = parser.parse_args()

    # ── Validate ──────────────────────────────────────────────────────────────
    if args.testrail_milestone and not args.testrail_project:
        parser.error("--testrail-milestone 需要同时指定 --testrail-project")

    # Parse --test-ids into a set[int]
    filter_test_ids: set[int] | None = None
    if args.test_ids:
        try:
            filter_test_ids = {int(x.strip()) for x in args.test_ids.split(",") if x.strip()}
        except ValueError:
            parser.error("--test-ids 格式错误，请使用逗号分隔的纯数字，如 12187090,12187091")

    if not args.device.exists():
        print(red(f"[!!] device.json 不存在: {args.device}"), file=sys.stderr)
        return 2

    # ── Banner ────────────────────────────────────────────────────────────────
    print()
    print(bold("=" * 64))
    print(bold("   mark_passed.py  --  TestRail Passed 结果批量写入"))
    print(bold("=" * 64))
    if args.dry_run:
        print(f"  {WARN}  {yellow(bold('预演模式（dry-run）— 不会写入任何结果'))}")
    if filter_test_ids is not None:
        print(f"  过滤 test   :  {cyan(str(len(filter_test_ids)))} 个指定 ID")
    if args.only_untested:
        print(f"  过滤状态    :  {cyan('只写 Untested')}")

    # ── Step 1: 读取 device.json ──────────────────────────────────────────────
    print()
    print(bold("  Step 1 · 读取 device.json"))
    try:
        ip = load_ip_from_device_json(args.device)
    except (json.JSONDecodeError, ValueError, StopIteration, KeyError) as exc:
        print(red(f"  {CROSS} 解析 device.json 失败: {exc}"), file=sys.stderr)
        return 2
    print(f"  {TICK}  ip_address = {cyan(ip)}")

    # ── Step 2: 从 DUT 采集设备信息 ───────────────────────────────────────────
    print()
    print(bold("  Step 2 · 从 DUT 采集设备信息"))
    print(f"  正在连接 {cyan(ip)}:{ECP_PORT} ...", flush=True)
    try:
        info = fetch_device_info(ip)
    except ConnectionError as exc:
        print(red(f"  {CROSS} {exc}"), file=sys.stderr)
        return 2

    for line in info.describe():
        print(f"  {TICK}{line}")

    # ── Step 3: 预览写入字段 ──────────────────────────────────────────────────
    print()
    print(bold("  Step 3 · 写入字段预览"))
    print(f"    status_id  :  {STATUS_PASSED}  (Passed)")
    print(f"    elapsed    :  (留空)")
    if info.build_number:
        print(f"    version    :  {info.build_number}")
    comment = build_comment(info)
    if comment:
        print(f"    comment    :")
        for line in comment.splitlines():
            print(f"                 {line}")

    # ── Step 4: 查询 TestRail 目标 ────────────────────────────────────────────
    print()
    print(bold("  Step 4 · 查询 TestRail"))
    if _USE_FRAMEWORK_CLIENT:
        print(f"  {dim('模式:')} 框架 TestRailClient（自动读取凭据与服务器地址）")
    else:
        print(f"  {dim('模式:')} stdlib 回退  {dim('地址:')} {args.testrail_url}")
    try:
        client = TestRailClient(args.testrail_url)
    except Exception as exc:
        print(red(f"  {CROSS} 初始化 TestRail 客户端失败: {exc}"), file=sys.stderr)
        return 2

    runs: list[tuple[int, str]] = []   # (run_id, run_name)

    try:
        if args.testrail_run:
            run_info = client.get_run(args.testrail_run)
            runs = [(args.testrail_run, run_info.get("name", str(args.testrail_run)))]
            print(f"  {TICK}  Run #{args.testrail_run}  \"{runs[0][1]}\"")

        elif args.testrail_plan:
            plan_info = client.get_plan(args.testrail_plan)
            plan_name = plan_info.get("name", str(args.testrail_plan))
            run_objs  = client.get_runs_for_plan(args.testrail_plan)
            runs = [(r["id"], r.get("name", str(r["id"]))) for r in run_objs]
            print(f"  {TICK}  Plan #{args.testrail_plan}  \"{plan_name}\"  → {len(runs)} run(s)")

        else:  # milestone
            ms_info  = client.get_milestone(args.testrail_milestone)
            ms_name  = ms_info.get("name", str(args.testrail_milestone))
            run_objs = client.get_runs_for_milestone(
                args.testrail_project, args.testrail_milestone
            )
            runs = [(r["id"], r.get("name", str(r["id"]))) for r in run_objs]
            print(f"  {TICK}  Milestone #{args.testrail_milestone}  \"{ms_name}\"  "
                  f"(project #{args.testrail_project}) → {len(runs)} run(s)")

    except Exception as exc:
        print(red(f"\n  {CROSS} TestRail 查询失败: {exc}"), file=sys.stderr)
        return 2

    if not runs:
        print(f"  {WARN}  未找到任何 run，退出。")
        return 0

    # ── Step 5: 写入确认 ──────────────────────────────────────────────────────
    if not args.dry_run:
        print()
        prompt = f"  即将向 {bold(str(len(runs)))} 个 run 写入 Passed 结果，确认继续？[y/N] "
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            print(yellow("  已取消。"))
            return 0

    # ── Step 6: 写入结果 ──────────────────────────────────────────────────────
    print()
    print(bold("-" * 64))
    print(bold("  Step 5 · 写入进度"))
    print(bold("-" * 64))

    summaries: list[RunSummary] = []
    for run_id, run_name in runs:
        s = post_run(
            client, run_id, run_name, info,
            dry_run=args.dry_run,
            verbose=args.verbose,
            filter_test_ids=filter_test_ids,
            only_untested=args.only_untested,
        )
        summaries.append(s)

    # ── Final summary ─────────────────────────────────────────────────────────
    total_cases  = sum(s.total   for s in summaries)
    total_posted = sum(s.posted  for s in summaries)
    total_skip   = sum(s.skipped for s in summaries)
    all_errors   = [e for s in summaries for e in s.errors]

    print()
    print(bold("-" * 64))
    print(bold("  汇总"))
    print(bold("-" * 64))
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"  设备 ESN      :  {info.serial_number or '(未获取)'}")
    print(f"  固件版本      :  {info.build_number  or '(未获取)'}")
    print(f"  Runs 数量     :  {bold(str(len(summaries)))}")
    print(f"  Cases 总数    :  {bold(str(total_cases))}")
    print(f"  {prefix}写入成功  :  {green(bold(str(total_posted)))}")
    if total_skip:
        print(f"  写入失败      :  {red(bold(str(total_skip)))}")

    if all_errors:
        print(f"\n  {CROSS}  {red(bold(str(len(all_errors)) + ' 个错误:'))}")
        for i, e in enumerate(all_errors, 1):
            print(f"    {red(str(i) + '.')} {e}")
        print()
        return 1

    print()
    if args.dry_run:
        print(f"  {WARN}  {yellow('预演完成，未实际写入任何数据。')}")
    else:
        print(f"  {TICK}  {green(bold('全部写入完成。'))}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
