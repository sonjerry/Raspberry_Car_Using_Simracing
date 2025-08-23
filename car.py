import asyncio, json, time, atexit
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# === 하드웨어 의존 패키지 ===
import busio
from board import SCL, SDA
from adafruit_pca9685 import PCA9685

# =========================
# 사용자 설정
# =========================
CHANNEL = 0                 # PCA9685 채널 (서보 조향 채널)
FREQUENCY_HZ = 50           # 서보 PWM 주파수
LEFT_US   = 600             # 좌측 한계(마이크로초, 필요시 조정)
RIGHT_US  = 2400            # 우측 한계(마이크로초, 필요시 조정)
CENTER_US = 1800            # 실제 센터(사용자 제공 기준)
NET_SPEED_US_PER_S = 1000.0 # 키 유지시 램프 속도(µs/s)
TICK_S = 0.005              # 제어 루프 주기(5ms)
HOLD_GRACE_S = 0.07         # 키 유지 판정 유예(최근 신호 허용시간)

WebRTC/스트림 주소, tailscale
VIDEO_IFRAME_SRC = "http://100.84.162.124:8889/cam"

# =========================
# 하드웨어 초기화
# =========================
i2c = busio.I2C(SCL, SDA)
pca = PCA9685(i2c)
pca.frequency = FREQUENCY_HZ

FULL = 65535
PERIOD_MS = 20.0

def us_to_duty(us: int) -> int:
    ms = us / 1000.0
    duty = int((ms / PERIOD_MS) * FULL)
    return max(0, min(FULL, duty))

# 상태 변수
cur_us = float(CENTER_US)
target_us = float(CENTER_US)
pca.channels[CHANNEL].duty_cycle = us_to_duty(int(cur_us))

# 최근 입력 시각(키 유지 판단용)
last_a = -1.0
last_d = -1.0

# 현재 컨트롤러 소켓(가장 최근 연결 1개만 제어권)
controller: WebSocket | None = None

