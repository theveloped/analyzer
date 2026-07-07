import http.server
import socketserver
import webbrowser
import threading
import os
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler
from loguru import logger

class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.index_path = kwargs.pop("index_path", "/index.html")
        super().__init__(*args, **kwargs)

    def translate_path(self, path):
        """Translate URL paths starting with the renamed path to physical directory paths under base path."""
        original_path = super().translate_path(path)
           
        if path == "/":
            logger.debug(path)
            return self.index_path
        
        return original_path

def serve(index_path, directory, port=8080, timeout=2.0):
    # Setup server configuration  
    handler = partial(CustomHTTPRequestHandler, index_path=index_path, directory=directory)
    with socketserver.TCPServer(("", port), handler) as httpd:
        # Open index.html in the default browser
        webbrowser.get('windows-default').open(f'http://localhost:{port}/')
        
        # Set a timer to stop the server after 2 seconds
        stop_timer = threading.Timer(timeout, httpd.shutdown)
        stop_timer.start()

        # Serve until shutdown
        httpd.serve_forever()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start a simple HTTP server with custom path handling.")
    parser.add_argument('--index', type=str, default='/index.html', help='Web path to index.html')
    parser.add_argument('--base', type=str, default='test', help='Physical base directory path')
    parser.add_argument('--renamed', type=str, default='/renamed/', help='Web path for renamed directory')
    args = parser.parse_args()

    serve(args.index, args.base, args.renamed)
