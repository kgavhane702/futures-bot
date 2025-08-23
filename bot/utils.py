from datetime import datetime, UTC

from .config import TZ
from .state import STATE


def log(*a):
    line = f"{datetime.now(UTC).astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S')} - {' '.join(str(x) for x in a)}"
    print(line, flush=True)
    try:
        STATE.append_log(line)
    except Exception:
        pass


