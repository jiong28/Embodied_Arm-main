import os
import io
import json
import wave
import time
import struct
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, List

import requests
import numpy as np
from PyQt5.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger(__name__)

_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
)


@dataclass
class VoiceConfig:
    api_key: str = ""
    asr_model: str = "paraformer-realtime-v2"
    tts_model: str = "cosyvoice-v1"
    tts_voice: str = "longxiaochun"
    edge_voice: str = "zh-CN-YunyangNeural"
    tts_sample_rate: int = 48000
    sample_rate: int = 16000
    chunk_size: int = 1024
    silence_threshold: int = 300
    silence_duration: float = 0.8
    max_record_duration: int = 15
    stop_keywords: List[str] = field(default_factory=lambda: ["停止", "结束", "退出", "关闭", "再见"])
    wake_word: str = "贾维斯"
    idle_timeout: int = 30
    llm_system_prompt: str = "你是一个机械臂视觉助手。请简洁明了地回答用户的问题，回答控制在100字以内。"
    llm_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-turbo"

    @classmethod
    def load(cls) -> "VoiceConfig":
        cfg = cls()
        voice_path = os.path.join(_CONFIG_DIR, "voice_config.json")
        if os.path.exists(voice_path):
            with open(voice_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        llm_path = os.path.join(_CONFIG_DIR, "llm_config.json")
        if os.path.exists(llm_path):
            with open(llm_path, "r", encoding="utf-8") as f:
                llm = json.load(f)
            if not cfg.api_key and llm.get("api_key"):
                cfg.api_key = llm["api_key"]
            if llm.get("api_base"):
                cfg.llm_api_base = llm["api_base"]
        return cfg


def _safe_str(val) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="ignore")
    if isinstance(val, dict):
        return val.get("text", "") if "text" in val else json.dumps(val, ensure_ascii=False)
    return str(val)


# ---- 唤醒词监听 Worker ----

class WakeWordWorker(QThread):
    """后台持续监听唤醒词"""
    wake_detected = pyqtSignal()
    partial_text = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, config: VoiceConfig):
        super().__init__()
        self._config = config
        self._should_stop = False

    def request_stop(self):
        self._should_stop = True

    def run(self):
        try:
            import pyaudio
            import websocket
        except ImportError as e:
            self.error.emit(f"依赖缺失: {e}")
            return

        while not self._should_stop:
            try:
                self._listen_once()
            except Exception as e:
                if not self._should_stop:
                    logger.debug(f"唤醒词监听异常，重试: {e}")
                    import time
                    time.sleep(1)

    def _listen_once(self):
        import pyaudio
        import websocket

        ws_url = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
        headers = [
            f"Authorization: bearer {self._config.api_key}",
            "X-DashScope-DataInspection: enable",
        ]
        ws = websocket.create_connection(ws_url, header=headers, timeout=10)
        self._active_ws = ws

        run_id = f"wake_{id(self)}_{threading.get_ident()}"
        ws.send(json.dumps({
            "header": {"action": "run-task", "streaming": "duplex",
                       "task_id": run_id, "task": "asr"},
            "payload": {"task_group": "audio", "task": "asr",
                        "function": "recognition",
                        "model": self._config.asr_model,
                        "parameters": {"sample_rate": self._config.sample_rate, "format": "pcm"},
                        "input": {}},
        }))

        audio = pyaudio.PyAudio()
        stream = audio.open(format=pyaudio.paInt16, channels=1,
                            rate=self._config.sample_rate, input=True,
                            frames_per_buffer=self._config.chunk_size)
        self._active_stream = stream

        wake_word = self._config.wake_word
        found = [False]

        def recv():
            try:
                while not self._should_stop and not found[0]:
                    try:
                        ws.settimeout(0.5)
                        msg = ws.recv()
                        if isinstance(msg, str):
                            d = json.loads(msg)
                            ev = d.get("header", {}).get("event", "")
                            if ev == "result-generated":
                                out = d.get("payload", {}).get("output", {})
                                text = _safe_str(out.get("sentence", "")) or _safe_str(out.get("text", ""))
                                if text:
                                    self.partial_text.emit(text)
                                    if wake_word in text:
                                        found[0] = True
                                        self.wake_detected.emit()
                            elif ev in ("task-finished", "task-failed"):
                                break
                    except websocket.WebSocketTimeoutException:
                        continue
                    except Exception:
                        break
            finally:
                pass

        recv_t = threading.Thread(target=recv, daemon=True)
        recv_t.start()

        try:
            while not self._should_stop and not found[0]:
                data = stream.read(self._config.chunk_size, exception_on_overflow=False)
                try:
                    ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
                except Exception:
                    break
        finally:
            self._active_stream = None
            self._active_ws = None
            try:
                ws.send(json.dumps({
                    "header": {"action": "finish-task", "streaming": "duplex",
                               "task_id": run_id, "task": "asr"},
                    "payload": {"input": {}},
                }))
            except Exception:
                pass
            try:
                stream.stop_stream()
                stream.close()
                audio.terminate()
            except Exception:
                pass
            try:
                ws.close()
            except Exception:
                pass


