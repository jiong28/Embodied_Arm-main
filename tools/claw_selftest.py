#!/usr/bin/env python3
"""
夹爪方向自检脚本
================
用途：确认夹爪「张开 / 闭合」方向是否正确，一条配置即可纠正。

运行（先关闭 GUI，避免串口被占用）：
    python tools/claw_selftest.py
    python tools/claw_selftest.py COM6

操作顺序建议：
    1) 输入 2 → 观察夹爪：应当是「张开」
    2) 输入 1 → 观察夹爪：应当是「闭合」
    若反了，输入 9 切换 servo_invert 并重试。

角度语义（全项目统一）：用户角度 0°=完全闭合，90°=完全张开
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from gui.core.claw_controller import ClawController, _CONFIG_PATH  # noqa: E402


def _save_invert(value: bool):
    import json
    data = {}
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    data["servo_invert"] = bool(value)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def main():
    port = sys.argv[1].strip() if len(sys.argv) > 1 else "COM6"

    claw = ClawController(port=port)
    print("=" * 56)
    print("夹爪方向自检")
    print("=" * 56)
    print(f"串口     : {port}")
    print(f"配置文件 : {_CONFIG_PATH}")
    print(f"当前方向 : {claw.direction_desc()}")

    if not claw.connect():
        print(f"\n[失败] 无法打开 {port}，请检查：串口是否被 GUI 占用 / ESP32 是否上电")
        return 1
    print("已连接。\n")

    try:
        while True:
            print("-" * 56)
            print("  1. 用户角度: 闭合 (0°)      2. 用户角度: 张开 (90°)")
            print("  3. 用户角度: 自定义          4. 原始舵机角度 (0~180)")
            print("  8. 打印当前配置              9. 切换方向 servo_invert 并保存")
            print("  0. 退出")
            choice = input("请选择: ").strip()

            if choice == "0":
                break

            elif choice in ("1", "2"):
                angle = 0.0 if choice == "1" else 90.0
                label = "闭合" if choice == "1" else "张开"
                if claw.set_angle(angle):
                    print(f"  已下发 用户{angle:.0f}°（{label}）→ 舵机 {claw.last_servo_angle}°")
                    print(f"  >>> 现在夹爪应当是【{label}】状态，请目视确认")
                else:
                    print("  下发失败")

            elif choice == "3":
                raw = input("  输入用户角度 0~90（0=闭合, 90=张开）: ").strip()
                try:
                    angle = float(raw)
                except ValueError:
                    print("  无效输入")
                    continue
                if claw.set_angle(angle):
                    print(f"  已下发 用户{angle:.0f}° → 舵机 {claw.last_servo_angle}°")
                else:
                    print("  下发失败")

            elif choice == "4":
                raw = input("  输入原始舵机角度 0~180: ").strip()
                try:
                    servo = float(raw)
                except ValueError:
                    print("  无效输入")
                    continue
                if claw.raw_set_servo(servo):
                    print(f"  已直接下发 舵机 {claw.last_servo_angle}°")
                else:
                    print("  下发失败")

            elif choice == "8":
                claw.reload_config()
                print(f"  servo_invert = {claw.servo_invert}｜{claw.direction_desc()}")

            elif choice == "9":
                new_value = not claw.servo_invert
                _save_invert(new_value)
                claw.servo_invert = new_value
                print(f"  已切换并保存: servo_invert = {new_value}")
                print(f"  新方向: {claw.direction_desc()}")
                print("  请重新用 1 / 2 验证是否已正确")

            else:
                print("  无效选择")

    finally:
        claw.disconnect()
        print("\n串口已关闭。")

    print("提示：方向最终以“用户90°=张开、用户0°=闭合”为准；改 config/claw_config.json 的 servo_invert 即可长期生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
