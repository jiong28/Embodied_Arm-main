# HorizonArm 上位机 GUI 功能报告

## 一、项目概述

HorizonArm 是一个 6 自由度机械臂（X1/H1 系列）的 Python SDK 与桌面控制软件。硬件上由 6 个 ZDT 闭环电机（CAN 总线，SLCAN 500k 波特率）、一个 ESP32 夹爪舵机控制器（串口 9600 波特率）、可选摄像头组成。

上位机 GUI 基于 PyQt5 构建，提供电机控制、夹爪控制、摄像头预览、状态监控、日志记录等功能。

---

## 二、项目内置功能与对应代码文件

以下为项目原始代码中已实现的功能，列出每个文件的位置和作用。

### 2.1 入口与主窗口

| 文件路径 | 功能 |
|----------|------|
| `horizon_gui.py` | **程序入口**。创建 QApplication，初始化日志系统，实例化主窗口，启动事件循环 |
| `gui/main_window.py` | **主窗口 (MainWindow)**。负责整体布局：标签页切换（机械臂控制 / 视觉抓取）、菜单栏（文件/帮助）、底部日志面板、空格键紧急停止快捷键、退出确认弹窗、程序崩溃清理 |

### 2.2 核心逻辑层 (`gui/core/`)

| 文件路径 | 功能 |
|----------|------|
| `gui/core/motor_manager.py` | **电机管理器 (MotorManager)**。封装所有电机操作：连接/断开、使能/失能、单关节位置移动、多关节同步运动、紧急停止、角度单位转换（电机端编码器角度 ↔ 关节角度）。内部包含 StatusWorker 子线程，定时轮询所有电机状态 |
| `gui/core/config_loader.py` | **配置加载器**。从 `config/motor_config.json` 读取减速比和方向配置，支持 `HORIZONARM_CONFIG_DIR` 环境变量自定义配置目录 |
| `gui/core/crash_handler.py` | **崩溃清理器**。注册 atexit / SIGINT / SIGTERM / excepthook 四种退出路径的清理回调，确保程序异常退出时电机能安全断开 |
| `gui/core/claw_controller.py` | **夹爪串口控制器 (ClawController)**。通过 pyserial 直接与 ESP32 通信，发送角度指令控制舵机。包含角度反转逻辑（用户视角 0°=闭合 / 90°=张开 ↔ 舵机 90°=闭合 / 0°=张开） |

### 2.3 UI 控件层 (`gui/widgets/`)

| 文件路径 | 功能 |
|----------|------|
| `gui/widgets/log_widget.py` | **日志面板 (LogWidget)**。彩色日志显示（info 蓝色 / warn 黄色 / error 红色 / success 绿色），带时间戳，自动写入 `gui/logs/` 目录下的每日日志文件 |
| `gui/widgets/motor_status_table.py` | **电机状态表 (MotorStatusTable)**。QTableWidget 实时显示每个电机的 ID、使能状态、位置、速度、温度、电压、电流。通过 StatusWorker 子线程每 500ms 刷新一次 |
| `gui/widgets/position_control.py` | **位置控制面板 (PositionControl)**。每个关节一组 +/- 按钮，支持 5 种步长（0.1° / 0.5° / 1° / 2° / 5°），显示当前关节角度，支持一键回零 |
| `gui/widgets/sync_control.py` | **同步控制面板 (SyncControl)**。6 个关节角度输入框 + 速度输入框，支持多关节同步运动、全部回零、紧急停止 |
| `gui/widgets/camera_panel.py` | **摄像头预览面板 (CameraPanel)**。通过 OpenCV 连接 USB 摄像头，QTimer 每 33ms 读取一帧（~30fps），BGR→RGB 转换后通过 QImage/QPixmap 显示到界面 |
| `gui/widgets/claw_control.py` | **夹爪控制面板 (ClawControl)**。串口连接/断开、张开按钮（固定 90°）、闭合按钮（用户可设角度 0-90°）、闭合角度手动输入并持久化到 `config/claw_config.json` |

### 2.4 电机面板 (`gui/`)

| 文件路径 | 功能 |
|----------|------|
| `gui/motor_panel.py` | **电机控制主面板 (MotorPanel)**。整合连接栏（端口选择、关节勾选、连接/断开/紧急停止按钮）和所有子面板（状态表、位置控制、同步控制、夹爪控制），提供全部使能/全部失能批量操作 |

### 2.5 SDK 层 (`Embodied_SDK/`)

| 文件路径 | 功能 |
|----------|------|
| `Embodied_SDK/motion.py` | **运动控制 SDK**。`create_motor_controller()` 创建电机控制器，`MotionSDK` 封装关节运动、笛卡尔运动、预设动作、夹爪控制 |
| `Embodied_SDK/visual_grasp.py` | **视觉抓取 SDK**。`VisualGraspSDK` 实现手眼标定 + 像素坐标到世界坐标转换 + YOLOv8 目标检测；`FollowGraspSDK` 实现跟踪抓取（YOLO + CSRT 模板匹配） |
| `Embodied_SDK/ai.py` | **AI 服务 SDK**。`AISDK` 封装 LLM 对话、语音识别(ASR)、语音合成(TTS)、多模态；`DepthEstimationSDK` 深度估计 |
| `Embodied_SDK/embodied.py` | **具身智能 SDK**。`EmbodiedSDK` 封装层级决策系统，自然语言指令→动作规划 |
| `Embodied_SDK/joycon.py` | **Joy-Con 遥操作 SDK**。Nintendo Joy-Con 手柄控制机械臂，支持关节/笛卡尔两种模式 |
| `Embodied_SDK/io.py` | **IO 控制 SDK**。ESP32 数字输入/输出控制 |
| `Embodied_SDK/digital_twin.py` | **数字孪生 SDK**。MuJoCo 仿真环境控制 |

### 2.6 配置文件 (`config/`)

| 文件路径 | 功能 |
|----------|------|
| `config/motor_config.json` | 6 个电机的减速比（134.5, 134.5, 68.5, 15.6, 8.32, 1.0）和方向（±1） |
| `config/dh_parameters_config.json` | 6-DOF DH 参数、关节偏移量、关节限位 |
| `config/calibration_parameter.json` | 相机内参、立体视觉参数、手眼标定 RT 矩阵 |
| `config/all_parameter_config.json` | 视觉抓取参数、具身智能参数、Joy-Con 控制参数 |
| `config/preset_actions.json` | 预设动作（点头、摇头、初始位置、唤醒） |
| `config/claw_config.json` | 夹爪闭合角度持久化存储 |
| `config/aisdk_config.yaml` | AI 服务 API Key、模型选择配置 |
| `config/new_arm.urdf` | 机器人 URDF 模型描述 |

