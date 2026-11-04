#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开发用热重启启动器（只用于开发，不影响正式启动方式）

作用：
    监听项目里的代码/配置文件，一旦保存（Ctrl+S）就自动重启上位机，
    不需要手动关窗口、再敲一遍 python horizon_gui.py。

用法：
    cd G:\\WL\\Embodied_Arm\\Project\\Embodied_Arm-main
    D:\\miniconda3\\envs\\embodied_arm\\python.exe gui/dev_launcher.py

    # 也可以换个启动目标（比如调试某个脚本）
    python gui/dev_launcher.py --target example/mujoco_control.py

    # 窗口被关掉/程序崩了之后，启动器继续待命（改了文件自动再来一次）
    python gui/dev_launcher.py --keep

监控范围：
    gui/            .py  （界面与业务逻辑）
    Embodied_SDK/   .py  （SDK 封装层）
    config/         .json（零点、标定、夹爪等参数）
    项目根目录       .py  （horizon_gui.py 等入口）

设计说明：
    - 不依赖 watchdog，用轮询 mtime，零安装
    - 重启前优先发 WM_CLOSE（taskkill 不带 /F）让程序走正常清理流程，
      尽量释放 CAN / 串口句柄；5 秒内没退才强杀
    - 连续保存多次只重启一次（0.4 秒去抖）
