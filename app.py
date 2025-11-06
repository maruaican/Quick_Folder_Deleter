# coding: utf-8
from flask import Flask, render_template_string, request, Response, stream_with_context
import os, shutil, stat, datetime, logging, time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quick_Folder_Deleter</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@1/css/pico.min.css">
<style>
  body{background:#f5f5f5;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;padding:1rem}
  main.container{width:100%;max-width:1100px;padding:1.5rem}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;align-items:stretch}
  article{padding:1rem;border-radius:8px;background:var(--pico-card-background-color);box-shadow:var(--pico-card-box-shadow);display:flex;flex-direction:column;min-height:420px}
  .caution-box{padding:1rem;border-left:4px solid var(--pico-primary-border);background:var(--pico-form-element-background-color)}
  form{margin-top:1rem;display:flex;flex-direction:column;gap:.6rem}
  input[type="text"]{width:100%;box-sizing:border-box}
  #submit-btn{align-self:start}
  #log-output{flex:1 1 auto;background:#000;color:#eee;padding:1rem;white-space:pre-wrap;word-wrap:break-word;font-family:Courier,monospace;overflow:auto;border-radius:6px;min-height:300px}
  .progress-wrap{margin-top:0.8rem;display:flex;align-items:center;gap:.6rem}
  progress{width:100%;height:1rem}
  @media(max-width:900px){.grid{grid-template-columns:1fr};article{min-height:360px}}
</style>
</head>
<body>
<main class="container">
  <header style="text-align:center;margin-bottom:1rem">
    <h1>Quick_Folder_Deleter</h1>
    <p>指定されたフォルダを、中身ごと強制的に削除します。</p>
  </header>

  <div class="grid">
    <article>
      <div class="caution-box">
        <strong>【重要】</strong>
        この操作は復元不可能です。対象フォルダと中身を必ず確認してください。
      </div>

      <form id="delete-form">
        <label for="folder_path">削除するフォルダのフルパスを入力してください</label>
        <input type="text" id="folder_path" name="folder_path" placeholder="例: F:\\01 総務班\\雑件\\その他" required>
        <button type="submit" id="submit-btn">削除実行</button>
      </form>

      <div class="progress-wrap" style="margin-top:0.8rem;">
        <progress id="progress-bar" value="0" max="100"></progress>
        <div id="progress-text" style="min-width:90px;text-align:right;font-weight:600">0%</div>
      </div>
    </article>

    <article id="log-container">
      <h2 style="margin:0 0 0.6rem 0">実行ログ</h2>
      <pre id="log-output"></pre>
    </article>
  </div>
</main>

<script>
(function(){
  const form = document.getElementById('delete-form');
  const folderInput = document.getElementById('folder_path');
  const logOutput = document.getElementById('log-output');
  const submitBtn = document.getElementById('submit-btn');
  const progressBar = document.getElementById('progress-bar');
  const progressText = document.getElementById('progress-text');

  function appendLog(line){
    logOutput.textContent += line + "\\n";
    logOutput.scrollTop = logOutput.scrollHeight;
  }

  function resetUI(){
    progressBar.value = 0;
    progressText.textContent = '0%';
    logOutput.textContent = '';
  }

  form.addEventListener('submit', function(e){
    e.preventDefault();
    const folder = folderInput.value.trim();
    if(!folder) return;
    // close existing ES if any
    if(window.deleteES){
      try{ window.deleteES.close(); } catch(e){}
      window.deleteES = null;
    }
    resetUI();
    appendLog("[INFO] 削除リクエスト送信: " + folder);
    submitBtn.disabled = true;
    submitBtn.setAttribute('aria-busy','true');

    const url = '/stream?folder_path=' + encodeURIComponent(folder);
    const es = new EventSource(url);
    window.deleteES = es;

    es.onmessage = function(evt){
      // evt.data is expected to be JSON string with { type, message, progress }
      try {
        const d = JSON.parse(evt.data);
        if(d.message) appendLog(d.message);
        if(typeof d.progress === 'number'){
          const percent = Math.max(0, Math.min(100, Math.round(d.progress)));
          progressBar.value = percent;
          progressText.textContent = percent + '%';
        }
        // end indicator
        if(d.type === 'end' || d.type === 'error') {
          es.close();
          submitBtn.disabled = false;
          submitBtn.removeAttribute('aria-busy');
        }
      } catch (err) {
        // fallback: raw text
        appendLog(evt.data);
      }
    };

    es.onerror = function(err){
      appendLog("[FATAL] SSE 接続エラーまたは切断");
      try{ es.close(); } catch(e){}
      submitBtn.disabled = false;
      submitBtn.removeAttribute('aria-busy');
    };
  });
})();
</script>