### 2.7 示例脚本 (`example/`)

| 文件路径 | 功能 |
|----------|------|
| `example/quickstart_guide.py` | 5 分钟快速入门：连接电机、读取状态、使能、简单运动 |
| `example/sdk_quickstart.py` | SDK 完整功能演示：关节/笛卡尔/夹爪/预设动作 |
| `example/test_interactive.py` | 单电机交互测试（43 项菜单） |
| `example/test_multi_motor_sync.py` | 多电机同步测试（27 项菜单） |
| `example/test_claw_servo.py` | 夹爪舵机直连测试（绕过 SDK，pyserial 通信） |
| `example/mujoco_control.py` | MuJoCo 仿真可视化 |

---

## 三、基于用户需求实现的功能

以下为在开发对话中根据用户要求新增或修改的功能。

### 3.1 夹爪舵机控制集成

**需求**：将夹爪舵机（ESP32，COM6，9600 波特率）集成到上位机 GUI，支持张开/闭合操作。

**实现方案**：
- 绕过项目 SDK 中编译的 `c_c_g()` 函数（该函数需要绑定控制器，依赖 `.pyd` 模块），改用 pyserial 直接与 ESP32 串口通信
- ESP32 固件（claw.ino）接收 `角度\n` 格式指令，角度 0-90 对应舵机位置
- 创建 `ClawController` 类封装串口通信，创建 `ClawControl` 控件提供 GUI

### 3.2 舵机角度反转逻辑

**问题**：用户发现点击"张开"时夹爪实际闭合，点击"闭合"时实际张开。原因：舵机物理上 90°=闭合、0°=张开，与用户视角相反。

**解决方案**：在 `ClawController.set_angle()` 中添加反转：
```python
servo_angle = 90 - int(user_angle)
# 用户 0°(闭合) → 舵机 90°(闭合)
# 用户 90°(张开) → 舵机 0°(张开)
```

### 3.3 夹爪闭合角度持久化

**需求**：闭合角度手动输入（非滑块），断电后保留上次设置。

**实现方案**：
- 使用 QLineEdit 替代滑块，输入范围 0-90°
- 闭合角度保存到 `config/claw_config.json`，启动时自动加载
- 默认闭合角度 65°

### 3.4 夹爪逻辑修正

**需求**："张开"按钮始终张开到最大（90°），"闭合"按钮使用用户设定的角度。

**实现**：
- `_on_open()` 固定发送 90°
- `_on_close()` 发送 `self._close_angle`（用户可配置）

### 3.5 摄像头实时预览

**需求**：新增摄像头页面，支持相机连接和实时画面预览。

**实现方案**：
- 使用 `cv2.VideoCapture` 连接 USB 摄像头
- QTimer 每 33ms 触发一次帧读取（~30fps）
- 图像处理流水线：`cv2.cvtColor(BGR→RGB)` → `QImage` → `QPixmap` → 缩放显示
- 支持多摄像头选择（ID 0-3）

### 3.6 标签页导航

**需求**：不要用菜单栏切换页面，改用两个大标签页。

**实现方案**：
- 使用 `QTabWidget` 替代菜单切换
- 标签 1："⚙ 机械臂控制" — 电机控制 + 夹爪
- 标签 2："📷 视觉抓取" — 摄像头预览（后续扩展目标检测、具身智能）
- 标签样式：14px 粗体、蓝色选中态、圆角、hover 高亮
- 删除原"视图"菜单

### 3.7 连接栏归入标签页

**问题**：连接栏（端口、关节、连接/断开）原本在全局，切换到"视觉抓取"标签时仍然显示。

**解决方案**：将连接栏从 `main_window.py` 移入 `motor_panel.py`，仅在"机械臂控制"标签页内可见。

### 3.8 断开确认弹窗

**需求**：夹爪断开和摄像头断开都需要确认弹窗，退出程序时直接清理不弹窗。

**实现方案**：
- `_on_disconnect()` 方法中添加 `QMessageBox.question` 确认对话框
- `clear()` 方法直接调用 `_do_disconnect()`（内部断开），退出时不弹窗

---

## 四、新建与修改文件清单

### 4.1 新建文件

| 文件路径 | 目的与作用 |
|----------|-----------|
| `gui/core/claw_controller.py` | **夹爪串口控制器**。封装 pyserial 与 ESP32 的通信协议，提供 `connect()`、`disconnect()`、`set_angle()`、`open_claw()`、`close_claw()` 方法。角度反转逻辑在此实现。独立于 SDK 层，不依赖编译模块 |
| `gui/widgets/claw_control.py` | **夹爪控制 GUI 控件**。提供串口选择、连接/断开、张开/闭合按钮、闭合角度输入框。闭合角度通过 JSON 文件持久化。断开时有确认弹窗，退出时直接清理 |
| `gui/widgets/camera_panel.py` | **摄像头预览 GUI 控件**。提供摄像头 ID 选择、连接/断开、实时画面预览。使用 OpenCV + QTimer 实现 ~30fps 帧刷新。断开时有确认弹窗，退出时直接清理 |
| `example/test_claw_servo.py` | **夹爪舵机测试脚本**。绕过 SDK，直接用 pyserial 测试 ESP32 串口通信，验证舵机连接和角度控制是否正常 |

### 4.2 修改文件

| 文件路径 | 修改内容 |
|----------|---------|
| `gui/main_window.py` | **重构主窗口**。① 删除原全局连接栏（`_build_connection_bar` 方法及 `port_combo`、`motor_checks`、`btn_connect`、`btn_disconnect`、`btn_emergency` 属性）；② 删除 `_on_connect()`、`_on_disconnect()`、`_on_emergency_stop()` 方法（已移入 MotorPanel）；③ 删除"视图"菜单；④ 改用 `QTabWidget` 双标签页布局；⑤ 空格键快捷键转发到 `motor_panel._on_emergency_stop()`；⑥ 状态栏通过 `motor_panel.status_changed` 信号更新；⑦ 清理无用导入 |
| `gui/motor_panel.py` | **重构电机面板**。① 新增连接栏（端口下拉框、6 个关节复选框、连接/断开按钮、紧急停止按钮）；② 新增 `_on_connect()`、`_on_disconnect()`、`_on_emergency_stop()` 方法；③ 新增 `status_changed(str)` 信号用于更新主窗口状态栏；④ 集成 `ClawControl` 夹爪控制控件；⑤ 新增全部使能/全部失能批量操作按钮 |

