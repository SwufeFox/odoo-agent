"""Local-only Odoo20 development lifecycle; all generated data stays in .runtime."""
import argparse
import configparser
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
SOURCE = RUNTIME / 'odoo'
REF = '7d8d985d0b4e5bf758b2a493e0da7f3ddce1383c'
PG_URL = 'https://get.enterprisedb.com/postgresql/postgresql-17.12-1-windows-x64-binaries.zip'
WINDOWS = os.name == 'nt'
PYTHON = RUNTIME / 'venv' / ('Scripts/python.exe' if WINDOWS else 'bin/python')
PG_BIN = Path(os.environ.get('MOPS_PG_BIN', str(RUNTIME / 'pgsql' / 'bin')))
CONF = RUNTIME / 'odoo.conf'
DB = 'mops_demo'
DB_PORT = '55432'


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **kwargs)


def pg(name):
    candidate = PG_BIN / (name + ('.exe' if WINDOWS else ''))
    found = str(candidate) if candidate.is_file() else shutil.which(name)
    if not found:
        raise SystemExit(f'找不到 {name}，设置 MOPS_PG_BIN 指向 PostgreSQL 16+ bin 目录。')
    return found


def settings():
    if not CONF.exists():
        raise SystemExit('请先运行 python scripts/dev.py setup。')
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(CONF, encoding='utf-8')
    return cfg['options']


def db_env():
    return dict(os.environ, PGPASSWORD=settings()['db_password'])


def setup():
    RUNTIME.mkdir(exist_ok=True)
    if not SOURCE.exists():
        run(['git', 'clone', '--depth', '1', '--branch', '20.0', 'https://github.com/odoo/odoo.git', SOURCE])
        run(['git', '-C', SOURCE, 'fetch', '--depth', '1', 'origin', REF])
        run(['git', '-C', SOURCE, 'checkout', '--detach', REF])
    else:
        current = run(['git', '-C', SOURCE, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        if current != REF:
            raise SystemExit(f'Odoo 源码提交为 {current}，需要 {REF}；请保留现有修改后另建运行目录。')
    uv = shutil.which('uv') or str(ROOT / '.venv' / ('Scripts/uv.exe' if WINDOWS else 'bin/uv'))
    if not Path(uv).is_file() and not shutil.which(uv):
        tools_python = ROOT / '.venv' / ('Scripts/python.exe' if WINDOWS else 'bin/python')
        if not tools_python.exists():
            run([sys.executable, '-m', 'venv', ROOT / '.venv'])
        run([tools_python, '-m', 'pip', 'install', 'uv==0.13.8'])
    if not PYTHON.exists():
        run([uv, 'venv', '--python', '3.12', RUNTIME / 'venv'])
    run([uv, 'pip', 'install', '--python', PYTHON, '-r', SOURCE / 'requirements.txt'])
    if WINDOWS and not (PG_BIN / 'pg_ctl.exe').exists():
        archive = RUNTIME / 'postgresql.zip'
        if not archive.exists():
            print('下载本地 PostgreSQL 17 二进制包……', flush=True)
            urllib.request.urlretrieve(PG_URL, archive)
        with zipfile.ZipFile(archive) as package:
            members = [n for n in package.namelist() if n.startswith(('pgsql/bin/', 'pgsql/lib/', 'pgsql/share/'))]
            if not members:
                raise SystemExit('PostgreSQL 压缩包目录结构不符合预期。')
            package.extractall(RUNTIME, members=members)
    configure()


def configure():
    if not CONF.exists():
        cfg = configparser.ConfigParser(interpolation=None)
        cfg['options'] = {
            'addons_path': f'{SOURCE / "addons"},{ROOT / "addons"}',
            'data_dir': str(RUNTIME / 'data'), 'db_host': '127.0.0.1',
            'db_port': DB_PORT, 'db_user': 'mops', 'db_password': secrets.token_urlsafe(32),
            'db_name': DB, 'dbfilter': '^mops_demo$', 'list_db': 'False',
            'admin_passwd': secrets.token_urlsafe(32), 'http_interface': '127.0.0.1',
            'http_port': '8069', 'workers': '0', 'max_cron_threads': '0',
            'smtp_server': '127.0.0.1', 'smtp_port': '1', 'without_demo': 'True',
        }
        with CONF.open('w', encoding='utf-8') as stream:
            cfg.write(stream)
        CONF.chmod(0o600)
    if not (RUNTIME / 'pgdata' / 'PG_VERSION').exists():
        password_file = RUNTIME / 'pg-password'
        password_file.write_text(settings()['db_password'], encoding='utf-8')
        password_file.chmod(0o600)
        try:
            run([pg('initdb'), '-D', RUNTIME / 'pgdata', '-U', 'mops', '--pwfile', password_file, '--auth=scram-sha-256', '--encoding=UTF8', '--locale=C'])
        finally:
            password_file.unlink(missing_ok=True)
    print('本地依赖与独立数据库目录已准备好。接着执行 db-start、init、seed、run。')


def ensure_database(name):
    connection = [pg('psql'), '-h', '127.0.0.1', '-p', DB_PORT, '-U', 'mops', '-d', 'postgres', '-tAc']
    result = run(connection + [f"SELECT 1 FROM pg_database WHERE datname='{name}'"], env=db_env(), capture_output=True, text=True)
    if result.stdout.strip() != '1':
        run([pg('createdb'), '-h', '127.0.0.1', '-p', DB_PORT, '-U', 'mops', '-T', 'template0', '--encoding=UTF8', name], env=db_env())


def odoo_args(*extra):
    return [PYTHON, SOURCE / 'odoo-bin', '-c', CONF, *extra]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['setup', 'configure', 'db-start', 'db-stop', 'init', 'upgrade', 'test', 'seed', 'run'])
    args = parser.parse_args()
    if args.command == 'setup':
        setup()
    elif args.command == 'configure':
        configure()
    elif args.command == 'db-start':
        run([pg('pg_ctl'), '-D', RUNTIME / 'pgdata', '-l', RUNTIME / 'postgres.log', '-o', f'-p {DB_PORT} -h 127.0.0.1', '-w', 'start'])
    elif args.command == 'db-stop':
        run([pg('pg_ctl'), '-D', RUNTIME / 'pgdata', '-m', 'fast', '-w', 'stop'])
    elif args.command in ('init', 'upgrade', 'test'):
        name = 'mops_test' if args.command == 'test' else DB
        ensure_database(name)
        test = ['-u', 'microscope_office_mvp', '--http-interface=127.0.0.1', '--http-port=18069', '--test-enable', '--test-tags', '/microscope_office_mvp', '--log-level=test'] if args.command == 'test' else []
        flag = '-u' if args.command == 'upgrade' else '-i'
        log = RUNTIME / f'{args.command}.log'
        with log.open('w', encoding='utf-8') as stream:
            result = subprocess.run(odoo_args('-d', name, flag, 'microscope_office_mvp', '--without-demo=True', '--no-http', '--stop-after-init', *test), cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        print(f'{args.command}: exit={result.returncode}; log={log}')
        raise SystemExit(result.returncode)
    elif args.command == 'seed':
        run([PYTHON, ROOT / 'scripts' / 'seed_demo.py', '--config', CONF, '--source', SOURCE])
    else:
        run(odoo_args())


if __name__ == '__main__':
    main()
