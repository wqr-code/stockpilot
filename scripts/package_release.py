"""Package only source files and public example data."""
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
DATA = {'README.md', 'products.csv', 'sales.csv', 'inventory_ledger.csv', 'retail_catalog.json'}


def release_files():
    for path in sorted(ROOT.rglob('*')):
        rel = path.relative_to(ROOT)
        if not path.is_file() or path.is_symlink():
            continue
        if len(rel.parts) == 1:
            if path.suffix not in {'.md', '.txt', '.ini'} and path.name not in {'LICENSE', '.gitignore', '.env.demo.example'}:
                continue
        else:
            if rel.parts[0] not in {'demo', 'tests', 'scripts'}:
                continue
            if any(p.startswith('.') or p == '__pycache__' for p in rel.parts):
                continue
            if path.suffix not in {'.py', '.js', '.cjs', '.html', '.css', '.svg', '.json', '.csv', '.md'}:
                continue
            if rel.parts[:2] == ('demo', 'data') and (len(rel.parts) != 3 or path.name not in DATA):
                continue
        yield path


if __name__ == '__main__':
    files = list(release_files())
    for path in files:
        if re.search(rb'sk-[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', path.read_bytes()):
            raise ValueError(f'Possible secret in {path.relative_to(ROOT)}')
    output = ROOT/'dist'/'stockpilot-source.zip'
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, Path('stockpilot')/path.relative_to(ROOT))
    print(f'Packaged {len(files)} files: {output.name}')