---

## 五、文件依赖关系图

```
horizon_gui.py (入口)
 └── gui/main_window.py (主窗口)
      ├── gui/motor_panel.py (电机控制面板)
      │    ├── gui/core/motor_manager.py (电机管理)
      │    │    └── Embodied_SDK/motion.py → Horizon_Core/gateway.pyd
      │    ├── gui/core/config_loader.py (配置加载)
      │    ├── gui/widgets/motor_status_table.py (状态表)
      │    ├── gui/widgets/position_control.py (位置控制)
      │    ├── gui/widgets/sync_control.py (同步控制)
      │    └── gui/widgets/claw_control.py (夹爪控制)
      │         └── gui/core/claw_controller.py (串口通信)
      ├── gui/widgets/camera_panel.py (摄像头预览)
      │    └── OpenCV cv2.VideoCapture
      ├── gui/widgets/log_widget.py (日志面板)
      └── gui/core/crash_handler.py (崩溃清理)
```

---

## 六、快速上手指南

### 6.1 启动程序

```bash
cd D:\Embodied_Arm\Embodied_Arm-main
python horizon_gui.py
```

### 6.2 连接电机

1. 切换到"⚙ 机械臂控制"标签页
2. 在顶部连接栏选择串口（如 COM5）
3. 勾选需要连接的关节（默认全选 1-6）
4. 点击"连接"
5. 连接成功后状态表会实时显示各电机状态

### 6.3 控制机械臂

- **单关节微调**：使用位置控制面板的 +/- 按钮，可选步长 0.1°~5°
- **多关节同步**：在同步控制面板输入各关节角度，点击"同步运动"
- **批量操作**：使用"全部使能"/"全部失能"按钮
- **紧急停止**：按空格键或点击红色"紧急停止"按钮

### 6.4 控制夹爪

1. 在夹爪控制区域选择串口（如 COM6）
2. 点击"连接"
3. 点击"张开 (90°)" — 夹爪完全张开
4. 点击"闭合" — 夹爪闭合到设定角度（默认 65°，可手动修改 0-90°）
5. 闭合角度会自动保存，下次启动自动恢复

### 6.5 摄像头预览

1. 切换到"📷 视觉抓取"标签页
2. 选择摄像头 ID（通常为 0）
3. 点击"连接"
4. 实时画面会显示在预览区域（~30fps）

### 6.6 查看日志

- 底部日志面板实时显示操作日志（蓝/黄/红/绿四色）
- 日志文件自动保存在 `gui/logs/` 目录下，按日期分文件

---

## 七、技术要点

| 项目 | 说明 |
|------|------|
| GUI 框架 | PyQt5 5.15.5 |
| 电机通信 | CAN 总线 (SLCAN 500k 波特率)，通过 Horizon_Core 编译模块 |
| 夹爪通信 | pyserial 串口 9600 波特率，直接与 ESP32 通信 |
| 摄像头 | OpenCV cv2.VideoCapture，QTimer 33ms 刷新 |
| 角度转换 | 关节角度 = 电机编码器角度 × 方向 / 减速比 |
| 状态轮询 | StatusWorker 子线程，500ms 间隔查询所有电机 |
| 配置持久化 | JSON 文件存储（夹爪角度、电机参数等） |
| 崩溃保护 | atexit + SIGINT + SIGTERM + excepthook 四重清理 |
| 日志系统 | 彩色控制台输出 + 按日期分文件的磁盘日志 |

---

## 八、视觉抓取与零点管理改动记录

> 本节记录了在实现"点击抓取"功能过程中发现的问题和所做的修改。
> 供后续开发者（或 AI 接手）了解改动背景，避免重复踩坑。

### 8.1 问题背景

用户在实现点击摄像头画面抓取功能时，发现机械臂关节会扭转到极度异常的位置，需要手动紧急停止。

### 8.2 根本原因分析

#### 原因 1：编译模块 `c_a_p` 的 joint→motor 转换缺少零点偏移

编译模块 `embodied_func.c_a_p()` 内部的转换公式：
```
motor_pos = joint_angle × direction × ratio
```

但 `MotorManager`（GUI 手动控制）的转换公式：
```
motor_pos = joint_angle × direction × ratio + zero_offset
```

`c_a_p` 通过 `_set_real_motors(motors, ratios, dirs)` 只接收减速比和方向，**无法接收零点偏移**。导致两条路径的电机目标位置不一致。

#### 原因 2：配置文件不匹配

用户之前使用的 `custom_zero.json` 包含大幅偏移值（如 motor1=121°, motor3=342°, motor5=-477°），这些值是错误的校准数据。同时 `calibration_parameter.json`（相机内参）和 `all_parameter_config.json`（TCP 偏移）也与实际硬件不匹配。

#### 原因 3：ZDT 驱动器不支持用户自定义零点的运动控制

经测试，ZDT 闭环驱动器的 `set_zero_position(save_to_chip=True)` 只改变 `get_position()` 的显示值，**不改变驱动器内部的运动坐标系**。`trigger_homing(4)`（回到绝对零点）回到的是驱动器出厂的原始零点，不是用户设置的零点。因此零点必须在软件层面管理。

### 8.3 解决方案

采用软件层面的 `custom_zero.json` 管理零点，同时通过 **电机代理包装器 (MotorPositionWrapper)** 确保 `MotorManager` 和编译模块 `c_a_p` 使用同一个零点。

#### 核心机制

```
设置零点：
  用户点击 "设置零点"
  → MotorManager.set_custom_zero()
  → 读取各电机当前编码器值（如 motor5=748.8°）
  → 存入 config/custom_zero.json
  → 立即生效

GUI 手动控制路径：
  关节角度 → MotorManager._joint_deg_to_motor_deg()
  → joint × direction × ratio + zero_offset
  → 电机走到正确位置 ✅

视觉抓取路径 (c_a_p)：
  关节角度 → 编译模块计算 motor_pos = joint × ratio × direction（不含偏移）
  → MotorPositionWrapper 自动加偏移 → motor_pos + zero_offset
  → 电机走到正确位置 ✅

显示关节角度：
  电机编码器值 → MotorManager._motor_deg_to_joint_deg()
  → (encoder - zero_offset) / direction / ratio
  → 显示正确角度 ✅
```