# ---- 对话 Session ----

class ConversationSession(QThread):
    """连续对话会话"""
    asr_result = pyqtSignal(str)
    asr_partial = pyqtSignal(str)
    tts_started = pyqtSignal()
    tts_finished = pyqtSignal()
    error = pyqtSignal(str)
    session_started = pyqtSignal()
    session_ended = pyqtSignal()

    def __init__(self, config: VoiceConfig):
        super().__init__()
        self._config = config
        self._should_stop = False
        self._response_event = threading.Event()
        self._response_text = ""
        self._last_activity = 0.0
        self._tts_playing = False  # TTS 播放标志

    def request_stop(self):
        self._should_stop = True
        self._response_event.set()

    def provide_response(self, text: str):
        """提供回复并重置活动时间（指令执行完成也算活动）"""
        self._last_activity = time.time()
        self._response_text = text
        self._response_event.set()

    def run(self):
        import time
        self._last_activity = time.time()
        self.session_started.emit()
        logger.info("对话会话已启动")

        while not self._should_stop:
            self.asr_partial.emit("请说话...")
            text = self._do_asr()
            if self._should_stop:
                break

            if not text:
                # 没有语音，检查是否超时
                if time.time() - self._last_activity > self._config.idle_timeout:
                    logger.info(f"对话超时 ({self._config.idle_timeout}秒无语音)")
                    break
                continue

            # TTS 播放期间忽略 ASR 结果（防止回声）
            if self._tts_playing:
                continue

            # 检测到语音，立即处理
            self._last_activity = time.time()

            if any(kw in text for kw in self._config.stop_keywords):
                logger.info(f"停止关键词: {text}")
                break

            # 发送 ASR 结果 → 立即执行指令
            self.asr_result.emit(text)

            # 等待指令执行完成并获取回复
            self._response_event.clear()
            self._response_text = ""
            got = self._response_event.wait(timeout=60)
            if self._should_stop:
                break
            if got and self._response_text:
                self._tts_playing = True
                try:
                    self._do_tts(self._response_text)
                finally:
                    self._tts_playing = False
                # 等待 1 秒让喇叭静音后再开始 ASR（防止回声）
                time.sleep(1.0)
                self._last_activity = time.time()

        self.session_ended.emit()
        logger.info("对话会话已结束")

    def _do_asr(self) -> Optional[str]:
        """
        流式 ASR：边录边识别。

        判断"说完话"的逻辑：
        - ASR 持续输出 partial 文字
        - 当文字连续 2 秒不再变化 → 认为说完 → 立即返回
        - 如果从未检测到语音 → 返回 None
        """
        try:
            import pyaudio
            import websocket
            import time

            ws_url = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
            headers = [
                f"Authorization: bearer {self._config.api_key}",
                "X-DashScope-DataInspection: enable",
            ]
            ws = websocket.create_connection(ws_url, header=headers, timeout=10)

            run_id = f"asr_{id(self)}_{threading.get_ident()}"
            ws.send(json.dumps({
                "header": {"action": "run-task", "streaming": "duplex",
                           "task_id": run_id, "task": "asr"},
                "payload": {"task_group": "audio", "task": "asr",
                            "function": "recognition",
                            "model": self._config.asr_model,
                            "parameters": {"sample_rate": self._config.sample_rate, "format": "pcm"},
                            "input": {}},
            }))

            audio = pyaudio.PyAudio()
            stream = audio.open(format=pyaudio.paInt16, channels=1,
                                rate=self._config.sample_rate, input=True,
                                frames_per_buffer=self._config.chunk_size)

            # 共享状态
            current_text = [""]
            last_changed_time = [time.time()]
            has_speech = [False]
            final_text = [None]
            ws_done = threading.Event()
            TEXT_STABLE_SECONDS = 2.0  # 文字不变 2 秒 = 说完话

            def recv():
                try:
                    while not self._should_stop and final_text[0] is None:
                        try:
                            ws.settimeout(0.5)
                            msg = ws.recv()
                            if isinstance(msg, str):
                                d = json.loads(msg)
                                ev = d.get("header", {}).get("event", "")
                                if ev == "result-generated":
                                    out = d.get("payload", {}).get("output", {})
                                    s = _safe_str(out.get("sentence", ""))
                                    t = _safe_str(out.get("text", ""))
                                    is_f = out.get("is_final", False)
                                    disp = s or t
                                    if disp and disp != current_text[0]:
                                        current_text[0] = disp
                                        last_changed_time[0] = time.time()
                                        has_speech[0] = True
                                        self.asr_partial.emit(disp)
                                    if is_f and s:
                                        final_text[0] = s
                                        break
                                elif ev in ("task-finished", "task-failed"):
                                    break
                        except websocket.WebSocketTimeoutException:
                            continue
                        except Exception:
                            break
                finally:
                    ws_done.set()

            recv_t = threading.Thread(target=recv, daemon=True)
            recv_t.start()

            # 发送音频帧，直到文字稳定或超时
            max_chunks = int(self._config.max_record_duration * self._config.sample_rate / self._config.chunk_size)

            for _ in range(max_chunks):
                if self._should_stop:
                    break
                data = stream.read(self._config.chunk_size, exception_on_overflow=False)
                try:
                    ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
                except Exception:
                    break

                # 检查文字是否已稳定（说完话）
                if has_speech[0] and current_text[0]:
                    elapsed = time.time() - last_changed_time[0]
                    if elapsed >= TEXT_STABLE_SECONDS:
                        # 文字 2 秒没变化 = 说完话，立即返回
                        break

            # 清理
            stream.stop_stream()
            stream.close()
            audio.terminate()

            try:
                ws.send(json.dumps({
                    "header": {"action": "finish-task", "streaming": "duplex",
                               "task_id": run_id, "task": "asr"},
                    "payload": {"input": {}},
                }))
            except Exception:
                pass

            ws_done.wait(timeout=2)
            try:
                ws.close()
            except Exception:
                pass

            # 返回结果
            result = final_text[0] or (current_text[0] if has_speech[0] else None)
            if result:
                result = result.strip()
            return result if result else None

        except Exception as e:
            logger.error(f"ASR 失败: {e}")
            return None

    def _do_tts(self, text: str):
        """TTS 合成并播放（播放期间暂停麦克风，避免回声）"""
        try:
            self.tts_started.emit()
            import edge_tts
            import asyncio
            import pygame

            tmp_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "config", "_tts_tmp.mp3"
            )

            async def _generate():
                communicate = edge_tts.Communicate(text, self._config.edge_voice)
                await communicate.save(tmp_path)

            asyncio.run(_generate())

            if os.path.exists(tmp_path):
                try:
                    # 用 pygame 播放（支持 stop）
                    pygame.mixer.init()
                    pygame.mixer.music.load(tmp_path)
                    pygame.mixer.music.play()

                    # 等待播放完成（最多 60 秒）
                    import time
                    start = time.time()
                    while pygame.mixer.music.get_busy():
                        if self._should_stop or time.time() - start > 60:
                            pygame.mixer.music.stop()
                            break
                        time.sleep(0.1)

                    pygame.mixer.quit()
                except Exception as e:
                    logger.warning(f"pygame 播放失败: {e}")
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

            self.tts_finished.emit()
        except Exception as e:
            logger.error(f"TTS 失败: {e}")
            self.error.emit(f"语音合成失败: {e}")
            self.tts_finished.emit()


