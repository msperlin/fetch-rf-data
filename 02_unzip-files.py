"""Extrai os arquivos .zip baixados da Receita Federal (script 01).

Os arquivos compactados ficam em ``[dirs].zip_dir`` e são descompactados em
``[dirs].unzip_dir``. A extração é idempotente por padrão: arquivos já
descompactados são ignorados, permitindo retomar o processamento interrompido.

Opções em ``[unzip]`` no config.toml:

- ``skip_existing``: pula zips cujos membros já foram extraídos.
- ``remove_zip_after_extract``: apaga o .zip depois de extrair com sucesso.
"""

import logging
import time
import zipfile
from pathlib import Path

from fcts.utils import get_config, get_dir_names

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


def unzip_if_needed(zip_path, target_dir, skip_existing=True):
    """Extrai ``zip_path`` em ``target_dir`` apenas se necessário.

    Retorna ``True`` quando a extração foi feita e ``False`` quando todos os
    membros já existiam (mesmo tamanho) e ``skip_existing`` é verdadeiro.
    Levanta ``ValueError`` se algum membro apontar para fora de ``target_dir``
    (proteção contra Zip Slip).
    """
    target_dir = target_dir.resolve()

    with zipfile.ZipFile(zip_path) as zip_ref:
        members = [info for info in zip_ref.infolist() if not info.is_dir()]

        already_extracted = (
            skip_existing
            and members
            and all(
                (dest := target_dir / info.filename).is_file()
                and dest.stat().st_size == info.file_size
                for info in members
            )
        )
        if already_extracted:
            return False

        for info in members:
            dest = (target_dir / info.filename).resolve()
            if not dest.is_relative_to(target_dir):
                raise ValueError(f"unsafe member path: {info.filename}")

        zip_ref.extractall(target_dir)
        return True


def main():
    config = get_config()
    my_dirs = get_dir_names(config)
    unzip_cfg = config.get("unzip", {})

    skip_existing = bool(unzip_cfg.get("skip_existing", True))
    remove_zip_after_extract = bool(unzip_cfg.get("remove_zip_after_extract", False))

    processing_dir = Path(config["dirs"]["processing_dir"]).expanduser()
    dir_zip = processing_dir / my_dirs[0]
    dir_unzip = processing_dir / my_dirs[1]

    dir_unzip.mkdir(parents=True, exist_ok=True)

    zip_files = sorted(dir_zip.glob("*.zip"))
    if not zip_files:
        logging.warning("No .zip files found in %s", dir_zip)
        return 0

    logging.info("Found %d zip file(s) in %s", len(zip_files), dir_zip)

    n_extracted = 0
    n_skipped = 0
    failures = []
    start_time = time.perf_counter()

    for idx, zip_path in enumerate(zip_files, start=1):
        logging.info("[%d/%d] Processing %s", idx, len(zip_files), zip_path.name)
        try:
            if unzip_if_needed(zip_path, dir_unzip, skip_existing):
                n_extracted += 1
                if remove_zip_after_extract:
                    zip_path.unlink()
                    logging.info("Removed archive: %s", zip_path.name)
            else:
                logging.info("Already extracted, skipping: %s", zip_path.name)
                n_skipped += 1
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            logging.error("Failed to extract %s: %s", zip_path.name, exc)
            failures.append(zip_path.name)

    elapsed = time.perf_counter() - start_time
    logging.info(
        "Done. %d extracted, %d skipped, %d failed in %.1f s",
        n_extracted,
        n_skipped,
        len(failures),
        elapsed,
    )
    if failures:
        logging.error("Failed files: %s", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
