"""Reserve the API socket before importing any trading application code."""
import errno
import os
import socket
import sys


def main():
    try:
        port = int(os.environ.get("PORT", "8006"))
        if not 1 <= port <= 65535:
            raise ValueError("port out of range")
    except ValueError:
        print("Invalid PORT: expected an integer from 1 to 65535.", file=sys.stderr)
        return 74

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("0.0.0.0", port))
            listener.listen(2048)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                print(f"Port {port} is already in use; bot was NOT started.", file=sys.stderr)
                return 73
            raise
        # Pass the SAME socket across exec; never release it before startup.
        listener.set_inheritable(True)
        os.execv(sys.executable, [
            sys.executable, "-m", "uvicorn", "services.api:app",
            "--host", "0.0.0.0", "--port", str(port),
            "--fd", str(listener.fileno()),
        ])
    return 0


if __name__ == "__main__":
    sys.exit(main())
