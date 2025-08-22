from datetime import datetime, UTC
def log(*a):
    print(datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"), "-", *a, flush=True)
