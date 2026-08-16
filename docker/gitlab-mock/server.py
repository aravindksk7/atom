from http.server import HTTPServer, BaseHTTPRequestHandler
import json

class GitLabMockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if "/retry" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "running", "id": 101}).encode())
        else:
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": 1, "status": "created"}).encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "success", "id": 1}).encode())

def run():
    server = HTTPServer(("0.0.0.0", 18080), GitLabMockHandler)
    server.serve_forever()

if __name__ == "__main__":
    run()