#### 为什么 wrapper 方案之前失败、现在成功

之前失败是因为旧的 `custom_zero.json` 值（121°、342° 等）是**任意存储的错误数据**。现在用户点击"设置零点"时，读取的是**当前实际的电机编码器值**，值是正确的。

### 8.4 修改文件清单

| 文件路径 | 修改内容 |
|----------|---------|
| `Embodied_SDK/_motor_wrapper.py` | **新建**。电机位置代理包装器，补偿编译模块缺少的零点偏移。包含 `_ControlActionsProxy`、`_ReadParamsProxy`、`MotorPositionWrapper`、`wrap_motors_with_zero_offset()` |
| `Embodied_SDK/visual_grasp.py` | ① 导入 wrapper ② `bind_motors()` 新增 `custom_zero` 参数，传入时用 wrapper 包装电机 ③ 修正默认减速比与 `motor_config.json` 一致 |
| `Embodied_SDK/motion.py` | ① 导入 wrapper ② `bind_motors()` 新增 `custom_zero` 参数 ③ 修正默认减速比 |
| `gui/core/motor_manager.py` | ① 恢复 `_custom_zero_motor` 及相关方法（`set_custom_zero`、`_save_custom_zero`、`_load_custom_zero`、`has_custom_zero`）② 恢复转换公式中的 `zero_offset` ③ 新增 `custom_zero` 属性暴露给外部 ④ 简化 `go_to_custom_zero()` |
| `gui/core/vision_manager.py` | `init_sdk()` 中调用 `bind_motors()` 时传入 `custom_zero=self.motor_manager.custom_zero` |
| `gui/widgets/position_control.py` | "设置零点"按钮改为调用 `motor_manager.set_custom_zero()`（读取当前编码器值存入 `custom_zero.json`） |
| `gui/widgets/sync_control.py` | "全部回零"调用 `go_to_custom_zero()` |
| `gui/widgets/camera_panel.py` | "拍照位置"调用 `go_to_custom_zero(extra_offsets={5: 90.0})` |
| `config/custom_zero.json` | 存储各电机的零点偏移值（电机端编码器角度），由"设置零点"按钮写入 |

### 8.5 配置文件说明

| 文件 | 用途 | 谁读取 |
|------|------|--------|
| `config/motor_config.json` | 电机减速比和方向 | MotorManager、MotorManagerGUI |
| `config/custom_zero.json` | 零点偏移（电机端编码器值） | MotorManager（直接用）、Embodied_SDK（通过 wrapper 用） |
| `config/calibration_parameter.json` | 相机内参、手眼标定矩阵 | 编译模块 `embodied_internal` |
| `config/all_parameter_config.json` | TCP 偏移、抓取深度等视觉参数 | 编译模块 `embodied_internal` |
| `config/dh_parameters_config.json` | DH 参数、关节限位 | 编译模块 IK 求解器 |

### 8.6 注意事项

1. **不要用 `set_zero_position()` + `trigger_homing(4)` 来管理零点** — ZDT 驱动器不支持用户自定义零点的运动控制
2. **`custom_zero.json` 的值应该接近 0**（正常校准后）— 如果值很大（如几百度），说明校准方式不对
3. **修改配置文件后需要重新连接电机**才能生效（`_load_custom_zero` 在 `connect_motors` 时调用）
4. **视觉抓取的精度**取决于 `calibration_parameter.json`（相机内参）和 `all_parameter_config.json`（TCP 偏移）的准确性，需要针对每台机械臂单独标定

### 8.7 AI 检测框坐标偏移修复

**问题**：AI 检测返回的 bounding box 与实际物体位置严重不符，框偏移到画面右下角。

**原因**：`_DETECT_PROMPT_TEMPLATE` 告诉 LLM 使用 (0,0)→(1000,1000) 的归一化坐标系，但 `paintEvent` 中的缩放逻辑 `sx = pix_w / det_frame_w` 把 bbox 值当作实际像素坐标处理。LLM 返回的 0-1000 范围值被直接当成 640x480 坐标来画框，导致位置偏移。

**修复**：将 prompt 改为让 LLM 直接返回实际像素坐标（使用图片的真实分辨率 `{w}x{h}`），不再使用 0-1000 归一化。

**修改文件**：`gui/core/vision_manager.py` — `_DETECT_PROMPT_TEMPLATE`

### 8.8 MIMO 多模态 MCP 配置

**背景**：主模型 mimo-v2.5-pro 不支持多模态（识图），需要通过 MCP 临时调用 mimo-v2.5 进行图片识别。

**配置文件**：

- 项目级：`D:\Embodied_Arm\Embodied_Arm-main\.mcp.json`
- 全局：`C:\Users\25069\.claude\.mcp.json`

**关键配置**：

- `command`：`D:\anaconda3\Scripts\mimo-image-recognition-mcp.exe`（pip 安装后生成的 exe，不要用 uvx 会导致超时）
- `MIMO_API_BASE`：`https://token-plan-cn.xiaomimimo.com/v1`（必须填到 /v1）
- `MIMO_MODEL`：`mimo-v2.5`

**CLAUDE.md 中已添加声明**：识图优先用主模型多模态能力，不支持时才走 MCP。

### 8.9 AI 检测功能分析与待办

**现状**：当前 AI 检测（`gui/core/vision_manager.py` 的 `LLMDetectWorker`）是**自定义实现**，不是官方 SDK。

**实现方式**：摄像头帧 → base64 编码 → 调用通用 LLM API（mimo-v2.5）→ 让 LLM 返回 JSON 格式的 bbox。

**存在的问题**：

1. LLM 是语言模型，不是视觉检测模型，无法精确到像素级定位
2. 同一位置多次识别结果不一致（bbox 偏差大）
3. 检测框大小和位置与实际物体不贴合
4. 当前 API（mimo-v2.5）的多模态能力有限

**官方 SDK 有专用检测器**（在编译模块中）：

- `YOLOOnnxDetector` — 基于 YOLOv8n 的目标检测，项目里已有 `config/yolov8n.onnx` 模型文件
- `SingleObjectFollower` — 检测后跟踪
- 导入方式：`from Horizon_Core.core.arm_core.yolo_onnx_detector import YOLOOnnxDetector`

