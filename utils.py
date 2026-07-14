import hashlib
import os
import time
from loguru import logger


def file_fingerprint(path, length=12):
    """Short content hash of a file, or None if it does not exist.

    Used to detect silent desyncs between artifacts keyed by direction
    index (zcache fields, setup/mold results) and a regenerated
    directions.npy.
    """
    if not os.path.exists(path):
        return None
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:length]


def files_fingerprint(paths, length=12):
    """Short combined content hash of several files, in order; None if any
    is missing. Same role as file_fingerprint over a set of artifacts."""
    digest = hashlib.sha1()
    for path in paths:
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()[:length]


def has_valid_extension(path: str, extensions: list[str] = []):
    if not any(path.lower().endswith(extension) for extension in extensions):
        raise ValueError(f"The file {path} does not have a valid extension! Allowed extensions: {extensions}")
    
    return

def ensure_directory(directory):
    """Ensure that the specified directory exists."""
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"Created directory: {directory}")
    else:
        print(f"Directory already exists: {directory}")

def ensure_parent_directories(filepath):
    """Ensure that the parent directories of the specified file path exist."""
    directory = os.path.dirname(filepath)
    ensure_directory(directory)
    

def log_execution_time(func):
    """Decorator that logs the execution time of the function using Loguru."""
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        logger.debug(f"Function {func.__name__} executed in {end_time - start_time:.4f} seconds")
        return result
    return wrapper