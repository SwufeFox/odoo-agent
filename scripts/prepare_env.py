"""Generate local Compose credentials without replacing an existing .env."""
from pathlib import Path
import secrets

path = Path(__file__).resolve().parents[1] / '.env'
if path.exists():
    raise SystemExit('.env 已存在，保留当前配置。')
with path.open('x', encoding='utf-8') as stream:
    stream.write(f'DB_PASSWORD={secrets.token_urlsafe(32)}\nODOO_MASTER_PASSWORD={secrets.token_urlsafe(32)}\nODOO_PORT=8069\n')
path.chmod(0o600)
print('已生成 .env；凭据只保存在本地。')