**下一步计划**：

1. 先把 `config/llm_config.json` 的 API 换成千问（Qwen）多模态试试效果
   - API Base: `https://dashscope.aliyuncs.com/compatible-mode/v1`
   - 模型: `qwen-vl-plus`
   - 需要阿里云 DashScope API Key
2. 如果千问效果仍不好，改用官方 SDK 的 `YOLOOnnxDetector` 做检测

**配置文件**：`config/llm_config.json` 当前内容：

```json
{
    "api_base": "https://token-plan-cn.xiaomimimo.com/anthropic",
    "api_key": "tp-ciz7dh76pzdy3ah15aiwdns0n3mdff8phfmq4xab0q98tpfw",
    "model": "mimo-v2.5",
    "max_tokens": 2048,
    "temperature": 0.1
}
```

---

## 九、本次对话完整改动文件清单

> 以下为本次对话中所有新建和修改的文件。

### 9.1 新建文件

| 文件路径 | 目的 |
|----------|------|
| `Embodied_SDK/_motor_wrapper.py` | 电机位置代理包装器，补偿编译模块 c_a_p 缺少的零点偏移 |

### 9.2 修改文件

| 文件路径 | 修改内容 |
|----------|---------|
| `Embodied_SDK/visual_grasp.py` | ① 导入 wrapper ② `bind_motors()` 新增 `custom_zero` 参数 ③ 修正默认减速比与 motor_config.json 一致 |
| `Embodied_SDK/motion.py` | ① 导入 wrapper ② `bind_motors()` 新增 `custom_zero` 参数 ③ 修正默认减速比 |
| `gui/core/motor_manager.py` | ① 恢复零点管理（`_custom_zero_motor`、`set_custom_zero`、`_save/load_custom_zero`、`has_custom_zero`）② 恢复转换公式中的 zero_offset ③ 新增 `custom_zero` 属性 ④ 简化 `go_to_custom_zero()` |
| `gui/core/vision_manager.py` | ① `init_sdk()` 传入 `custom_zero` ② 修正 AI 检测 prompt（0-1000 坐标 → 实际像素坐标） |
| `gui/widgets/position_control.py` | "设置零点"改为调用 `motor_manager.set_custom_zero()` |
| `gui/widgets/sync_control.py` | "全部回零"调用 `go_to_custom_zero()` |
| `gui/widgets/camera_panel.py` | "拍照位置"调用 `go_to_custom_zero(extra_offsets={5: 90.0})` |
| `config/custom_zero.json` | 新建，存储各电机零点偏移值 |
| `config/all_parameter_config.json` | 使用官方参数（tcp_offset, fixed_depth 等） |
| `config/calibration_parameter.json` | 使用官方参数（相机内参） |
| `.mcp.json` | 新建，MIMO 多模态 MCP 配置 |
| `CLAUDE.md` | 新增 Change log requirement 和 Image Recognition 声明 |
| `gui_report.md` | 新增第八、九节（视觉抓取改动记录和完整文件清单） |

### 9.3 关键配置文件当前状态

| 文件 | 当前内容 | 说明 |
|------|---------|------|
| `config/custom_zero.json` | `{1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0, 6: -0.5}` | 由"设置零点"按钮写入 |
| `config/llm_config.json` | api_base=mimo, model=mimo-v2.5 | AI 检测用，待换成千问 |
| `config/motor_config.json` | ratio={1:134.5, 2:134.5, 3:68.5, 4:15.6, 5:8.32, 6:1.0} | 未修改 |
| `.mcp.json` | MIMO MCP 配置（exe 路径） | 已配置 |

---

## 十、AI 检测优化与具身智能 GUI 改动记录

### 10.1 AI 检测 API 切换：mimo → 千问 VL Plus

将 `config/llm_config.json` 的 API 切换为阿里云 DashScope 千问 VL Plus：
- API Base: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- 模型: `qwen-vl-plus`
- 优化检测 prompt，简化 JSON 格式要求

### 10.2 集成 YOLOv8 ONNX 本地检测器

新增独立的 `YOLODetector` 类，使用 onnxruntime 直接加载 `config/yolov8n.onnx`：
- `gui/core/yolo_detector.py`：独立 YOLOv8 ONNX 检测器（letterbox + NMS + COCO 80 类中文名）
- `gui/core/vision_manager.py`：新增 `YOLODetectWorker`，`detect_objects_async(frame, mode)` 支持 `"yolo"` / `"llm"` 两种模式
- `gui/widgets/camera_panel.py`：新增检测模式下拉框（"YOLO 本地" / "AI 多模态"）

### 10.3 AI 检测结果截断修复

移除 `_detect_group.setMaximumHeight(150)` 限制，改用 `QScrollArea` 包裹检测结果列表，多目标时可滚动查看。每个检测项增加颜色标记。

### 10.4 具身智能集成到视觉抓取页面

在 camera_panel 画布下方添加自然语言指令输入栏：
- 🤖 图标 + 输入框 + 执行/停止按钮 + 状态提示
- 懒加载 EmbodiedSDK，首次执行指令时自动初始化
- 删除预设动作功能（用户不需要）
- 标签名改为 "📷 视觉抓取 + 具身智能"

### 10.5 QThread 闪退修复

YOLO 检测时 `QThread: Destroyed while thread is still running` 导致闪退。
修复：所有 worker 的 `finished`/`error` 回调中先 `worker.wait(3000)` 确保线程完全退出再清除引用。`_do_disconnect` 中 `terminate()` 后也加 `wait(3000)`。

### 10.6 修改文件清单

| 文件路径 | 修改内容 |
|----------|---------|
| `config/llm_config.json` | API 切换为千问 VL Plus（DashScope） |
| `gui/core/vision_manager.py` | ① 优化检测 prompt ② 新增 `YOLODetectWorker` ③ `detect_objects_async` 支持 mode 参数 |
| `gui/core/yolo_detector.py` | **新建**。独立 YOLOv8 ONNX 检测器 |
| `gui/widgets/camera_panel.py` | ① 检测模式下拉框 ② 自然语言指令输入栏 ③ 检测结果改用 QScrollArea ④ QThread wait 修复 |
| `gui/main_window.py` | 标签名改为 "📷 视觉抓取 + 具身智能" |

### 10.7 零点校准重要发现

