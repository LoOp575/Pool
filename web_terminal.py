import argparse,json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse
from scanner import scan,review_pair
STATIC=Path(__file__).resolve().parent/"static"
class Handler(BaseHTTPRequestHandler):
 def send_json(self,status,payload):
  b=json.dumps(payload).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  path=self.path.split("?",1)[0]
  if path in ("/","/index.html"):
   b=(STATIC/"index.html").read_bytes();self.send_response(200);self.send_header("Content-Type","text/html");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
  elif path=="/api/scan":
   try:
    q=parse_qs(urlparse(self.path).query);limit=min(max(int(q.get("limit",["40"])[0]),1),100);met=q.get("meteora",["0"])[0]=="1";self.send_json(200,scan(limit,met))
   except Exception as e:self.send_json(502,{"error":str(e)})
  elif path=="/api/review":
   try:
    q=parse_qs(urlparse(self.path).query);pair=q.get("pair",[""])[0]
    if not pair:raise ValueError("pair wajib diisi")
    horizon=min(max(int(q.get("horizon",["96"])[0]),1),192);paths=min(max(int(q.get("paths",["2000"])[0]),200),5000)
    self.send_json(200,review_pair(pair,horizon_bars=horizon,mc_paths=paths))
   except Exception as e:self.send_json(502,{"error":str(e)})
  elif path=="/healthz":self.send_json(200,{"ok":True})
  else:self.send_json(404,{"error":"not found"})
 def log_message(self,fmt,*args):print(fmt%args)
def main():
 p=argparse.ArgumentParser();p.add_argument("--host",default="127.0.0.1");p.add_argument("--port",type=int,default=8000);a=p.parse_args()
 s=ThreadingHTTPServer((a.host,a.port),Handler);print(f"http://{a.host}:{a.port}");s.serve_forever()
if __name__=="__main__":main()