# ---- Voice Manager ----

class VoiceManager(QObject):
    """
    语音交互管理器

    唤醒方式：
      - 喊"贾维斯"（自动待命时）
      - 手动点击语音按钮

    喊"贾维斯" = 点击按钮 = 激活语音功能
    """
    asr_result = pyqtSignal(str)
    asr_partial = pyqtSignal(str)
    tts_started = pyqtSignal()
    tts_finished = pyqtSignal()
    error = pyqtSignal(str)
    state_changed = pyqtSignal(str)
    session_started = pyqtSignal()
    session_ended = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._config = VoiceConfig.load()
        self._state = "idle"
        self._wake_worker: Optional[WakeWordWorker] = None
        self._session: Optional[ConversationSession] = None
        self._old_workers = []  # 保留旧线程引用，防止 GC 导致崩溃

    @property
    def state(self) -> str:
        return self._state

    @property
    def config(self) -> VoiceConfig:
        return self._config

    def _set_state(self, state: str):
        self._state = state
        self.state_changed.emit(state)

    def start_listening(self):
        """开始待命（监听唤醒词）"""
        if self._state != "idle":
            return
        self._set_state("waiting")
        self._start_wake_worker()

    def stop_listening(self):
        """停止所有语音功能"""
        self._stop_wake_worker()
        self._stop_session()
        self._set_state("idle")

    def toggle(self):
        """切换语音状态（和喊贾维斯效果一样）"""
        if self._state == "idle":
            self.start_listening()
        else:
            self.stop_listening()

    def _start_wake_worker(self):
        if self._wake_worker:
            return
        self._wake_worker = WakeWordWorker(self._config)
        self._wake_worker.wake_detected.connect(self._on_wake_detected)
        self._wake_worker.partial_text.connect(self.asr_partial)
        self._wake_worker.error.connect(self._on_wake_error)
        self._wake_worker.start()

    def _stop_wake_worker(self):
        if self._wake_worker:
            self._wake_worker.request_stop()
            # 保留引用防止 GC 在线程退出前销毁 QThread
            self._old_workers.append(self._wake_worker)
            self._wake_worker = None
            # 清理已完成的旧线程
            self._old_workers = [w for w in self._old_workers if w.isRunning()]

    def _on_wake_detected(self):
        """唤醒词检测到 → 激活语音功能（和点按钮效果一样）"""
        logger.info(f"唤醒词 '{self._config.wake_word}' 检测到，激活语音")
        self._stop_wake_worker()
        self._start_session()

    def _on_wake_error(self, msg: str):
        logger.error(f"唤醒词监听错误: {msg}")
        if self._state == "waiting":
            self._wake_worker = None
            self._start_wake_worker()

    def _start_session(self):
        if self._session:
            return
        self._set_state("active")
        self._session = ConversationSession(self._config)
        self._session.asr_result.connect(self.asr_result)
        self._session.asr_partial.connect(self.asr_partial)
        self._session.tts_started.connect(self._on_tts_started)
        self._session.tts_finished.connect(self._on_tts_finished)
        self._session.error.connect(self._on_session_error)
        self._session.session_started.connect(self.session_started)
        self._session.session_ended.connect(self._on_session_ended)
        self._session.start()

    def _stop_session(self):
        if self._session:
            self._session.request_stop()
            self._old_workers.append(self._session)
            self._session = None
            self._old_workers = [w for w in self._old_workers if w.isRunning()]

    def provide_response(self, text: str):
        if self._session:
            self._session.provide_response(text)

    def _on_tts_started(self):
        self._set_state("speaking")
        self.tts_started.emit()

    def _on_tts_finished(self):
        self._set_state("active")
        self.tts_finished.emit()

    def _on_session_error(self, msg: str):
        self.error.emit(msg)

    def _on_session_ended(self):
        """对话结束 → 回到待命"""
        self._session = None
        if self._state != "idle":
            self._set_state("waiting")
            self._start_wake_worker()
        self.session_ended.emit()

    def cancel(self):
        self._stop_wake_worker()
        self._stop_session()
        self._set_state("idle")