"""

import argparse
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- 配置

ROOT = Path(__file__).resolve().parent.parent

# (目录, 是否只扫顶层) —— 顶层只扫 .py，避免把 .git / Horizon_Core 全翻一遍
WATCH_DIRS = [
    ("gui", False),
    ("Embodied_SDK", False),
    ("config", False),
    ("", True),           # 项目根目录，只看顶层 *.py
]

WATCH_EXT = {".py", ".json"}
IGNORE_PARTS = {"__pycache__", "logs", ".git", ".vscode", ".idea", "node_modules"}
IGNORE_NAMES = {"dev_launcher.py"}   # 改启动器本身不触发重启（避免自己杀自己）

POLL_INTERVAL = 0.6      # 轮询间隔（秒）
SETTLE_TIME = 0.4        # 检测到变化后再等一会儿，攒齐一次保存的多个文件
CLOSE_TIMEOUT = 5.0      # 优雅关闭等待上限（秒）

IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------- 工具

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def iter_watched_files():
    """遍历所有需要监控的文件。"""
    seen = set()
    for sub, shallow in WATCH_DIRS:
        base = ROOT / sub if sub else ROOT
        if not base.is_dir():
            continue
        it = base.glob("*") if shallow else base.rglob("*")
        for p in it:
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            if p.suffix.lower() not in WATCH_EXT:
                continue
            if p.name in IGNORE_NAMES:
                continue
            if IGNORE_PARTS & set(p.parts):
                continue
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            yield p


def snapshot():
    """返回 {文件路径: (mtime, size)} 快照。"""
    snap = {}
    for p in iter_watched_files():
        try:
            st = p.stat()
        except OSError:
            continue
        snap[str(p)] = (st.st_mtime, st.st_size)
    return snap


def diff(old, new):
    """返回 (新增, 修改, 删除) 三个路径列表。"""
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in new if k in old and new[k] != old[k]]
    return added, changed, removed


def short(path):
    """把绝对路径缩成相对项目的短路径，日志更好读。"""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return path


def has_visible_window(pid):
    """判断某个进程是否有可见的顶层窗口（用来区分 GUI 和控制台脚本）。"""
    if not IS_WINDOWS:
        return True
    try:
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _cb(hwnd, _lparam):
            wnd_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wnd_pid))
            if wnd_pid.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
                return False        # 找到一个就够了，停止枚举
            return True

        user32.EnumWindows(_cb, 0)
        return bool(found)
    except Exception:
        return True                 # 探测失败就按有窗口处理，走完整等待


# ---------------------------------------------------------------- 子进程管理

class Runner:
    """负责启动/关闭上位机子进程。"""

    def __init__(self, target: str, extra_args):
        self.target = target
        self.extra_args = extra_args
        self.proc = None
        self.generation = 0

    def start(self):
        self.generation += 1
        if self.generation > 1:
            log(f"———— 重启第 {self.generation - 1} 次 ————")
        log(f"启动: {self.target}")

        # 用当前解释器启动，保证和激活的 conda 环境一致
        self.proc = subprocess.Popen(
            [sys.executable, self.target, *self.extra_args],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,   # 避免子进程抢终端输入
        )
        return self.proc

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self, reason="重启"):
        """关闭子进程：先礼后兵。"""
        if not self.is_alive():
            return
        pid = self.proc.pid
        log(f"{reason}：正在关闭进程 {pid} ...")

        # 有窗口的程序（上位机）给它足够时间走完 closeEvent；
        # 没有窗口的（控制台脚本）WM_CLOSE 无人接收，快速强杀即可
        timeout = CLOSE_TIMEOUT if has_visible_window(pid) else 1.0

        if IS_WINDOWS:
            # taskkill 不带 /F 会给 GUI 窗口发 WM_CLOSE，等价于点右上角 ×，
            # 程序能走完 closeEvent 的清理流程（断开关节、关闭 CAN 接口）
            subprocess.run(
                ["taskkill", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            self.proc.terminate()

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                log("已正常退出")
                return
            time.sleep(0.15)

        log(f"{timeout:.1f} 秒未退出，强制结束")
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            self.proc.kill()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


# ---------------------------------------------------------------- 主流程

def main():
    parser = argparse.ArgumentParser(
        description="开发用热重启启动器：改代码保存后自动重启上位机",
    )
    parser.add_argument(
        "--target", default="horizon_gui.py",
        help="启动的脚本（相对项目根目录），默认 horizon_gui.py",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="程序自己退出（关窗口/崩溃）后，启动器继续待命，不退出",
    )
    args, extra = parser.parse_known_args()

    target_path = Path(args.target)
    if not target_path.is_absolute():
        target_path = ROOT / args.target
    if not target_path.is_file():
        log(f"找不到启动目标: {target_path}")
        return 1

    # 给子进程传相对路径，日志更干净
    target = str(target_path.relative_to(ROOT)) if target_path.is_relative_to(ROOT) else str(target_path)

    log("=" * 58)
    log("热重启启动器已就绪")
    log(f"启动目标: {target}")
    log("监控目录: gui/  Embodied_SDK/  config/  根目录*.py")
    log("保存文件即自动重启；按 Ctrl+C 结束")
    log("=" * 58)

    before = snapshot()
    log(f"已监控 {len(before)} 个文件，正在启动程序 ...")

    runner = Runner(target, extra)
    runner.start()

    exit_code = 0
    try:
        while True:
            time.sleep(POLL_INTERVAL)

            # ① 程序自己退出了（关窗口 / 崩溃）
            if not runner.is_alive():
                exit_code = runner.proc.returncode
                if args.keep:
                    log(f"程序已退出（退出码 {exit_code}），启动器继续待命，保存文件即可重新启动")
                    # 等文件变化再拉起
                    before = snapshot()
                    while True:
                        time.sleep(POLL_INTERVAL)
                        now = snapshot()
                        added, changed, removed = diff(before, now)
                        if added or changed or removed:
                            for p in (added + changed + removed)[:5]:
                                log(f"  检测到变化: {short(p)}")
                            time.sleep(SETTLE_TIME)
                            before = snapshot()
                            runner.start()
                            break
                    continue
                else:
                    log(f"程序已退出（退出码 {exit_code}），启动器随之结束")
                    break

            # ② 检测文件变化
            now = snapshot()
            added, changed, removed = diff(before, now)
            if not (added or changed or removed):
                continue

            for p in (added + changed)[:6]:
                log(f"  检测到变化: {short(p)}")
            for p in removed[:3]:
                log(f"  文件被删除: {short(p)}")

            time.sleep(SETTLE_TIME)      # 去抖：连续保存多个文件只重启一次
            before = snapshot()

            runner.stop(reason="代码有改动")
            runner.start()

    except KeyboardInterrupt:
        log("")
        log("收到 Ctrl+C，准备退出 ...")
        runner.stop(reason="退出")
        log("已结束。")
        return 0

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
