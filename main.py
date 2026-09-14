#!/usr/bin/env python3
"""潍坊学院校园网断线自动重连脚本。

用法::

    uv run main.py run        # 守护模式：断线即自动登录（默认）
    uv run main.py status     # 只检测当前是否联网
    uv run main.py login      # 立刻登录一次
    uv run main.py login --dry-run   # 只打印将要发送的密文，不提交

账号密码按 CLI 参数 -> 环境变量 -> config.json 的顺序读取，优先级从高到低：
    --username/--password
    WFU_USERNAME/WFU_PASSWORD
    config.json  {"username": "...", "password": "...", " ": 15}
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

from portal import DEFAULT_BASE_URL, PortalClient, PortalError

CONFIG_FILE = Path(__file__).with_name("config.json")
DEFAULT_INTERVAL = 15.0

log = logging.getLogger("wfu")


def load_config(args: argparse.Namespace) -> dict:
    """合并 config.json / 环境变量 / 命令行参数。"""
    cfg: dict = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("读取 %s 失败：%s", CONFIG_FILE.name, exc)
    cfg["username"] = (
        args.username or os.getenv("WFU_USERNAME") or cfg.get("username", "")
    )
    cfg["password"] = (
        args.password or os.getenv("WFU_PASSWORD") or cfg.get("password", "")
    )
    cfg["base_url"] = args.base_url or os.getenv("WFU_BASE_URL") or cfg.get(
        "base_url", DEFAULT_BASE_URL
    )
    cfg["interval"] = float(
        args.interval or os.getenv("WFU_INTERVAL") or cfg.get("interval", DEFAULT_INTERVAL)
    )
    cfg["timeout"] = float(args.timeout)
    cfg["max_attempts"] = int(args.max_attempts)
    return cfg


def setup_logging(verbose: bool, log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


class Watchdog:
    """断线检测 + 自动重连的守护循环。"""

    def __init__(
        self,
        client: PortalClient,
        interval: float = DEFAULT_INTERVAL,
        max_attempts: int = 5,
    ) -> None:
        self.client = client
        self.interval = interval
        self.max_attempts = max_attempts
        self.stopping = False

    def stop(self, *_args) -> None:
        self.stopping = True

    def _sleep(self, seconds: float) -> None:
        """可被 Ctrl-C 打断的休眠。"""
        deadline = time.monotonic() + seconds
        while not self.stopping and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))

    def reconnect(self) -> bool:
        """断线后重连：最多尝试 max_attempts 次，成功返回 True。"""
        log.warning("检测到断网，开始自动登录…")
        for attempt in range(1, self.max_attempts + 1):
            if self.stopping:
                return False
            try:
                result = self.client.login()
            except (requests.RequestException, PortalError) as exc:
                log.error("第 %d/%d 次登录异常：%s", attempt, self.max_attempts, exc)
            else:
                if result.ok:
                    for _ in range(8):          # 认证后网关通常瞬时生效
                        self._sleep(1)
                        if self.client.is_online():
                            log.info("重连成功，网络已恢复")
                            return True
                    log.warning("认证返回成功，但网络探测仍失败，继续重试")
                else:
                    log.error(
                        "第 %d/%d 次登录失败：%s", attempt, self.max_attempts, result
                    )
                    if result.reason in ("30", "32", "41", "43"):
                        # 账号侧的问题，重试也没用，交给人工处理
                        log.error("账号状态异常，暂停自动重连，请人工处理")
                        return False
            self._sleep(min(5 * attempt, 30))
        log.error("连续 %d 次登录失败，%ds 后重新开始", self.max_attempts, self.interval)
        return False

    def run(self) -> None:
        log.info(
            "守护启动：账号=%s 门户=%s 检测间隔=%.0fs（Ctrl-C 退出）",
            self.client.username,
            self.client.base_url,
            self.interval,
        )
        while not self.stopping:
            try:
                if self.client.is_online():
                    log.debug("网络正常")
                else:
                    self.reconnect()
            except requests.RequestException as exc:
                log.error("网络探测异常：%s", exc)
            self._sleep(self.interval)
        log.info("已退出守护模式")

    def status(self) -> bool:
        online = self.client.is_online()
        log.info("当前网络状态：%s", "已连接" if online else "未连接")
        return online


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="潍坊学院校园网断线自动重连",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="run",
        choices=("run", "status", "login"),
        help="run=守护重连, status=查询状态, login=登录一次",
    )
    parser.add_argument("-u", "--username", help="学工号/手机号")
    parser.add_argument("-p", "--password", help="密码（也可用 WFU_PASSWORD）")
    parser.add_argument("--base-url", help="门户地址")
    parser.add_argument("--interval", type=float, help="断线检测间隔（秒）")
    parser.add_argument("--timeout", type=float, default=10.0, help="请求超时（秒）")
    parser.add_argument(
        "--max-attempts", type=int, default=5, help="一轮重连的最大尝试次数"
    )
    parser.add_argument("--dry-run", action="store_true", help="只加密不提交")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--log-file", help="同时把日志写入文件")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    setup_logging(args.verbose, args.log_file)
    cfg = load_config(args)

    client = PortalClient(
        cfg["username"],
        cfg["password"],
        base_url=cfg["base_url"],
        timeout=cfg["timeout"],
    )
    watchdog = Watchdog(client, cfg["interval"], cfg["max_attempts"])

    try:
        if args.action == "status":
            return 0 if watchdog.status() else 1

        if not cfg["username"] or not cfg["password"]:
            log.error("缺少账号或密码：请用 --username/--password、环境变量 "
                      "WFU_USERNAME/WFU_PASSWORD，或写入 config.json")
            return 2

        if args.action == "login":
            if args.dry_run:
                items, iv = client.fetch_login_form()
                payload = client.build_payload(items, iv)
                log.info("dry-run：iv=%s 字段数=%d 密文长度=%d",
                         iv, len(items), len(payload["data"]))
                print("data =", payload["data"][:80] + "...")
                print("iv   =", payload["iv"])
                return 0
            result = client.login()
            if result.ok:
                return 0
            log.error("%s", result)
            return 1

        watchdog.run()
        return 0
    except KeyboardInterrupt:
        watchdog.stop()
        log.info("收到中断信号，正在退出…")
        return 130


if __name__ == "__main__":
    sys.exit(main())
