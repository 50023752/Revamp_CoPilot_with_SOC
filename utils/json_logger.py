import json
import logging
import datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # ISO8601 UTC timestamp
        try:
            timestamp = datetime.datetime.fromtimestamp(
                record.created, tz=datetime.timezone.utc
            ).isoformat()
        except Exception:
            timestamp = self.formatTime(record)

        log_record = {
            "level": record.levelname,
            "message": record.getMessage(),
            "timestamp": timestamp,
            "component": record.name,
            "module": getattr(record, "module", None),
            "funcName": getattr(record, "funcName", None),
            "lineno": getattr(record, "lineno", None),
            "process": getattr(record, "process", None),
            "thread": getattr(record, "thread", None),
        }

        # Allow attaching extra props via record.props (conventional usage)
        if hasattr(record, "props") and isinstance(record.props, dict):
            try:
                log_record.update(record.props)
            except Exception:
                # Don't let extra data break logging
                log_record["props_error"] = "failed to serialize record.props"

        # Include exception info if present
        if record.exc_info:
            try:
                log_record["exc_info"] = self.formatException(record.exc_info)
            except Exception:
                log_record["exc_info"] = "<unavailable>"

        # Ensure JSON serialization - fall back to string conversion for unknown types
        return json.dumps(log_record, default=str)


def get_json_logger(name: str = "orion") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
