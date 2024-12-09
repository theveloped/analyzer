import os
import time
from loguru import logger

def has_valid_extension(path: str, extensions: list[str] = []):
    if not any(path.endswith(extension) for extension in extensions):
        raise(f"The file {path} does not have a valid extension! Allowed extensions: {extensions}")
    
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