</body>
</html>
"""

def on_rm_error(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as ex:
        logging.warning(f"on_rm_error: retry failed: {path} => {ex}")

def sse(data_dict):
    # SSE data must be lines prefixed by "data: "
    # we send JSON string in data field
    import json
    return f"data: {json.dumps(data_dict, ensure_ascii=False)}\n\n"

def count_items(folder_path):
    total = 0
    for root, dirs, files in os.walk(folder_path):
        total += len(files) + len(dirs)
    return total

def delete_generator(folder_path):
    # Send start
    yield sse({'type':'info', 'message':f'[INFO] 削除処理開始: {folder_path}', 'progress':0})

    # Count items for progress calculation
    try:
        total = count_items(folder_path)
    except Exception as e:
        yield sse({'type':'error', 'message':f'[ERROR] 対象の走査に失敗しました: {e}', 'progress':0})
        yield sse({'type':'end', 'message':'[END] 処理終了', 'progress':0})
        return

    if total == 0:
        yield sse({'type':'info', 'message':'[INFO] 対象フォルダにファイル/サブフォルダはありません。', 'progress':100})
    else:
        yield sse({'type':'info', 'message':f'[INFO] 削除対象アイテム数: {total}', 'progress':0})

    processed = 0

    # Walk bottom-up to try to remove files/dirs progressively
    for root, dirs, files in os.walk(folder_path, topdown=False):
        # delete files first
        for name in files:
            p = os.path.join(root, name)
            try:
                if os.path.islink(p) or os.path.isfile(p):
                    os.remove(p)
                    processed += 1
                    pct = (processed / total) * 100 if total else 100
                    yield sse({'type':'del', 'message':f'[DEL FILE] {p}', 'progress':pct})
                else:
                    # unknown file-like
                    os.remove(p)
                    processed += 1
                    pct = (processed / total) * 100 if total else 100
                    yield sse({'type':'del', 'message':f'[DEL FILE?] {p}', 'progress':pct})
            except Exception as e:
                processed += 1
                pct = (processed / total) * 100 if total else 100
                yield sse({'type':'error', 'message':f'[ERROR] ファイル削除失敗: {p} => {e}', 'progress':pct})

            time.sleep(0.005)  # throttle to allow SSE flush

        # then delete dirs
        for name in dirs:
            p = os.path.join(root, name)
            try:
                os.rmdir(p)
                processed += 1
                pct = (processed / total) * 100 if total else 100
                yield sse({'type':'del', 'message':f'[DEL DIR] {p}', 'progress':pct})
            except OSError:
                # non-empty or locked: will be handled by final rmtree
                processed += 1
                pct = (processed / total) * 100 if total else 100
                yield sse({'type':'skip', 'message':f'[SKIP DIR] 後処理へ: {p}', 'progress':pct})
            except Exception as e:
                processed += 1
                pct = (processed / total) * 100 if total else 100
                yield sse({'type':'error', 'message':f'[ERROR] ディレクトリ削除失敗: {p} => {e}', 'progress':pct})
            time.sleep(0.005)

    # Final rmtree to ensure removal of any leftovers
    try:
        shutil.rmtree(folder_path, onerror=on_rm_error)
    except Exception as e:
        yield sse({'type':'error', 'message':f'[ERROR] 最終 rmtree で例外: {e}', 'progress':(processed/total)*100 if total else 100})

    if not os.path.exists(folder_path):
        yield sse({'type':'success', 'message':f'[SUCCESS] ディレクトリを完全に削除しました: {folder_path}', 'progress':100})
        yield sse({'type':'end', 'message':'[END] 処理完了', 'progress':100})
    else:
        yield sse({'type':'error', 'message':f'[ERROR] 削除後も存在します: {folder_path}', 'progress':(processed/total)*100 if total else 100})
        yield sse({'type':'end', 'message':'[END] 処理完了（未完全）', 'progress':(processed/total)*100 if total else 100})

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/stream')
def stream():
    folder_path = request.args.get('folder_path', '').strip()
    # Basic validation
    if not folder_path:
        return Response(sse({'type':'error','message':'[ERROR] folder_path が指定されていません','progress':0}), mimetype='text/event-stream')
    if not os.path.isabs(folder_path):
        return Response(sse({'type':'error','message':f'[ERROR] 絶対パスを指定してください: {folder_path}','progress':0}), mimetype='text/event-stream')
    if not os.path.exists(folder_path):
        return Response(sse({'type':'error','message':f'[ERROR] 指定されたパスは存在しません: {folder_path}','progress':0}), mimetype='text/event-stream')
    if not os.path.isdir(folder_path):
        return Response(sse({'type':'error','message':f'[ERROR] 指定されたパスはディレクトリではありません: {folder_path}','progress':0}), mimetype='text/event-stream')

    # Return generator wrapped in stream_with_context for SSE streaming
    return Response(stream_with_context(delete_generator(folder_path)), mimetype='text/event-stream')

if __name__ == '__main__':
    # For local testing only. In production use a proper WSGI server.
    app.run(debug=True, host='0.0.0.0', port=5001)

