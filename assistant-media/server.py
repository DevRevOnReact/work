"""Private, serial decoder. Request bytes never become filenames or command arguments."""
import json
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

LIMIT = 4 * 1024 * 1024


def decode(mode, data, folder):
    if mode == "audio":
        wav = folder / "voice.wav"
        subprocess.run([
            "ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "pipe",
            "-format_whitelist", "ogg,matroska,webm,mov,mp3,wav,aac", "-threads", "1",
            "-i", "pipe:0", "-vn", "-t", "60", "-ar", "16000", "-ac", "1", str(wav)
        ], input=data, check=True, timeout=15, stderr=subprocess.DEVNULL)
        prefix = folder / "transcript"
        subprocess.run(["whisper-cli", "-m", "/opt/whisper-small.bin", "-f", str(wav),
                        "-l", "auto", "-t", "2", "-nt", "-otxt", "-of", str(prefix)],
                       check=True, timeout=100, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return prefix.with_suffix(".txt").read_text(encoding="utf8").strip()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        decoder = "png_pipe"
    elif data.startswith(b"\xff\xd8\xff"):
        decoder = "jpeg_pipe"
    else:
        raise ValueError()
    image = folder / "screen.png"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "pipe",
                    "-f", decoder, "-threads", "1", "-max_pixels", "12000000", "-i", "pipe:0",
                    "-frames:v", "1", "-vf", "scale='min(1800,iw)':-2", str(image)],
                   input=data, check=True, timeout=12, stderr=subprocess.DEVNULL)
    return subprocess.run(["tesseract", str(image), "stdout", "-l", "rus+eng"],
                          capture_output=True, check=True, timeout=25).stdout.decode().strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        self.connection.settimeout(15)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if self.path not in ("/audio", "/image") or not 0 < size <= LIMIT:
                return self.send_error(400)
            with tempfile.TemporaryDirectory() as tmp:
                text = decode(self.path[1:], self.rfile.read(size), Path(tmp))
            if not text:
                raise ValueError()
            body = json.dumps({"text": text[:4000]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, OSError, subprocess.SubprocessError):
            self.send_error(422)


HTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