# =========================
# HTML (단일 파일)
# =========================
HTML = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>RC Stream & Steering</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Noto Sans, Helvetica, Arial, Apple Color Emoji, Segoe UI Emoji; background:#0b1020; color:#e7ecff; margin:0; }}
    .wrap {{ max-width: 1100px; margin: 24px auto; padding: 16px; }}
    .card {{ background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1); border-radius:16px; padding:16px; box-shadow:0 10px 30px rgba(0,0,0,0.25); }}
    h1 {{ margin:0 0 12px; font-size:20px; }}
    iframe {{ width:100%; aspect-ratio:16/9; border:0; border-radius:12px; }}
    .row {{ display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-top:12px; }}
    button {{ padding:12px 16px; border-radius:12px; border:1px solid rgba(255,255,255,0.15); background:rgba(255,255,255,0.08); color:#e7ecff; cursor:pointer; font-size:15px; }}
    button:active {{ transform: translateY(1px); }}
    .pill {{ padding:6px 10px; font-size:12px; border-radius:999px; border:1px solid rgba(255,255,255,0.2); background:rgba(255,255,255,0.06); }}
    .kbd {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background:#1c2343; border:1px solid #334; padding:2px 6px; border-radius:6px; }}
    .status {{ font-size:13px; opacity:0.9; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>실시간 스트리밍 + 조향 제어</h1>
      <iframe src="{VIDEO_IFRAME_SRC}" allow="autoplay; fullscreen; picture-in-picture"></iframe>

      <div class="row">
        <span class="pill">키보드: <span class="kbd">A</span> 왼쪽 / <span class="kbd">D</span> 오른쪽 / <span class="kbd">S</span> 센터 / <span class="kbd">Space</span> 정지</span>
        <span id="wsStat" class="status">WS: connecting…</span>
      </div>

      <div class="row">
        <button id="btnL">◀ 왼쪽</button>
        <button id="btnC">● 센터</button>
        <button id="btnR">오른쪽 ▶</button>
        <button id="btnStop">■ 정지</button>
      </div>
    </div>
  </div>

<script>
(() => {{
  const wsUrl = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
  let ws;
  let pressed = {{a:false, d:false}};
  let connected = false;

  const stat = document.getElementById('wsStat');
  function setStat(t) {{ if(stat) stat.textContent = "WS: " + t; }}

  function connect() {{
    ws = new WebSocket(wsUrl);
    ws.onopen = () => {{ connected = true; setStat("connected"); }};
    ws.onclose = () => {{ connected = false; setStat("disconnected, retry…"); setTimeout(connect, 1000); }};
    ws.onerror = () => {{ setStat("error"); }};
    ws.onmessage = (ev) => {{ /* 서버 → 클라 메시지 필요시 사용 */ }};
  }}
  connect();

  function send(obj) {{
    if (connected && ws && ws.readyState === 1) {{
      ws.send(JSON.stringify(obj));
    }}
  }}

  // 키 입력
  const down = new Set();
  window.addEventListener('keydown', (e) => {{
    const k = e.key.toLowerCase();
    if (['a','d',' ','s'].includes(k)) e.preventDefault();
    if (k === 'a' && !down.has('a')) {{ down.add('a'); pressed.a = true; pressed.d = false; send(pressed); }}
    if (k === 'd' && !down.has('d')) {{ down.add('d'); pressed.d = true; pressed.a = false; send(pressed); }}
    if (k === ' ') {{ pressed.a=false; pressed.d=false; send({{stop:true}}); }}
    if (k === 's') {{ pressed.a=false; pressed.d=false; send({{center:true}}); }}
  }});
  window.addEventListener('keyup', (e) => {{
    const k = e.key.toLowerCase();
    if (k === 'a') {{ down.delete('a'); pressed.a = false; send(pressed); }}
    if (k === 'd') {{ down.delete('d'); pressed.d = false; send(pressed); }}
  }});

  // 키 유지 중이면 주기적으로 keepalive(지연/드랍 보정)
  setInterval(() => {{
    if (pressed.a || pressed.d) send(pressed);
  }}, 40);

  // 버튼(터치 지원)
  function hold(btn, setKey) {{
    let timer = null;
    const start = () => {{ setKey(true); timer = setInterval(() => send(pressed), 60); }};
    const stop  = () => {{ setKey(false); if(timer) clearInterval(timer); send(pressed); }};
    btn.addEventListener('pointerdown', (e) => {{ e.preventDefault(); start(); }});
    window.addEventListener('pointerup', stop);
    btn.addEventListener('pointerleave', stop);
  }}
  const btnL = document.getElementById('btnL');
  const btnC = document.getElementById('btnC');
  const btnR = document.getElementById('btnR');
  const btnStop = document.getElementById('btnStop');

  if (btnL) hold(btnL, (v)=>{{ pressed.a=v; if(v) pressed.d=false; }});
  if (btnR) hold(btnR, (v)=>{{ pressed.d=v; if(v) pressed.a=false; }});
  if (btnC) btnC.addEventListener('click', ()=>{{ pressed.a=false; pressed.d=false; send({{center:true}}); }});
  if (btnStop) btnStop.addEventListener('click', ()=>{{ pressed.a=false; pressed.d=false; send({{stop:true}}); }});
}})();
</script>
</body>
</html>
"""

# =========================
# 제어 루프(백그라운드 태스크)
# =========================
async def control_loop():
    global last_a, last_d, cur_us, target_us
    last = time.monotonic()
    while True:
        now = time.monotonic()
        dt = now - last
        if dt < TICK_S:
            await asyncio.sleep(TICK_S - dt)
            now = time.monotonic()
            dt = now - last
        last = now

        hold_left  = (now - last_a) <= HOLD_GRACE_S
        hold_right = (now - last_d) <= HOLD_GRACE_S

        ramp_dir = 0
        if hold_left and not hold_right:
            ramp_dir = -1
        elif hold_right and not hold_left:
            ramp_dir = +1

        if ramp_dir != 0:
            target_us += ramp_dir * NET_SPEED_US_PER_S * dt
            if target_us < LEFT_US:  target_us = LEFT_US
            if target_us > RIGHT_US: target_us = RIGHT_US

        # 슬루(속도 제한)
        max_step = NET_SPEED_US_PER_S * dt
        delta = target_us - cur_us
        if abs(delta) > max_step:
            cur_us += max_step if delta > 0 else -max_step
        else:
            cur_us = target_us

        # 적용
        if cur_us < LEFT_US:  cur_us = LEFT_US
        if cur_us > RIGHT_US: cur_us = RIGHT_US
        pca.channels[CHANNEL].duty_cycle = us_to_duty(int(cur_us))

# =========================
# Lifespan (startup/shutdown 대체)
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(control_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            pca.channels[CHANNEL].duty_cycle = 0
            pca.deinit()
        except Exception:
            pass

app = FastAPI(lifespan=lifespan)

# =========================
# 라우트 & WebSocket
# =========================
@app.get("/")
async def index():
    return HTMLResponse(HTML)

@app.get("/health")
async def health():
    return {"ok": True, "cur_us": int(cur_us), "target_us": int(target_us)}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    global controller, last_a, last_d, target_us
    await ws.accept()
    controller = ws
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)

            now = time.monotonic()
            if data.get("stop"):
                last_a = -1.0
                last_d = -1.0
                # stop은 현 위치에서 정지(자율 이동 없음)
                target_us = target_us
                continue
            if data.get("center"):
                last_a = -1.0
                last_d = -1.0
                target_us = CENTER_US
                continue
            if "a" in data or "d" in data:
                if data.get("a"):
                    last_a = now
                if data.get("d"):
                    last_d = now

    except WebSocketDisconnect:
        if controller is ws:
            controller = None

# =========================
# 직접 실행
# =========================
if __name__ == "__main__":
    import uvicorn
    # 모듈명 대신 인스턴스 직접 전달 → "Could not import module 'app'" 방지
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