**编译模块的 IK 依赖电机芯片里存储的零点位置（通过官方上位机 `set_zero_position` 写入），不是 `custom_zero.json`。**

校准流程：
1. 先用官方上位机校准芯片零点（写入电机硬件）
2. 把机械臂移到初始位置
3. 用本上位机点"设置零点"（读取编码器值存入 custom_zero.json）
4. wrapper 用 custom_zero 补偿编译模块与 MotorManager 的坐标系差异

---

## 十一、全局空格紧急停止统一

### 11.1 问题

视觉抓取标签页的空格紧急停止不生效，只能手动点击按钮。

### 11.2 原因

`main_window.py` 的全局空格快捷键只调用了 `motor_panel._on_emergency_stop()`，没有处理 `camera_panel` 的情况。`camera_panel` 虽然有自己的空格快捷键，但作用域仅限控件本身，被主窗口的快捷键覆盖。

### 11.3 修复

- `main_window.py`：新增 `_on_global_emergency_stop()` 方法，根据当前活跃标签页调用对应的紧急停止
- `camera_panel.py`：移除重复的空格快捷键（由主窗口统一处理）

### 11.4 修改文件

- `gui/main_window.py` — 空格快捷键改为按标签页分发
- `gui/widgets/camera_panel.py` — 移除重复的空格快捷键

---

## 十二、视觉指令联动

### 12.1 功能

自然语言输入框现在支持视觉问答：当指令包含视觉相关关键词时，自动附带当前摄像头画面，同时调用 LLM 回答并在画面上标出检测框。

### 12.2 触发条件

指令中包含以下任一关键词：画面、摄像头、相机、看到、物体、目标、东西、什么、检测、识别、看看 等。

### 12.3 处理流程

```text
用户输入："画面中都有什么物体？"
  ↓ 识别为视觉指令
截取当前摄像头画面
  ↓
发送给 LLM 多模态（画面 + 指令）
  ↓
LLM 返回：自然语言描述 + JSON 检测结果
  ↓
分开处理：
  ├→ 指令栏显示 LLM 文字回复
  └→ 画面上标出检测框（与 AI 检测按钮效果一致）
```

### 12.4 实现

- `_VisionInstructionWorker`：同时调用 LLM 和解析检测结果
- `_is_vision_instruction()`：关键词匹配判断
- `_execute_vision_instruction()`：视觉指令专用入口
- `_on_vision_instruction_done()`：处理 LLM 回复 + 标框

### 12.5 修改文件

- `gui/widgets/camera_panel.py` — 新增视觉指令联动功能

---

## 十三、语音交互功能

### 13.1 功能概述

新增语音对话功能：点击 🎤 按钮进入连续对话模式，用户说话 → ASR 识别 → 指令处理 → AI 回复 → TTS 语音播报，循环进行。支持语音说"停止"或再次点击按钮结束对话。

### 13.2 交互流程

```text
点击 🎤 → 进入对话模式
  ↓ 循环：
  ASR 监听 → 检测到语音 → 静音检测停止录音
  → DashScope ASR 识别文字
  → 检查停止关键词（停止/结束/退出/关闭/再见）
  → 文字放入输入框 → 执行指令（文本/视觉/抓取）
  → AI 回复通过 TTS 播报（女声：longxiaochun）
  → 继续监听
点击 🎤 或说"停止" → 退出对话模式
```

### 13.3 技术实现

- **ASR**：DashScope `paraformer-realtime-v2` 模型，pyaudio 16kHz 录音，RMS 静音检测
- **TTS**：DashScope `cosyvoice-v1` 模型，voice `longxiaochun`（女声），pyaudio 播放
- **LLM**：复用现有指令管线（文本指令/视觉指令/抓取指令）
- **线程安全**：所有音频操作在 QThread 中执行，信号通信

### 13.4 新建文件

- `config/voice_config.json` — 语音配置（ASR/TTS 模型、静音阈值、停止关键词）
- `gui/core/voice_manager.py` — 语音管理模块（VoiceConfig、ASRWorker、TTSWorker、ConversationSession、VoiceManager）

### 13.5 修改文件

- `gui/widgets/camera_panel.py` — 添加 🎤 语音按钮、语音会话集成、指令回调提供 TTS 回复

---

## 十四、指令处理优化

### 14.1 问题

1. "帮我回到拍照的位置"等指令走 EmbodiedSDK，但该 SDK 的 provider 配置有误（"厂商 alibaba: 不支持的厂商"）
2. 普通对话指令没有 LLM 回答
3. 手动点击执行时 TTS 不工作

### 14.2 解决方案

重写指令路由逻辑，不再依赖 EmbodiedSDK：

- **预设动作**（回零/拍照位置/点头/摇头/起床）→ 直接通过 MotorManager 执行，含关节限位检查
- **视觉指令**（含视觉/动作关键词）→ LLM 多模态 + 检测标框
- **普通对话**（其他所有指令）→ LLM 回答（新 `_ChatWorker`），有画面时附带摄像头帧

### 14.3 预设动作

| 关键词 | 动作 | 关节限位检查 |
|--------|------|-------------|
| 回零/初始位置/归零/零点 | go_to_custom_zero | ✓ |
| 拍照位置/拍照 | go_to_custom_zero + 关节5+90° | ✓ |
| 点头 | 关节5 ±30° | [-90,90] |
| 摇头 | 关节1 ±30° | [-90,90] |
| 起床 | 7步序列 | 全部检查 |

### 14.4 修改文件

- `gui/widgets/camera_panel.py` — 重写指令路由、添加 `_ChatWorker`、`PresetActionWorker`、预设动作执行

---

## 十五、语音交互修复与优化

### 15.1 ASR 修复

DashScope SDK 的 `Recognition.call()` 存在 bytes/str 拼接 bug，改为 WebSocket 流式 ASR + `_safe_str()` 类型安全处理。

### 15.2 按钮无响应修复

`_instruction_worker` 引用未正确清除导致按钮卡死。添加 `_reset_instruction_ui()` 统一清理，`_on_execute_instruction` 中增加 `isFinished()` 安全检查。

### 15.3 TTS 错误容差

`_speak_if_voice` 调用包裹 try/except，防止 TTS 的 `begin_time` 等异常阻断回调链。

### 15.4 修改文件

- `gui/core/voice_manager.py` — ASR WebSocket + `_safe_str()` + 错误处理
- `gui/widgets/camera_panel.py` — 按钮安全清理 + TTS 容差

---

