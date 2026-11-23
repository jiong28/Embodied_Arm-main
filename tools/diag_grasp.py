#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取失败分步诊断脚本
====================

复现 GUI"执行抓取"的完整链路，逐步打印中间结果，定位失败环节：
  ① 末端位姿获取  ② 标定参数加载  ③ 抓取参数  ④ 像素→世界坐标  ⑤ c_a_p 执行

用法（在 Embodied_Arm-main 目录下，**运行前必须先关闭 GUI 释放 COM 口**）：
    D:/miniconda3/envs/embodied_arm/python.exe tools/diag_grasp.py 352 192 COM3
    参数: 像素u 像素v [串口号，默认 COM3]

⚠️ 脚本会真实运动机械臂，请确认工作区无障碍、急停可用。

重要：像素→世界换算依赖机械臂当前姿态。想复现 GUI 的抓取条件，请：
    ① 在 GUI 里先回到"拍照位置" ② 关闭 GUI（机械臂保持原姿态）③ 立即运行本脚本
"""

import os
import sys
import json

# 保证从项目根目录导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "  ✅"
FAIL = "  ❌"


def connect_motors(port: str, motor_ids=(1, 2, 3, 4, 5, 6)):
    """连接电机并注册到 embodied_internal（等价于 GUI 的 bind_motors 流程）"""
    from Embodied_SDK.motion import create_motor_controller
    from Horizon_Core import gateway as horizon_gateway

    motors = {}
    for mid in motor_ids:
        m = create_motor_controller(
            motor_id=mid, interface_type="slcan",
            shared_interface=True, port=port, baudrate=500000,
        )
        m.connect()
        motors[mid] = m
        print(f"  电机 {mid} 已连接")

    # 减速比/方向：与 GUI 保持一致，从 motor_config.json 读取
    ratios, dirs = {}, {}
    cfg_path = os.path.join("config", "motor_config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        ratios = {int(k): float(v) for k, v in cfg.get("motor_reducer_ratios", {}).items()}
        dirs = {int(k): int(v) for k, v in cfg.get("motor_directions", {}).items()}
    for mid in motor_ids:
        ratios.setdefault(mid, 16.0)
        dirs.setdefault(mid, 1)

    embodied_internal = horizon_gateway.get_embodied_internal_module()
    embodied_internal._set_real_motors(motors, ratios, dirs)
    print(f"  已注册到 embodied_internal (减速比={ratios})")
    return motors


def step(title):
    print(f"\n{'=' * 60}\n▶ {title}\n{'=' * 60}")


def main():
    u = float(sys.argv[1]) if len(sys.argv) > 2 else 352.0
    v = float(sys.argv[2]) if len(sys.argv) > 2 else 192.0
    port = sys.argv[3] if len(sys.argv) > 3 else "COM3"
    print(f"诊断目标像素: (u={u}, v={v})  串口: {port}")

    # ---------- ⓪ 连接电机（必须先关闭 GUI 释放 COM 口）----------
    step("⓪ 连接电机并注册到 embodied_internal")
    try:
        connect_motors(port)
    except Exception as e:
        print(FAIL + f" 电机连接失败: {e}")
        print("   → 检查: GUI 是否已关闭(串口独占)、COM 口号、CAN 线与供电")
        return
    print(PASS + " 6 轴全部连接成功")

    # ---------- ① 末端位姿 ----------
    step("① 获取机械臂当前末端位姿")
    from Horizon_Core import gateway as horizon_gateway
    embodied_internal = horizon_gateway.get_embodied_internal_module()

    current_pose = embodied_internal._get_current_arm_pose()
    print(f"current_pose = {current_pose}")
    if current_pose is None:
        print(FAIL + " 失败原因: 机械臂未连接或无法获取位姿")
        print("   → 检查: 电机是否已连接(先在'机械臂控制'页连接)、CAN 线、COM 口")
        return
    print(PASS + " 位姿获取正常")

    # ---------- ② 标定参数 ----------
    step("② 加载标定参数 calibration_parameter.json")
    calib = None
    if hasattr(embodied_internal, "_load_calibration_params"):
        calib = embodied_internal._load_calibration_params()
    if not calib:
        import json
        for cand in [
            os.path.join(os.environ.get("HORIZONARM_CONFIG_DIR", ""), "calibration_parameter.json"),
            os.path.join("config", "calibration_parameter.json"),
        ]:
            if cand and os.path.exists(cand):
                with open(cand, "r", encoding="utf-8") as f:
                    calib = json.load(f)
                print(f"  (备用加载: {cand})")
                break
    if not calib:
        print(FAIL + " 失败原因: 找不到标定文件")
        return
    print(f"{PASS} 标定键: {list(calib.keys())}")

    # ---------- ③ 抓取参数 ----------
    step("③ 读取抓取参数 (姿态/TCP偏移/深度)")
    grasp = embodied_internal._get_grasp_params()
    print(f"grasp_params = {grasp}")
    tcp_x = grasp.get("tcp_offset_x", 0.0)
    tcp_y = grasp.get("tcp_offset_y", 0.0)
    tcp_z = grasp.get("tcp_offset_z", 0.0)
    print(PASS + f" TCP偏移=({tcp_x}, {tcp_y}, {tcp_z})")

    # ---------- ④ 像素→世界 ----------
    step("④ 像素坐标 → 基坐标世界坐标")
    world = embodied_internal._convert_pixel_to_world_coords(
        u, v, calib, current_pose,
        tcp_x=tcp_x, tcp_y=tcp_y, tcp_z=tcp_z,
    )
    print(f"world = {world}")
    if world is None:
        print(FAIL + " 失败原因: 像素坐标转换失败（检查标定参数数值/畸变系数）")
        return
    x_w, y_w, z_w = world
    print(f"{PASS} 目标世界坐标 = ({x_w:.1f}, {y_w:.1f}, {z_w:.1f}) mm")

    # 合理性检查
    r = (x_w ** 2 + y_w ** 2) ** 0.5
    print(f"  目标到基座水平距离 = {r:.1f} mm", end="  ")
    if r > 320:
        print("⚠️ 超出常见工作半径，IK 可能失败")
    elif r < 80:
        print("⚠️ 离基座太近，IK 可能失败")
    else:
        print("(在常见工作半径内)")
    if z_w < 10:
        print("  ⚠️ z 过低，注意是否会把夹爪压到桌面")

    # ---------- ⑤ IK 可达性预检查 ----------
    step("⑤ IK 可达性预检查（关节限位）")
    try:
        from Embodied_SDK.visual_grasp import _get_kinematics, _check_ik
        kin = _get_kinematics()
        reach_ok, reach_msg, sol = _check_ik(
            kin, x_w, y_w, z_w,
            grasp.get("yaw", 0.0), grasp.get("pitch", 0.0), grasp.get("roll", 180.0))
        if reach_ok:
            print(f"{PASS} 存在可行解: {None if sol is None else sol.round(1).tolist()}")
        else:
            print(f"{FAIL} 不可达: {reach_msg}")
            print("   → 这就是抓取失败根因：物体离基座过近或过低。需放远至半径≥210mm；")
            print("     若同一物体始终被算到近处，请重做手眼标定。")
    except Exception as e:
        print(f"  (IK 预检查跳过: {e})")

    # ---------- ⑥ c_a_p 执行 ----------
    step("⑥ 调用 c_a_p 执行末端运动")
    grasp2 = embodied_internal._get_grasp_params()
    yaw = grasp2.get("yaw", 0.0)
    pitch = grasp2.get("pitch", 0.0)
    roll = grasp2.get("roll", 180.0)
    pos = [float(x_w), float(y_w), float(z_w)]
    ori = [float(yaw), float(pitch), float(roll)]
    print(f"Pos = {pos}\nOri = {ori}")

    embodied_internal._set_motion_params(max_speed=300, acceleration=300, deceleration=300)
    embodied_func = horizon_gateway.get_embodied_module()
    ok = embodied_func.c_a_p(pos, ori)
    print(f"c_a_p 返回 = {ok}")
    if ok:
        print(PASS + " 抓取运动已执行，请目视确认落点是否对准目标")
        print("   → 若落点系统性偏移: 标定/零点问题; 若到位但没抓到: TCP 偏移或夹爪问题")
    else:
        print(FAIL + " 失败原因: c_a_p 执行失败")
        print("   → 最常见: 目标不可达(IK 失败)。可尝试: 微调像素点位置(±20px)、")
        print("     检查 all_parameter_config.json 的 euler_angles/tcp_offset 是否被改动过")


class _Tee:
    """同时输出到终端和文件，方便留存完整诊断记录"""
    def __init__(self, path):
        self.file = open(path, "w", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, s):
        self.stdout.write(s)
        self.file.write(s)

    def flush(self):
        self.stdout.flush()
        self.file.flush()


if __name__ == "__main__":
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_last_run.txt")
    sys.stdout = _Tee(log_path)
    try:
        main()
    finally:
        sys.stdout = sys.__stdout__
        print(f"\n完整输出已保存: {log_path}")
