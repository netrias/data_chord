#!/usr/bin/env python3
"""
Lightweight mockup server for quick prototyping with HTMX and Alpine.js
Run with: python mockup_server.py
"""
import json
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import urllib.parse
import os

class MockupHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(Path(__file__).parent / "mockups"), **kwargs)

    def do_POST(self):
        """Handle POST requests for HTMX interactions"""
        if self.path == '/api/echo':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)

            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()

            # Parse form data
            form_data = urllib.parse.parse_qs(post_data.decode('utf-8'))
            message = form_data.get('message', [''])[0]

            response = f'<div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded">Echo: {message}</div>'
            self.wfile.write(response.encode())

        elif self.path == '/api/toggle':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()

            response = '''
            <div class="bg-blue-100 border border-blue-400 text-blue-700 px-4 py-3 rounded">
                <p>Content toggled!</p>
                <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded"
                        hx-post="/api/toggle" hx-target="#dynamic-content">
                    Toggle Again
                </button>
            </div>
            '''
            self.wfile.write(response.encode())
        else:
            self.send_error(404)

def run_server(port=8080):
    """Run the mockup server"""
    # Create mockups directory if it doesn't exist
    mockups_dir = Path(__file__).parent / "mockups"
    mockups_dir.mkdir(exist_ok=True)

    server = HTTPServer(('localhost', port), MockupHandler)
    print(f"Mockup server running at http://localhost:{port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run_server(port)