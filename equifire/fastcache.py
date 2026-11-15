"""Consolidated parquet caches that make program startup fast.

Each cache file carries a JSON entry (key b"equifire") in its parquet schema metadata with the
fingerprint of the inputs it was built from. A reader compares that fingerprint to the current
inputs and rebuilds when they differ. Files are written to a temp name and then os.replace()d, so
concurrent readers see either the old file or the new one, never a partial file.
"""
import hashlib
import json
import os
import weakref

import pyarrow as pa
import pyarrow.parquet as pq

META_KEY = b"equifire"


def files_fingerprint(paths, tag=""):
    """Cheap fingerprint of a set of files (names, sizes, mtimes); no file contents are read."""
    h = hashlib.sha1(tag.encode())
    for p in sorted(paths):
        st = p.stat()
        h.update(f"{p.name}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def read_meta(path):
    """The equifire metadata dict stored in `path`, or None if missing or unreadable."""
    try:
        md = pq.read_schema(path).metadata or {}
        return json.loads(md[META_KEY])
    except (OSError, KeyError, ValueError, pa.ArrowException):
        return None


def read_frame(path, columns=None):
    """Read a cache file into pandas. Columns are converted one block each and the arrow buffers
    freed as they go, so peak memory stays near the size of the final frame."""
    t = pq.read_table(path, columns=columns, use_threads=True)
    return t.to_pandas(split_blocks=True, self_destruct=True)


def write_frame(df, path, meta):
    """Atomically write `df` (index dropped) to `path` with `meta` stored in the schema metadata.
    Failures (read-only disk, full disk) only print a warning: the cache is an optimisation."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        table = pa.Table.from_pandas(df, preserve_index=False)
        md = dict(table.schema.metadata or {})
        md[META_KEY] = json.dumps(meta).encode()
        table = table.replace_schema_metadata(md)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, tmp, compression="lz4", row_group_size=1 << 20)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001 - never fail the caller because of the cache
        print(f"[cache] could not write {path.name}: {e}")
        try:
            tmp.unlink()
        except OSError:
            pass


# Frames returned by a full load, so derived caches can recognise them: id -> (weakref, fp, rows).
_registry = {}


def register(df, fingerprint):
    for k in [k for k, (r, _, _) in _registry.items() if r() is None]:
        del _registry[k]
    _registry[id(df)] = (weakref.ref(df), fingerprint, len(df))


def fingerprint_of(df):
    """Fingerprint of `df` if it is (still) an unmodified-length full table from register()."""
    entry = _registry.get(id(df))
    if entry is None:
        return None
    ref, fp, rows = entry
    if ref() is not df or len(df) != rows:
        return None
    return fp
