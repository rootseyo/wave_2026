#!/usr/bin/env python3
"""Local string utility. Run: python3 string_tool.py [--port 8080]."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>String Studio · 문자열 변환</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif;background:#101216;color:#e8edf3}*{box-sizing:border-box}body{margin:0}main{max-width:1120px;margin:auto;padding:56px 28px}header{display:flex;justify-content:space-between;align-items:center;margin-bottom:32px}.eyebrow{color:#87dab3;font-size:12px;letter-spacing:2px;font-weight:700}h1{font-size:36px;letter-spacing:-1.5px;margin:10px 0}p{color:#9aa7b7;line-height:1.7;margin:0}.badge{border:1px solid #34483f;border-radius:99px;padding:8px 12px;font-size:12px;color:#9de3bd;white-space:nowrap}.settings{border:1px solid #303741;border-radius:14px;background:#191d24;padding:20px;margin-bottom:20px;display:grid;grid-template-columns:1fr 1.5fr;gap:20px}label,.panel-title{font-size:13px;font-weight:650;color:#c8d1dc}select,input,textarea,button{font:inherit}select,input{width:100%;padding:12px;border:1px solid #414b59;border-radius:8px;background:#12161c;color:#eef3fa;margin-top:9px}input:disabled{opacity:.45}.password{display:flex;gap:8px;align-items:center}.password button{margin-top:9px;padding:12px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.panel{border:1px solid #303741;border-radius:14px;overflow:hidden;background:#191d24}.bar{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;border-bottom:1px solid #303741;min-height:58px}.count{color:#8d99a9;font-size:12px}textarea{display:block;resize:vertical;min-height:300px;width:100%;border:0;background:#151920;color:#e9eff8;line-height:1.7;padding:20px;font-family:ui-monospace,monospace;font-size:14px}textarea:focus{outline:2px solid #70d8a9;outline-offset:-2px}button{border:1px solid #414b59;border-radius:8px;background:#242c36;color:#e7eef7;padding:10px 16px;cursor:pointer;font-size:13px;font-weight:600}button:hover{background:#34404d}button:disabled{opacity:.5;cursor:wait}.primary{background:#9ce5be;color:#10261a;border-color:#9ce5be}.primary:hover{background:#b8f5d4}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}.spacer{flex:1}.small{padding:6px 10px;font-size:12px}.hint{font-size:12px;margin-top:9px;min-height:20px}#status{margin-top:22px;min-height:26px;color:#9ce5be;font-size:14px}#status.error{color:#ffaaa6}footer{border-top:1px solid #303741;margin-top:30px;padding-top:20px;font-size:12px;color:#8996a6;line-height:1.8}:focus-visible{outline:2px solid #9ce5be;outline-offset:3px}@media(max-width:700px){main{padding:28px 16px}.grid,.settings{grid-template-columns:1fr}header{align-items:flex-start;gap:12px}h1{font-size:28px}.badge{font-size:10px}textarea{min-height:220px}.spacer{display:none}}
</style></head><body><main>
<header><div><div class="eyebrow">LOCAL STRING UTILITY</div><h1>String Studio</h1><p>문자열을 변환하고, 필요할 때 다시 복원하세요.</p></div><span class="badge">● 브라우저에서만 처리</span></header>
<section class="settings" aria-label="변환 설정"><div><label for="mode">변환 방식</label><select id="mode"><option value="aes">비밀번호 암호화 · AES-GCM</option><option value="base64">간단한 난독화 · Base64</option></select><p class="hint" id="modeHint"></p></div><div><label for="password">비밀번호</label><div class="password"><input id="password" type="password" autocomplete="off" placeholder="암호화와 복호화에 같은 비밀번호를 사용하세요"><button id="show" type="button" aria-label="비밀번호 표시" aria-pressed="false">보기</button></div><p class="hint">비밀번호를 잊으면 암호화한 내용을 복원할 수 없습니다.</p></div></section>
<div class="grid"><section class="panel"><div class="bar"><label for="source">01 / 입력</label><span class="count" id="sourceCount">0자</span></div><textarea id="source" spellcheck="false" placeholder="변환할 문자열 또는 복원할 결과를 붙여넣으세요."></textarea></section><section class="panel"><div class="bar"><label for="result">02 / 결과</label><button id="copy" class="small" type="button">결과 복사</button></div><textarea id="result" spellcheck="false" readonly placeholder="변환 결과가 여기에 표시됩니다."></textarea></section></div>
<div class="actions"><button class="primary" id="encode" type="button">암호화</button><button id="decode" type="button">복호화</button><button id="reuse" type="button">결과를 입력으로 ↑</button><span class="spacer"></span><button id="clear" type="button">모두 지우기</button></div>
<div id="status" role="status" aria-live="polite"></div>
<footer>입력값과 비밀번호는 서버로 전송하거나 저장하지 않습니다. Base64는 비밀번호 없이 누구나 복원 가능한 인코딩입니다.<br>AES-GCM은 브라우저 Web Crypto를 사용합니다. 암호문에는 salt와 nonce가 포함되어 같은 입력도 매번 다른 결과가 나옵니다.</footer>
</main><script>
'use strict';
const $=id=>document.getElementById(id), encoder=new TextEncoder(), decoder=new TextDecoder('utf-8',{fatal:true});
const rounds=250000;
function status(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error)}
function to64(bytes){let s='';for(let i=0;i<bytes.length;i+=8192)s+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(s)}
function from64(value){if(!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value))throw Error('유효한 Base64 문자열이 아닙니다.');const s=atob(value);return Uint8Array.from(s,c=>c.charCodeAt(0))}
async function key(password,salt){const material=await crypto.subtle.importKey('raw',encoder.encode(password),'PBKDF2',false,['deriveKey']);return crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt,iterations:rounds},material,{name:'AES-GCM',length:256},false,['encrypt','decrypt'])}
function count(){$('sourceCount').textContent=Array.from($('source').value).length.toLocaleString()+'자'}
function mode(){const aes=$('mode').value==='aes';$('password').disabled=!aes;$('show').disabled=!aes;$('encode').textContent=aes?'암호화':'난독화 (인코딩)';$('decode').textContent=aes?'복호화':'복원 (디코딩)';$('modeHint').textContent=aes?'비밀번호로 보호하는 실제 암호화입니다.':'보안 기능이 없는 간단한 문자열 변환입니다.';status('')}
async function transform(encrypt){
 const input=$('source').value,aes=$('mode').value==='aes',password=$('password').value;
 if(!input){status('입력할 문자열을 먼저 작성하세요.',true);return}
 if(input.length>2000000){status('입력은 2백만 자 이하로 작성하세요.',true);return}
 if(aes&&!password){status('비밀번호를 입력하세요.',true);$('password').focus();return}
 const controls=Array.from(document.querySelectorAll('button,select,input,textarea'));const previous=controls.map(el=>el.disabled);controls.forEach(el=>el.disabled=true);$('result').value='';status('처리 중…');
 try{
  let output;
  if(!aes)output=encrypt?to64(encoder.encode(input)):decoder.decode(from64(input.trim()));
  else{
   if(!globalThis.crypto?.subtle)throw Error('이 브라우저에서는 암호화 기능을 사용할 수 없습니다. http://localhost 주소로 접속하세요.');
   if(encrypt){const salt=crypto.getRandomValues(new Uint8Array(16)),iv=crypto.getRandomValues(new Uint8Array(12));const cipher=await crypto.subtle.encrypt({name:'AES-GCM',iv},await key(password,salt),encoder.encode(input));output=['ss1',to64(salt),to64(iv),to64(new Uint8Array(cipher))].join('.')}
   else{const parts=input.trim().split('.');if(parts.length!==4||parts[0]!=='ss1')throw Error('String Studio 암호문(ss1.…)을 입력하세요.');const salt=from64(parts[1]),iv=from64(parts[2]),cipher=from64(parts[3]);if(salt.length!==16||iv.length!==12||cipher.length<16)throw Error('암호문 형식이 올바르지 않습니다.');try{const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv},await key(password,salt),cipher);output=decoder.decode(plain)}catch{throw Error('비밀번호가 다르거나 암호문이 손상되었습니다.')}}
  }
  $('result').value=output;status(encrypt?'변환 완료. 결과를 복사해 사용하세요.':'복원 완료.');
 }catch(error){status(error instanceof TypeError?'올바른 UTF-8 문자열로 복원할 수 없습니다.':error.message,true)}finally{controls.forEach((el,i)=>el.disabled=previous[i])}
}
$('mode').addEventListener('change',mode);$('source').addEventListener('input',count);$('encode').onclick=()=>transform(true);$('decode').onclick=()=>transform(false);
$('show').onclick=()=>{const visible=$('password').type==='password';$('password').type=visible?'text':'password';$('show').textContent=visible?'숨기기':'보기';$('show').setAttribute('aria-pressed',String(visible))};
$('copy').onclick=async()=>{if(!$('result').value){status('복사할 결과가 없습니다.',true);return}try{await navigator.clipboard.writeText($('result').value);status('결과를 복사했습니다.')}catch{$('result').focus();$('result').select();status('자동 복사가 불가능합니다. 선택된 결과를 Ctrl+C 또는 ⌘C로 복사하세요.')}};
$('reuse').onclick=()=>{if(!$('result').value){status('입력으로 가져올 결과가 없습니다.',true);return}$('source').value=$('result').value;$('result').value='';count();status('결과를 입력으로 가져왔습니다.')};
$('clear').onclick=()=>{$('source').value='';$('result').value='';$('password').value='';$('password').type='password';$('show').textContent='보기';$('show').setAttribute('aria-pressed','false');count();status('모두 지웠습니다.');$('source').focus()};mode();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/', '/index.html'):
            self.send_error(404)
            return
        body = PAGE.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description='Local string obfuscation and encryption utility')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError as error:
        parser.exit(1, f'서버를 시작할 수 없습니다: {error}\n')
    print(f'String Studio: http://localhost:{args.port} (종료: Ctrl+C)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n서버를 종료합니다.')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
