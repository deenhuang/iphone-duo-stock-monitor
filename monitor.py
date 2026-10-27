#!/usr/bin/env python3
"""Apple China pickup monitor. Python standard library only."""

import argparse
import base64
import copy
from datetime import datetime, timezone, timedelta
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
APPLE_URL = "https://www.apple.com.cn/shop/retail/pickup-message"
STATE_BRANCH = "monitor-state"
PRODUCT_URL = "https://www.apple.com.cn/shop/buy-iphone/iphone-duo/mk2p4ch/a"
PRODUCT_NAME = "iPhone Duo 512GB 星光白色"
PART = "MK2P4CH/A"
CN_TIME = timezone(timedelta(hours=8))


class MonitorError(Exception):
    """Safe error message suitable for public workflow logs."""


def request_json(url, *, data=None, headers=None, method=None):
    request_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
    }
    request_headers.update(headers or {})
    encoded = None
    if data is not None:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=encoded, headers=request_headers, method=method)
    host = urllib.parse.urlsplit(url).hostname
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise MonitorError(f"{host} 返回的数据过大")
        result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise MonitorError(f"{host} HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise MonitorError(f"{host} 网络连接失败或超时") from None
    except (ValueError, UnicodeError):
        raise MonitorError(f"{host} 返回了非 JSON 数据") from None
    if not isinstance(result, dict):
        raise MonitorError(f"{host} 返回了无效的数据结构")
    return result


def clean_text(value):
    return html.unescape(re.sub(r"<[^>]*>", "", str(value))).strip()[:500]


def parse_stores(payload, part, target_stores=None):
    try:
        if str(payload["head"]["status"]) != "200":
            raise MonitorError("Apple 返回业务错误")
        stores = payload["body"]["stores"]
        if not isinstance(stores, list) or not stores:
            raise MonitorError("Apple 未返回门店列表")
        result = {}
        for store in stores:
            if target_stores is not None and (
                    not isinstance(store, dict) or store.get("storeNumber") not in target_stores):
                continue
            try:
                item = store["partsAvailability"][part]
                display = item["pickupDisplay"]
                if display not in ("available", "unavailable"):
                    raise MonitorError("Apple 返回未知的商品取货状态")
                store_id = store["storeNumber"]
                if not isinstance(store_id, str) or not re.fullmatch(r"R\d+", store_id):
                    raise MonitorError("Apple 返回无效的门店编号")
                result[store_id] = {
                    "available": display == "available",
                    "city": clean_text(store["city"]),
                    "name": clean_text(store["storeName"]),
                    "quote": clean_text(item.get("pickupSearchQuote", display)),
                }
            except (KeyError, TypeError, AttributeError, MonitorError):
                if target_stores is None:
                    raise
                # collect() reports absent target rows as unknown. Keep valid
                # target rows from this response even if another row is bad.
                continue
        return result
    except (KeyError, TypeError, AttributeError):
        raise MonitorError("Apple 库存响应字段不完整") from None


def collect(config):
    observations = {}
    query_errors = []
    for location in config["locations"]:
        url = APPLE_URL + "?" + urllib.parse.urlencode({"parts.0": config["part"], "location": location})
        try:
            stores = parse_stores(request_json(url), config["part"], config["stores"])
            for store_id, entry in stores.items():
                if store_id not in config["stores"]:
                    continue
                # Nearby search responses overlap. A positive response takes
                # precedence over an unavailable response from the same run.
                if store_id not in observations or entry["available"]:
                    observations[store_id] = entry
        except MonitorError as exc:
            query_errors.append(f"地区 {location}：{exc}")
    missing = sorted(set(config["stores"]) - set(observations))
    errors = []
    if missing:
        errors = ["缺少有效库存结果：" + "、".join(missing)] + query_errors
    return observations, errors


def validate_state(state):
    if not isinstance(state, dict) or not isinstance(state.get("stores", {}), dict):
        raise MonitorError("监测状态文件无效，拒绝重置提醒记录")
    if state.get("part", PART) != PART:
        raise MonitorError("监测状态对应其他商品，拒绝混用提醒记录")
    for entry in state.get("stores", {}).values():
        if not isinstance(entry, dict) or type(entry.get("available")) is not bool:
            raise MonitorError("监测状态的门店记录无效")
    for name in ("failures", "last_error_push"):
        if type(state.get(name, 0)) not in (int, float) or state.get(name, 0) < 0:
            raise MonitorError("监测状态的计数无效")


def advance(state, observations, errors, notify, now):
    validate_state(state)
    updated = copy.deepcopy(state)
    previous = updated.setdefault("stores", {})
    restocked = [entry for key, entry in observations.items()
                 if entry["available"] and not previous.get(key, {}).get("available", False)]
    if restocked:
        text = "\n".join(f"{s['city']} · Apple {s['name']}：{s['quote']}" for s in restocked)
        notify("iPhone Duo 到店取货有货", f"{PRODUCT_NAME}\n{text}\n点击打开购买页面，以结账结果为准。")
    # This update happens only after the stock notification succeeds.
    previous.update(observations)
    if errors:
        updated["failures"] = updated.get("failures", 0) + 1
        last_push = updated.get("last_error_push", 0)
        if updated["failures"] >= 3 and (not last_push or now - last_push >= 21600):
            notify("iPhone 库存监测异常", "连续多轮未能完整查询十家门店，不能据此判断缺货。\n" + "\n".join(errors))
            updated["last_error_push"] = now
    else:
        if updated.get("last_error_push"):
            notify("iPhone 库存监测已恢复", "杭州、上海十家门店查询均已恢复；检测到补货时会另行提醒。")
        updated["failures"] = 0
        updated["last_error_push"] = 0
        updated["last_full_success"] = now
    updated.update({"version": 1, "part": PART, "last_run": now})
    return updated


class Bark:
    def __init__(self, key):
        if not key or not re.fullmatch(r"[A-Za-z0-9_-]{5,200}", key):
            raise MonitorError("请在 GitHub Secrets 配置 BARK_KEY（只填设备密钥，不填整个网址）")
        self.key = key

    def send(self, title, body):
        result = request_json("https://api.day.app/push", data={
            "device_key": self.key, "title": title, "body": body,
            "url": PRODUCT_URL, "group": "iPhone Duo 补货", "sound": "bell",
        })
        if result.get("code") != 200:
            raise MonitorError("Bark 推送失败，请检查 BARK_KEY 与手机通知权限")


class GitHubState:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or "") or not token:
            raise MonitorError("缺少 GitHub 仓库信息或 GITHUB_TOKEN")
        self.url = f"https://api.github.com/repos/{repository}/contents/state.json"
        self.headers = {"Authorization": "Bearer " + token,
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}
        self.sha = None

    def load(self):
        data = request_json(self.url + "?ref=" + STATE_BRANCH, headers=self.headers)
        try:
            state = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
            self.sha = data["sha"]
        except (KeyError, ValueError, UnicodeError, TypeError):
            raise MonitorError("无法读取监测状态；请先初始化 monitor-state 分支") from None
        validate_state(state)
        return state

    def save(self, state):
        validate_state(state)
        if not self.sha:
            raise MonitorError("保存状态之前必须先成功读取状态")
        content = base64.b64encode(json.dumps(state, ensure_ascii=False, indent=2).encode()).decode()
        result = request_json(self.url, method="PUT", headers=self.headers, data={
            "message": "chore: update pickup monitoring state",
            "content": content, "sha": self.sha, "branch": STATE_BRANCH,
        })
        try:
            self.sha = result["content"]["sha"]
        except (KeyError, TypeError):
            raise MonitorError("GitHub 未确认监测状态保存成功") from None


