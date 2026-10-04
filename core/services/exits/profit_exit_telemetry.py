import json
import logging
import logging.handlers
import os
import threading
import time

TELEMETRY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'logs')
TELEMETRY_FILE = os.path.join(TELEMETRY_DIR, 'profit_exit_telemetry.jsonl')
# Bounded logging: rollover at ~10 MB, keep at most 5 uncompressed backups.
TELEMETRY_MAX_BYTES = 10 * 1024 * 1024
TELEMETRY_BACKUP_COUNT = 5
_LOGGER_INIT_LOCK = threading.Lock()

def get_telemetry_logger():
    logger = logging.getLogger('ProfitExitTelemetry')
    if not logger.handlers:
        with _LOGGER_INIT_LOCK:
            # Re-check under lock: exactly one handler per process/logger.
            if not logger.handlers:
                logger.setLevel(logging.INFO)
                try:
                    os.makedirs(TELEMETRY_DIR, exist_ok=True)
                    handler = logging.handlers.RotatingFileHandler(
                        TELEMETRY_FILE,
                        maxBytes=TELEMETRY_MAX_BYTES,
                        backupCount=TELEMETRY_BACKUP_COUNT,
                    )
                    handler.setFormatter(logging.Formatter('%(message)s'))
                    logger.addHandler(handler)
                except Exception:
                    pass
    return logger

class ProfitExitTelemetry:
    ENABLED = True
    
    @classmethod
    def log_event(cls, position_id, event_type, data):
        if not cls.ENABLED:
            return
        try:
            logger = get_telemetry_logger()
            payload = {
                'timestamp_ms': time.time() * 1000,
                'position_id': position_id,
                'event_type': event_type,
                'data': data
            }
            logger.info(json.dumps(payload))
        except Exception:
            pass
