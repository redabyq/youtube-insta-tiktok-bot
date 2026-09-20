"""Run Docker Compose with the topology selected by PROXY_ENABLED in .env."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    # Compose handles quoting, interpolation and environment precedence.
    base = ['docker', 'compose', '--project-directory', str(ROOT)]
    result = subprocess.run(
        base + ['-f', str(ROOT / 'docker-compose.yml'), 'config', '--environment'],
        capture_output=True, text=True, check=True,
    )
    env = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    proxy = env.get('PROXY_ENABLED', 'true').strip().lower() in ('1', 'true', 'yes', 'on')
    filename = 'docker-compose.proxy.yml' if proxy else 'docker-compose.yml'
    result = subprocess.run(base + ['-f', str(ROOT / filename), 'config', '--format', 'json'],
                            capture_output=True, text=True, check=True)
    model = json.loads(result.stdout)
    if env.get('USE_LOCAL_BOT_API', 'true').strip().lower() not in ('1', 'true', 'yes', 'on'):
        model['services'].pop('telegram-bot-api', None)
        model['services'].pop('tun2socks', None)
        model['services']['bot'].get('depends_on', {}).pop('telegram-bot-api', None)
    args = sys.argv[1:] or ['up', '-d', '--build']
    if args[0] == 'up' and '--remove-orphans' not in args:
        args.append('--remove-orphans')
    print('Proxy mode' if proxy else 'Direct mode (no xray or tun2socks)', flush=True)
    return subprocess.run(base + ['-f', '-', *args], input=json.dumps(model), text=True).returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or 'Docker Compose failed.', file=sys.stderr)
        sys.exit(exc.returncode)