def report(config, observations, errors):
    now = datetime.now(CN_TIME).strftime("%Y-%m-%d %H:%M:%S 北京时间")
    lines = [f"{config['product_name']} · {now}", "", "| 城市 | 门店 | 到店取货 |", "| --- | --- | --- |"]
    for key, store in config["stores"].items():
        found = observations.get(key)
        status = ("可取货" if found["available"] else "不可取货") if found else "查询失败，库存未知"
        lines.append(f"| {store['city']} | {store['name']} | {status} |")
    if errors:
        lines += ["", "查询异常："] + ["- " + e for e in errors]
    output = "\n".join(lines) + "\n"
    print(output)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只查询，不推送、不读写状态")
    mode.add_argument("--test-push", action="store_true", help="发送一条测试通知，不查库存")
    args = parser.parse_args()
    try:
        if args.test_push:
            Bark(os.environ.get("BARK_KEY")).send("iPhone 补货提醒测试", "测试成功。此通知不表示有货；杭州、上海补货时会发送门店信息。")
            print("Bark 测试消息已提交，请在手机确认收到。")
            return 0
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        if args.check:
            observations, errors = collect(config)
            report(config, observations, errors)
            return 1 if errors else 0
        bark = Bark(os.environ.get("BARK_KEY"))
        storage = GitHubState(os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN"))
        state = storage.load()
        observations, errors = collect(config)
        report(config, observations, errors)
        updated = advance(state, observations, errors, bark.send, time.time())
        storage.save(updated)
        return 1 if errors else 0
    except MonitorError as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
