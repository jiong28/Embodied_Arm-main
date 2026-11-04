# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HorizonArm (Embodied_Arm) is a Python SDK for controlling a 6-DOF robotic arm (X1/H1 series). It provides motion control, computer vision grasping, AI integration (LLM/ASR/TTS), Joy-Con teleoperation, digital twin simulation, and embodied intelligence. Targets CPython 3.12 on Windows (primary), with Linux/macOS support for dependencies.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Verify SDK integrity (checks all module imports and key exports)
python verify_sdk_integrity.py

# Run examples (from project root)
cd example
python quickstart_guide.py          # 5-minute motor connection intro
python sdk_quickstart.py            # Full arm control walkthrough
python test_interactive.py          # 40+ single motor functions
python test_multi_motor_sync.py     # 26+ multi-motor sync functions

# MkDocs documentation (from example/)
cd example
mkdocs serve
```

There is no formal test suite, build system, linter, or CI/CD. Testing is done via interactive example scripts.

## Architecture

The project uses a three-layer architecture:

### Horizon_Core (compiled, opaque)
`Horizon_Core/` contains `.pyd` compiled Python extensions. These are not editable source code.

- **`gateway.pyd`** — Central authorization/licensing gateway. All core access flows through this.
- **`Control_SDK/Control_Core/`** — Motor control via CAN bus (ZDT closed-loop drivers). Key class: `ZDTMotorController`. Exposes `control_actions`, `homing_commands`, `read_parameters`, `modify_parameters`, `trigger_actions`.
- **`AI_SDK/`** — LLM, ASR, TTS, multimodal services. Providers: Alibaba (Qwen/DashScope), DeepSeek.
- **`core/arm_core/`** — Kinematics, trajectory planning, depth estimation, hand-eye calibration, YOLO detection, RRT planner.
- **`core/embodied_core/`** — Hierarchical decision system, MuJoCo kinematics, embodied AI functions.

### Embodied_SDK (editable Python source)
`Embodied_SDK/` is the developer-facing API wrapping Horizon_Core. This is where most development happens.

- **`horizon_sdk.py`** — `HorizonArmSDK`: top-level aggregator binding all sub-SDKs. Primary entry point.
- **`motion.py`** — `MotionSDK`, `create_motor_controller()`: joint/Cartesian motion control.
- **`visual_grasp.py`** — `VisualGraspSDK`, `FollowGraspSDK`: pixel/bbox grasp, YOLO+CSRT tracking.
- **`ai.py`** — `AISDK`, `DepthEstimationSDK`: LLM/ASR/TTS/multimodal via gateway.
- **`embodied.py`** — `EmbodiedSDK`: natural language to action planning via hierarchical decision system.
- **`joycon.py`** — `JoyconSDK`: Nintendo Joy-Con teleoperation.
- **`io.py`** — `IOSDK`: ESP32 digital I/O control.
- **`digital_twin.py`** — `DigitalTwinSDK`: MuJoCo simulation.

All sub-SDKs are re-exported from `Embodied_SDK/__init__.py`.

### Configuration
`config/` holds all runtime configuration. Key files:
- `motor_config.json` — Motor reducer ratios and directions (6 motors, IDs 1-6)
- `dh_parameters_config.json` — 6-DOF DH parameters, joint offsets and limits
- `calibration_parameter.json` — Camera intrinsics, stereo params, hand-eye calibration RT matrix
- `aisdk_config.yaml` — AI provider API keys, model selection, caching
- `all_parameter_config.json` — Vision grasp, embodied intelligence, Joy-Con control params
- `embodied_config/preset_actions.json` — Named preset motions (nod, shake, initial position)
- `urdf/new_arm.urdf` — URDF robot description
- `urdf/mjmodel.xml` — MuJoCo model file
- `yolov8n.onnx` — YOLOv8 nano model for object detection

## Key Patterns

**Motor controller creation**: Always via `create_motor_controller(motor_id=N, port="COMx")`. For multi-motor sync, use `shared_interface=True` on all motors after the first.

**Shared interface**: Multi-motor control requires a shared CAN interface. The first motor creates it; subsequent motors join with `shared_interface=True`.

**Config directory override**: Set `HORIZONARM_CONFIG_DIR` environment variable to change config path.

**AI API keys**: Set `ALIBABA_API_KEY` and/or `DEEPSEEK_API_KEY` environment variables, or configure in `config/aisdk_config.yaml`.

**Hardware**: 6 ZDT closed-loop motors (CAN bus via SLCAN, 500k baud), ESP32 I/O controller (serial 115200), optional Joy-Con (Bluetooth), single or stereo cameras.

## Image Recognition

进行图片识别任务时，优先使用主模型自身的多模态能力识图。仅当主模型不支持多模态时，才使用 mimo-image-recognition MCP（`understand_image` 工具）。MCP 配置见项目根目录 `.mcp.json`，安装指南见 `MCP_MIMO_ImageRecognition_安装指南.md`。

## Development Notes

**Change log requirement**: Every time you make code changes (new files, modified files, bug fixes), append a summary to `gui_report.md` under a new numbered section (e.g., `### 8.x`). This file serves as the project change log and context handoff document. If the conversation crashes or another AI takes over, they can read `gui_report.md` to understand all prior modifications. Include: problem description, root cause, solution, and list of modified files.

- `Horizon_Core/` modules are compiled `.pyd` files — do not attempt to edit them. All customization happens in `Embodied_SDK/` or by creating new scripts that import from it.
- The SDK is designed to be GUI-independent; it does not require PyQt5 at the import level.
- `numpy<2.0.0` is pinned due to compatibility constraints in the compiled core.
- `opencv-contrib-python` (not `opencv-python`) is required — the CSRT tracker in `visual_grasp.py` depends on `cv2.TrackerCSRT_create` from the contrib build.
- The `verify_sdk_integrity.py` script is useful after environment setup to confirm all modules load correctly.
