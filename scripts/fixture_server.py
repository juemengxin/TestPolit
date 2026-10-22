"""Local integration fixture, never a real LLM. Explicit --fixture-tests target."""
import json
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,data,status=200,content_type='application/json'):
        raw=json.dumps(data).encode() if not isinstance(data,bytes) else data
        self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        if self.path.startswith('/health'):self.send({'status':'ok','service':'testpilot-fixture'})
        else:self.send(b'<!doctype html><title>TestPilot Fixture</title><h1>TestPilot Fixture</h1><label>Name<input id="name"></label><button id="submit" onclick="document.querySelector(\'#result\').textContent=\'Hello \'+document.querySelector(\'#name\').value">Submit</button><p id="result"></p>',content_type='text/html')
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or '{}')
        if self.path=='/mcp':
            method=data.get('method')
            if method=='initialize':result={'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
            elif method=='tools/list':result={'tools':[{'name':'echo','description':'Local fixture echo','inputSchema':{'type':'object','properties':{'message':{'type':'string'}}}}]}
            elif method=='tools/call':result={'content':[{'type':'text','text':str(data['params'].get('arguments',{}))}]}
            else:self.send({},202);return
            self.send({'jsonrpc':'2.0','id':data.get('id'),'result':result})
        elif self.path.endswith('/embeddings'):self.send({'data':[{'index':i,'embedding':[1.0,0.0,0.0]} for i,_ in enumerate(data['input'])]})
        elif self.path.endswith('/rerank'):self.send({'results':[{'index':i,'relevance_score':1/(i+1)} for i,_ in enumerate(data['documents'])]})
        elif self.path.endswith('/chat/completions'):
            prompt=data['messages'][-1]['content']
            output={'cases':[{'name':'Fixture generated case','priority':'P1','preconditions':'Local fixture only','steps':[{'action':'Open fixture','expected':'Fixture is visible'}]}]}
            if 'feedback' in prompt:output={'feedback':'Fixture structural validation only'}
            if '回复连接成功' in prompt:output={'answer':'fixture connected, not a real model'}
            self.send({'id':'fixture','object':'chat.completion','model':'fixture-only','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(output)}}],'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}})
        else:self.send({'echo':data})
if __name__=='__main__':
    print('Local fixture on http://127.0.0.1:8766 (NOT a real model)',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
