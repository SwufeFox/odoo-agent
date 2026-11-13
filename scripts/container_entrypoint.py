"""Shared Docker entry point, using the pinned official Odoo20 source."""
import configparser
import os
from pathlib import Path
import subprocess
import sys

SOURCE = Path('/opt/odoo')
ROOT = Path('/workspace')
CONF = Path('/tmp/mops-odoo.conf')
config = configparser.ConfigParser(interpolation=None)
config['options'] = {
    'addons_path': f'{SOURCE / "addons"},{ROOT / "addons"}',
    'data_dir': '/var/lib/odoo', 'db_host': os.environ['DB_HOST'],
    'db_port': os.environ.get('DB_PORT', '5432'), 'db_user': os.environ['DB_USER'],
    'db_password': os.environ['DB_PASSWORD'], 'db_name': os.environ.get('DB_NAME', 'mops_demo'),
    'admin_passwd': os.environ['ODOO_MASTER_PASSWORD'], 'list_db': 'False',
    'dbfilter': '^mops_demo$', 'http_interface': '0.0.0.0', 'http_port': '8069',
    'workers': '0', 'max_cron_threads': '0', 'smtp_server': '127.0.0.1', 'smtp_port': '1',
    'without_demo': 'True',
}
with CONF.open('w', encoding='utf-8') as stream:
    config.write(stream)
CONF.chmod(0o600)
mode = sys.argv[1] if len(sys.argv) > 1 else 'run'
if mode == 'seed':
    os.execv(sys.executable, [sys.executable, str(ROOT / 'scripts/seed_demo.py'), '--config', str(CONF), '--source', str(SOURCE), '--credentials', '/var/lib/odoo/demo_credentials.json'])
args = [sys.executable, str(SOURCE / 'odoo-bin'), '-c', str(CONF)]
if mode == 'run':
    # Initialize without exposing the default bootstrap account; seed before HTTP starts.
    subprocess.run(args + ['-i', 'microscope_office_mvp', '--without-demo=True', '--stop-after-init', '--no-http'], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/seed_demo.py'), '--config', str(CONF), '--source', str(SOURCE), '--credentials', '/var/lib/odoo/demo_credentials.json'], check=True)
elif mode == 'upgrade':
    args += ['-u', 'microscope_office_mvp', '--stop-after-init', '--no-http']
elif mode == 'test':
    args += ['-d', 'mops_test', '-i', 'microscope_office_mvp', '-u', 'microscope_office_mvp', '--http-interface=127.0.0.1', '--http-port=18069', '--test-enable', '--test-tags', '/microscope_office_mvp', '--stop-after-init', '--no-http', '--log-level=test']
else:
    raise SystemExit('Usage: run | seed | upgrade | test')
os.execv(sys.executable, args)
