#!/usr/bin/env python3
"""
夹爪舵机测试脚本
================
在你的 Arm conda 环境中运行：
    python example/test_claw_servo.py

功能：
1. 尝试通过 SDK 创建并绑定夹爪控制器
2. 如果 SDK 方式失败，使用 pyserial 直接通信测试
"""
import os
import sys
import time
import serial

# 设置项目路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
os.environ.setdefault("HORIZONARM_CONFIG_DIR", os.path.join(project_root, "config"))

# ============================================================
# 第一步：尝试 SDK 方式
# ============================================================
print("=" * 50)
print("夹爪舵机测试")
print("=" * 50)

CLAW_COM = input("请输入舵机 ESP32 的串口号 (默认 COM6): ").strip() or "COM6"

# 尝试方式1: 通过 gateway 获取 control_claw 模块，查找控制器类
print("\n[方式1] 尝试通过 SDK gateway 创建夹爪控制器...")
try:
    from Horizon_Core import gateway

    # 检查 gateway 是否有创建夹爪控制器的函数
    gateway_attrs = [a for a in dir(gateway) if 'claw' in a.lower() or 'gripper' in a.lower()]
    print(f"  gateway 中与 claw/gripper 相关的属性: {gateway_attrs}")

    # 检查 embodied_func 的 claw 相关函数
    embodied_func = gateway.get_embodied_module()
    ef_attrs = [a for a in dir(embodied_func) if 'claw' in a.lower() or 'gripper' in a.lower() or a == 'c_c_g']
    print(f"  embodied_func 中与 claw 相关的属性: {ef_attrs}")

    # 检查 embodied_internal
    try:
        embodied_internal = gateway.get_embodied_internal_module()
        ei_attrs = [a for a in dir(embodied_internal) if 'claw' in a.lower() or 'gripper' in a.lower()]
        print(f"  embodied_internal 中与 claw 相关的属性: {ei_attrs}")
    except Exception as e:
        print(f"  embodied_internal 获取失败: {e}")

    # 尝试直接导入 control_claw 模块
    print("\n  尝试导入 Horizon_Core.core.control_claw...")
    try:
        from Horizon_Core.core import control_claw
        cc_attrs = dir(control_claw)
        print(f"  control_claw 模块属性: {cc_attrs}")

        # 查找可能的控制器类
        for name in cc_attrs:
            obj = getattr(control_claw, name)
            if callable(obj) and not name.startswith('_'):
                print(f"    {name}: {type(obj).__name__}")
                if type(obj).__name__ == 'type':
                    print(f"      -> 这是一个类！尝试检查构造函数...")
                    try:
                        import inspect
                        sig = inspect.signature(obj.__init__)
                        print(f"      -> __init__ 签名: {sig}")
                    except:
                        pass
    except ImportError as e:
        print(f"  导入 control_claw 失败: {e}")

except Exception as e:
    print(f"  SDK gateway 方式失败: {e}")

# 尝试方式2: 查找 _set_claw_controller 的签名信息
print("\n[方式2] 检查 _set_claw_controller 预期的控制器类型...")
try:
    from Horizon_Core import gateway
    embodied_func = gateway.get_embodied_module()
    if hasattr(embodied_func, '_set_claw_controller'):
        print("  embodied_func._set_claw_controller 存在")
        try:
            import inspect
            # 尝试获取签名（编译函数可能不支持）
            sig = inspect.signature(embodied_func._set_claw_controller)
            print(f"  签名: {sig}")
        except:
            print("  无法获取签名（编译函数）")
except Exception as e:
    print(f"  检查失败: {e}")

# ============================================================
# 第二步: 直接串口通信测试（绕过 SDK）
# ============================================================
print("\n" + "=" * 50)
print("[方式3] 直接串口通信测试（绕过 SDK）")
print("=" * 50)
print(f"尝试连接 {CLAW_COM} (9600 baud)...")

try:
    ser = serial.Serial(CLAW_COM, 9600, timeout=1)
    time.sleep(2)  # 等待 ESP32 重启
    print(f"已连接 {CLAW_COM}")

    while True:
        print("\n  1. 张开夹爪")
        print("  2. 闭合夹爪")
        print("  3. 自定义角度 (0-90)")
        print("  0. 退出")

        choice = input("\n请选择: ").strip()

        if choice == '0':
            break
        elif choice == '1':
            angle = input("张开角度 (默认 90): ").strip() or "90"
            a = max(0, min(90, int(angle)))
            ser.write(f"{a}\n".encode())
            print(f"已发送: {a}")
        elif choice == '2':
            ser.write(b"0\n")
            print("已发送: 0")
        elif choice == '3':
            angle = input("输入角度 (0-90): ").strip()
            try:
                a = int(angle)
                if 0 <= a <= 90:
                    ser.write(f"{a}\n".encode())
                    print(f"已发送: {a}")
                else:
                    print("角度必须在 0-90 之间")
            except ValueError:
                print("无效输入")
        else:
            print("无效选择")

    ser.close()
    print("串口已关闭")

except serial.SerialException as e:
    print(f"串口连接失败: {e}")
    print("\n可能的原因:")
    print(f"  1. {CLAW_COM} 端口不存在或被占用")
    print("  2. ESP32 未烧录 claw.ino 固件")
    print("  3. 波特率不匹配 (应为 9600)")
except Exception as e:
    print(f"错误: {e}")