## 十六、顺序归位与速度控制

### 16.1 顺序归位

关节 5 减速比小（8.32），归位速度快，夹爪先到位会刮蹭桌面。

实现：`go_to_custom_zero_sequential()` — 先动其他关节，延迟后动关节 5。

流程：① 关节 1,2,3,4,6 先动 → ② 延迟 3 秒 → ③ 关节 5 跟上

### 16.2 速度控制

工具栏新增速度输入框（QSpinBox，50-800 RPM，默认 500），统一控制所有运动场景。

### 16.3 抓取速度优化

`_load_grasp_params()` 未加载 `motion_speed` 和 `motion_acceleration`，导致抓取用默认值 200。修复后从配置文件读取，默认 500。加速度/减速度与速度同步。

### 16.4 修改文件

- `gui/core/motor_manager.py` — 新增 `go_to_custom_zero_sequential()`、`_wait_until_targets()`
- `gui/core/vision_manager.py` — 加载 motion_speed/motion_acceleration，GraspWorker 加速度同步
- `gui/widgets/camera_panel.py` — 速度输入框 + 顺序归位调用

---

## 十七、抓取界面运动参数对照表

### 17.1 参数来源

| 参数 | 配置文件路径 | 键名 | 说明 |
|------|-------------|------|------|
| 抓取速度 | `all_parameter_config.json` | `vision_grasp.grasp_params.motion_speed` | 编译模块 c_a_p 的最大速度，默认 500 |
| 抓取加速度 | `all_parameter_config.json` | `vision_grasp.grasp_params.motion_acceleration` | 编译模块 c_a_p 的加速度，默认 300 |
| 抓取深度 | `all_parameter_config.json` | `vision_grasp.camera.fixed_depth_value` | 像素坐标转世界坐标时的 Z 值，默认 275.0 |
| TCP 偏移 | `all_parameter_config.json` | `vision_grasp.grasp_params.tcp_offset` | 末端工具偏移 [-35, -5, 0] |
| 欧拉角 | `all_parameter_config.json` | `vision_grasp.grasp_params.euler_angles` | 抓取姿态 [0, 0, 180] |
| 相机内参 | `calibration_parameter.json` | `one.camera_matrix` | fx/fy/cx/cy，用于像素→世界坐标转换 |
| 手眼标定 | `calibration_parameter.json` | `eyeinhand.RT_camera2end` | 相机到末端的变换矩阵 |
| DH 参数 | `dh_parameters_config.json` | `dh_parameters` | 连杆长度/偏移/扭转角，用于 IK |
| 关节限位 | `dh_parameters_config.json` | `joint_limits` | 各关节角度范围 |
| 电机减速比 | `motor_config.json` | `motor_reducer_ratios` | 6 个电机的减速比 |
| 电机方向 | `motor_config.json` | `motor_directions` | 6 个电机的旋转方向 |
| 零点偏移 | `custom_zero.json` | 各电机编码器值 | 由"设置零点"按钮写入 |
| 语音 ASR/TTS | `voice_config.json` | `asr_model`/`tts_model`/`tts_voice` | 语音识别和合成配置 |
| LLM API | `llm_config.json` | `api_base`/`api_key`/`model` | AI 检测和对话的 API 配置 |

### 17.2 各操作的参数使用

| 操作 | 速度来源 | 深度来源 | 坐标转换 | IK 求解 |
|------|---------|---------|---------|---------|
| 点击抓取 | UI 速度框 → `_set_motion_params` | `fixed_depth_value` | `calibration_parameter.json` + TCP 偏移 | 编译模块 c_a_p |
| AI 检测抓取 | 同上 | 同上 | 同上 | 同上 |
| 语音指令抓取 | 同上 | 同上 | 同上 | 同上 |
| 拍照位置 | UI 速度框 → `sync_move_positions` | 不涉及 | 不涉及 | 不涉及（直接电机命令） |
| 预设动作 | UI 速度框 → `sync_move_positions` | 不涉及 | 不涉及 | 不涉及（直接电机命令） |

### 17.3 关键配置建议

- **抓取速度**：UI 输入框默认 500 RPM，可在 50-800 范围调整
- **抓取深度**：`fixed_depth_value` 默认 275mm，决定抓取时的 Z 轴高度
- **TCP 偏移**：`tcp_offset` 影响末端位置计算，需根据实际夹爪长度调整
- **相机内参**：需要针对每台相机单独标定

---

## 十八、唤醒词语音助手

### 18.1 功能

实现类似智能音箱的语音交互：点击按钮进入待命状态，喊"贾维斯"唤醒，进入连续对话，30秒无语音自动回到待命。

### 18.2 状态机

```text
[空闲] --点击🎤--> [待命] --喊"贾维斯"--> [对话中]
  ↑                 ↑                       ↓
  └──再点🎤────────┴──超时/说"停止"────────┘
```

- **idle（空闲）**：语音功能未激活
- **waiting（待命）**：持续监听唤醒词，按钮显示 "⏹ 停止监听"（紫色）
- **active（对话中）**：ASR→LLM→TTS 循环，按钮显示 "⏹ 停止监听"（红色）
- **speaking（播报中）**：TTS 播放 AI 回复

### 18.3 配置参数（voice_config.json）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `wake_word` | "贾维斯" | 唤醒关键词 |
| `idle_timeout` | 30 | 对话中无语音超时秒数，自动回到待命 |
| `stop_keywords` | ["停止","结束","退出","关闭","再见"] | 对话中说出则结束对话 |

### 18.4 技术实现

- **WakeWordWorker(QThread)**：后台持续 WebSocket ASR，检测到唤醒词时发出 `wake_detected` 信号
- **ConversationSession**：对话循环，带不活跃超时检测
- **VoiceManager**：三状态状态机（idle/waiting/active），管理 WakeWordWorker 和 ConversationSession 的生命周期

### 18.5 修改文件

- `config/voice_config.json` — 新增 wake_word、idle_timeout
- `gui/core/voice_manager.py` — 重写为三状态机 + WakeWordWorker
- `gui/widgets/camera_panel.py` — 按钮行为和状态显示适配新状态机

---

## 十九、自动抓取模式与语音夹爪控制

### 19.1 自动抓取模式

工具栏新增"自动抓取"切换按钮，默认关闭。

- **关闭**：抓取到位后不动夹爪，需手动点击闭合
- **开启**：抓取到位 → 自动闭合夹爪 → 1.5秒后自动回到拍照位置（夹爪不松开）

