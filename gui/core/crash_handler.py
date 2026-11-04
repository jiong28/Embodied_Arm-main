import atexit
import signal
import sys
import logging

logger = logging.getLogger(__name__)

_cleanup_func = None
_registered = False


def register_cleanup(cleanup_func):
    global _cleanup_func, _registered
    _cleanup_func = cleanup_func

    if not _registered:
        atexit.register(_safe_cleanup)

        try:
            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)
        except (OSError, ValueError):
            pass

        sys.excepthook = _excepthook_handler
        _registered = True


def _safe_cleanup():
    if _cleanup_func:
        try:
            _cleanup_func()
        except Exception as e:
            logger.error(f"清理时出错: {e}")


def _signal_handler(signum, frame):
    logger.info(f"收到信号 {signum}，正在清理...")
    _safe_cleanup()
    sys.exit(0)


def _excepthook_handler(exc_type, exc_value, exc_tb):
    logger.error(f"未捕获异常: {exc_type.__name__}: {exc_value}", exc_info=(exc_type, exc_value, exc_tb))
    _safe_cleanup()
    sys.__excepthook__(exc_type, exc_value, exc_tb)
