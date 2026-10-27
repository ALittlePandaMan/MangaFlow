"""Chunked ZIP writes: cancel promptly and do not recompress compressed images."""
import zipfile


class ExportArchive(zipfile.ZipFile):
    def __init__(self, path, *, check=lambda: None, on_bytes=lambda value: None):
        super().__init__(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1)
        self.check = check
        self.on_bytes = on_bytes

    def write(self, filename, arcname=None, compress_type=None, compresslevel=None):
        info = zipfile.ZipInfo.from_file(filename, arcname)
        # These payloads already have entropy coding. Deflating them again costs
        # CPU for negligible savings and delays the browser's first download.
        compressed = str(filename).lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".zip", ".cbz"))
        info.compress_type = compress_type if compress_type is not None else (
            zipfile.ZIP_STORED if compressed else self.compression)
        info._compresslevel = compresslevel if compresslevel is not None else self.compresslevel
        self.check()
        with open(filename, "rb") as source, self.open(info, "w", force_zip64=info.file_size >= zipfile.ZIP64_LIMIT) as target:
            while block := source.read(1024**2):
                self.check()
                target.write(block)
                self.on_bytes(len(block))