覆盖所有抓取路径：手动点击、AI 检测框、语音指令。

### 19.2 语音夹爪控制

新增语音指令：

| 指令 | 动作 |
|------|------|
| 闭合夹爪 | 以默认角度闭合 |
| 闭合夹爪到X度 | 以指定角度闭合（0-90°） |
| 张开夹爪 / 松开夹爪 | 完全张开（90°） |

### 19.3 修改文件

- `gui/widgets/camera_panel.py` — 自动抓取按钮、`_on_grasp_done` 自动闭合逻辑、语音夹爪指令解析

---

## 二十、旋转检测框（Oriented Bounding Box）

### 20.1 问题

检测框为轴对齐矩形，倾斜物体（如斜放的方块）的抓取姿态不准确。

### 20.2 方案

1. YOLO 检测后，对每个检测框裁剪区域做 `cv2.minAreaRect` 拟合，提取旋转角度
2. 画布上绘制旋转矩形（`QPainter.translate + rotate`）
3. 抓取时将旋转角度作为末端 yaw 偏移传给 `c_a_p`

### 20.3 角度计算

```python
crop = frame[y1:y2, x1:x2]
gray → threshold → contours → max contour
rect = cv2.minAreaRect(largest_contour)
angle = rect[2]  # OpenCV 旋转角度
# 归一化到 [-45, 45] 范围
```

### 20.4 抓取链路

```text
检测 → bbox + angle
  → set_grasp_params(yaw=angle)
  → grasp_at_pixel(cx, cy)
  → c_a_p(pos, [angle, pitch, roll])
```

### 20.5 修改文件

- `gui/core/yolo_detector.py` — 新增 `_compute_rotation_angle()`，检测结果含 `angle` 字段
- `gui/widgets/camera_panel.py` — `paintEvent` 绘制旋转矩形，抓取时传入旋转角度

---

## 二十一、自适应夹爪闭合角度

### 21.1 问题

固定闭合角度无法适应不同大小的物体：大物体被挤压，小物体夹不住。

### 21.2 方案

根据检测框面积自动计算闭合角度：

```text
area = (x2-x1) × (y2-y1)
ratio = clamp(area / ref_area, 0, 1)
angle = min_angle + ratio × (max_angle - min_angle)
```

### 21.3 配置参数（claw_config.json）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `adaptive_enabled` | true | 是否启用自适应 |
| `ref_area` | 30000 | 参考面积（约170×170像素） |
| `min_angle` | 15° | 小物体闭合角度 |
| `max_angle` | 45° | 大物体闭合角度 |

### 21.4 工具栏

- "自适应"复选框：勾选时自动计算角度，取消勾选时用手动输入角度
- 自适应模式下手动角度输入框禁用

### 21.5 修改文件

- `config/claw_config.json` — 新增 adaptive_enabled、ref_area、min_angle、max_angle
- `gui/widgets/camera_panel.py` — 自适应复选框、角度计算方法、抓取流程集成

---

## 二十二、智能抓取放置（Pick & Place）

### 22.1 功能

用户通过自然语言指令，让机械臂自动完成"抓取物体 → 放入容器"的完整流程。

### 22.2 对话示例

```text
用户：我感冒了，帮我把感冒药放到黑色的碗里
AI：好的，我帮你把莲花清瘟颗粒放到黑色的碗里。
    ACTION_PLAN:{"target":"莲花清瘟颗粒","container":"黑色的碗"}
系统执行：
  ① 检测 → 找到莲花清瘟颗粒
  ② 抓取 → 闭合夹爪
  ③ 回拍照位置
  ④ 检测 → 找到黑色的碗
  ⑤ 移动到碗上方 → 松开夹爪
  ⑥ 回拍照位置
```

### 22.3 LLM Prompt 设计

放置类指令（含"放到/放进/装入"等关键词）使用专用 prompt：
- 要求 LLM 返回 `ACTION_PLAN:{"target":"...","container":"..."}`
- target 和 container 必须是画面中实际存在的物体名称
- LLM 负责语义推理（如"感冒药"="莲花清瘟颗粒"）

### 22.4 执行流程

```text
① 抓取目标物体（grasp_at_pixel）
② 闭合夹爪
③ 回拍照位置（安全高度）
④ 移动到容器中心（grasp_at_pixel）
⑤ 松开夹爪
⑥ 回拍照位置
```

### 22.5 容器匹配

`_find_box_by_name` 方法在检测结果中模糊匹配容器名称：
- 精确匹配：名称包含关键词
- 词级匹配：名称中的词有交集

### 22.6 修改文件

- `gui/widgets/camera_panel.py` — 修改 LLM prompt、添加 `_parse_action_sequence`、`_execute_action_sequence`、`_PickPlaceWorker`

---

## 二十三、连续动作序列

### 23.1 功能

支持一条指令执行多个预设动作，如"回到零点位，然后点点头再摇头，最后回到拍照位置"。

### 23.2 解析逻辑

`_parse_action_sequence()` 用分隔符（然后、接着、再、最后、逗号等）拆分指令，逐段匹配预设动作关键词。

### 23.3 执行流程

```text
指令："回到零点位，然后点点头，最后回到拍照位置"
  → 解析: ["go_zero", "nod", "go_photo"]
  → 串行执行：回零 → 等待到位 → 点头 → 等待完成 → 拍照位置
```

### 23.4 修改文件

- `gui/widgets/camera_panel.py` — 添加 `_parse_action_sequence`、`_execute_action_sequence`、`_SequenceWorker`

---

## 二十四、放回原位功能

### 24.1 功能

抓取物体后，可以通过语音指令"放回原位"将物体放回原来的位置。

### 24.2 流程

```text
抓取时：记录像素坐标 (u,v) 和世界坐标 (x,y,z)
  ↓
用户说"放回原位"
  ↓
① 移动到原位（grasp_at_pixel）
② 松开夹爪
③ 回到拍照位置
④ 清除记录
```

### 24.3 关键词

放回原位、放回去、放回原来、归位、送回去、放还原

### 24.4 优势

比放置到容器更简单，因为目标位置已知（抓取时记录），不需要重新检测。

### 24.5 修改文件

- `gui/widgets/camera_panel.py` — 添加 `_record_grasp_position`、`_execute_put_back`、`_on_put_back_arrived`、`_put_back_return_photo`
