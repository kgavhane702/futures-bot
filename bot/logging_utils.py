from datetime import datetime, UTC
from collections import deque
import threading, queue

_log_buffer = deque(maxlen=500)
_subscribers = []
_subs_lock = threading.Lock()

def _broadcast(msg: str):
    with _subs_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        if dead:
            for q in dead:
                try:
                    _subscribers.remove(q)
                except Exception:
                    pass

def log(*a):
    msg = f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} - {' '.join(str(x) for x in a)}"
    print(msg, flush=True)
    _log_buffer.append(msg)
    _broadcast(msg)

def recent_logs() -> list[str]:
    return list(_log_buffer)

def subscribe_logs():
    q = queue.Queue(maxsize=1000)
    with _subs_lock:
        _subscribers.append(q)
    return q

def unsubscribe_logs(q):
    with _subs_lock:
        try:
            _subscribers.remove(q)
        except Exception:
            pass
