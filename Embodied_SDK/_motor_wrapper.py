#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电机位置代理包装器
=================

解决编译模块 (c_a_p / c_a_j) 内部 joint→motor 转换缺少零点偏移的问题。

背景：
- MotorManager 的转换公式：motor_pos = joint_angle × direction × ratio + zero_offset
- 编译模块的转换公式：motor_pos = joint_angle × direction × ratio（缺少 zero_offset）
- 编译模块无法读取 custom_zero.json

本模块通过代理模式，在编译模块调用电机 API 时自动补偿零点偏移：
- get_position() 返回值自动减去零点偏移
- move_to_position() 自动加上零点偏移
- 其他所有属性/方法透传给原始电机对象
"""

import logging

logger = logging.getLogger(__name__)


class _ControlActionsProxy:
    """代理 motor.control_actions，在 move_to_position 中注入零点偏移补偿。"""

    def __init__(self, real_actions, offset):
        self._real = real_actions
        self._offset = offset

    def move_to_position(self, position=None, **kwargs):
        if position is not None:
            position = float(position) + self._offset
        return self._real.move_to_position(position=position, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _ReadParamsProxy:
    """代理 motor.read_parameters，在 get_position 中扣除零点偏移。"""

    def __init__(self, real_params, offset):
        self._real = real_params
        self._offset = offset

    def get_position(self):
        return self._real.get_position() - self._offset

    def __getattr__(self, name):
        return getattr(self._real, name)


class MotorPositionWrapper:
    """
    电机位置代理包装器。

    解决编译模块 c_a_p / c_a_j 内部 joint→motor 转换缺少零点偏移的问题：
    - get_position() 返回值自动减去零点偏移（让编译模块看到"关节零点=电机零点"的坐标系）
    - move_to_position() 自动加上零点偏移（补偿编译模块漏加的偏移）
    - 其他所有属性/方法透传给原始电机对象
    """

    def __init__(self, motor, zero_offset):
        self._motor = motor
        self._offset = zero_offset
        self._control_actions_proxy = _ControlActionsProxy(motor.control_actions, zero_offset)
        self._read_params_proxy = _ReadParamsProxy(motor.read_parameters, zero_offset)

    @property
    def control_actions(self):
        return self._control_actions_proxy

    @property
    def read_parameters(self):
        return self._read_params_proxy

    def __getattr__(self, name):
        return getattr(self._motor, name)


def wrap_motors_with_zero_offset(motors: dict, custom_zero: dict) -> dict:
    """
    用零点偏移包装电机字典。

    Args:
        motors: 原始电机字典 {motor_id: motor_controller}
        custom_zero: 零点偏移字典 {motor_id: encoder_offset}

    Returns:
        包装后的电机字典（新字典，不修改原字典）
    """
    wrapped = {}
    for mid, motor in motors.items():
        offset = custom_zero.get(mid, 0.0)
        if offset != 0.0:
            wrapped[mid] = MotorPositionWrapper(motor, offset)
            logger.debug(f"电机 {mid}: 已应用零点偏移 {offset:.1f}°")
        else:
            wrapped[mid] = motor
    return wrapped
