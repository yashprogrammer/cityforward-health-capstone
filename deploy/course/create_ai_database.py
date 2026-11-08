"""Create a separate AI database on the synthetic course PostgreSQL server.

Run on the demo VM, before starting the integrated AI service. It writes an
unprinted, randomly generated database password to the private runtime file.
It refuses to take over an existing role or database.
"""
import secrets
import subprocess
from pathlib import Path

runtime = Path.home() / 'cityforward-integrated' / 'runtime.conf'
contents = runtime.read_text()
if any(line.startswith('INTEGRATED_DB_PASSWORD=') for line in contents.splitlines()):
    raise SystemExit('AI database password already exists; verify existing provisioning before retrying.')

def psql(database, sql):
    result = subprocess.run(['sudo', 'docker', 'exec', '-i', 'cityforward-course-db-1',
                             'psql', '-U', 'postgres', '-d', database, '-v', 'ON_ERROR_STOP=1', '-tA'],
                            input=sql, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('PostgreSQL provisioning failed; review database state before retrying.')
    return result.stdout.strip()

role = psql('postgres', "SELECT count(*) FROM pg_roles WHERE rolname='cf_integrated_ai';")
database = psql('postgres', "SELECT count(*) FROM pg_database WHERE datname='cityforward_integrated_ai';")
if role != '0' or database != '0':
    raise SystemExit('Refusing to overwrite an existing AI role or database.')

password = secrets.token_hex(32)  # hexadecimal is safe in a PostgreSQL URL
runtime.write_text(contents + 'INTEGRATED_DB_PASSWORD=' + password + '\n')
runtime.chmod(0o600)
psql('postgres', "CREATE ROLE cf_integrated_ai LOGIN PASSWORD '" + password + "';\n")
psql('postgres', 'CREATE DATABASE cityforward_integrated_ai OWNER cf_integrated_ai;\n')
psql('cityforward_integrated_ai', 'CREATE EXTENSION IF NOT EXISTS vector;\n')
print('Created the separate AI database, role and vector extension.')
