"""Startup tests use dummy sockets/modules, never import the trading app."""
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('serve_bot', ROOT / 'tools/serve_bot.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.mark.parametrize('port', ['abc', '0', '65536'])
def test_invalid_port_never_starts_app(monkeypatch, port):
    monkeypatch.setenv('PORT', port)
    monkeypatch.setattr(guard.os, 'execv', lambda *_: pytest.fail('App started'))
    assert guard.main() == 74


def test_occupied_port_never_starts_app(monkeypatch):
    with socket.socket() as listener:
        listener.bind(('0.0.0.0', 0))
        listener.listen()
        monkeypatch.setenv('PORT', str(listener.getsockname()[1]))
        monkeypatch.setattr(guard.os, 'execv', lambda *_: pytest.fail('App started'))
        assert guard.main() == 73


def test_socket_survives_exec_without_release(tmp_path):
    # Replace uvicorn with a harmless module that inspects the inherited FD.
    (tmp_path / 'uvicorn.py').write_text('''
import socket, sys
fd = int(sys.argv[sys.argv.index('--fd') + 1])
s = socket.socket(fileno=fd)
assert s.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
with socket.socket() as competitor:
    try:
        competitor.bind(s.getsockname())
    except OSError:
        print('RESERVATION_OK')
    else:
        raise AssertionError('Socket reservation lost')
''')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, PORT=str(port), PYTHONPATH=str(tmp_path))
    result = subprocess.run([sys.executable, str(ROOT / 'tools/serve_bot.py')],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'RESERVATION_OK' in result.stdout


def test_launcher_lock_and_graceful_stop_from_other_directory(tmp_path):
    import fcntl
    import shutil
    import time
    project = tmp_path / 'dummy project'
    (project / '.venv/bin').mkdir(parents=True)
    (project / 'tools').mkdir()
    shutil.copy(ROOT / 'start.sh', project / 'start.sh')
    (project / '.venv/bin/python3').symlink_to(sys.executable)
    (project / 'tools/serve_bot.py').write_text('''
import os, signal, time
from pathlib import Path
Path('ready').write_text(str(os.getpid()))
def stop(*_):
    Path('stopped').touch()
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
while True: time.sleep(0.05)
''')
    first = subprocess.Popen(['bash', str(project / 'start.sh')], cwd=tmp_path,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not (project / 'ready').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (project / 'ready').exists()
        duplicate = subprocess.run(['bash', str(project / 'start.sh')], cwd=tmp_path,
                                   capture_output=True, text=True, timeout=5)
        assert duplicate.returncode == 0
        assert 'refusing duplicate start' in duplicate.stdout
        first.terminate()
        assert first.wait(timeout=5) == 0
        assert (project / 'stopped').exists()
        with (project / 'data/binance-futures-bot.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if first.poll() is None:
            first.terminate()
            first.wait(timeout=5)
