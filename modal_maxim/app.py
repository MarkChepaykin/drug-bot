"""Голос «Максим» на бесплатном serverless-GPU Modal.

Пайплайн одного запроса: текст -> edge-tts (обычный мужской голос) -> RVC-модель
MaximBot перекрашивает тембр в Максима -> отдаём wav. GPU T4, scale-to-zero:
платим только за секунды синтеза. Бот на Render дёргает /tts, при простое всё гаснет.

Деплой:
  modal volume create maxim-models
  modal volume put maxim-models <MaximBot_e240_s3120.pth> /
  modal volume put maxim-models <added_..._MaximBot_v2.index> /
  modal secret create maxim-token TOKEN=<секрет>   # тот же кладём боту в env RVC_TOKEN
  modal deploy modal_maxim/app.py
"""
import modal

MODEL_PTH = "/models/MaximBot_e240_s3120.pth"
MODEL_INDEX = "/models/added_IVF440_Flat_nprobe_1_MaximBot_v2.index"
BASE_VOICE = "ru-RU-DmitryNeural"

# omegaconf==2.0.6 (транзитив rvc-python, стек fairseq) имеет «невалидные» метаданные
# (PyYAML (>=5.1.*)), которые принимает только pip<24.1 — иначе ResolutionImpossible.
image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("ffmpeg", "build-essential")
    .run_commands("python -m pip install 'pip<24.1' 'setuptools<70' wheel")
    .pip_install("torch==2.1.1", "torchaudio==2.1.1")
    .pip_install("rvc-python")
    .pip_install("edge-tts", "soundfile", "fastapi[standard]")
)

app = modal.App("maxim-tts")
vol = modal.Volume.from_name("maxim-models", create_if_missing=True)


@app.function(
    gpu="T4",
    image=image,
    volumes={"/models": vol},
    secrets=[modal.Secret.from_name("maxim-token")],
    scaledown_window=120,   # держим GPU тёплым 2 мин после последней фразы
    timeout=180,
)
@modal.asgi_app()
def web():
    import asyncio
    import os
    import shutil
    import subprocess
    import tempfile

    from fastapi import FastAPI, Request, Response
    from fastapi.responses import JSONResponse
    from rvc_python.infer import RVCInference

    rvc = RVCInference(device="cuda:0")
    rvc.load_model(MODEL_PTH)
    try:
        rvc.set_params(index_path=MODEL_INDEX)
    except Exception:
        try:
            rvc.index_path = MODEL_INDEX
        except Exception:
            pass
    try:
        rvc.set_params(f0method="rmvpe", f0up_key=0, index_rate=0.6,
                       filter_radius=3, protect=0.33, rms_mix_rate=0.25)
    except TypeError:
        rvc.set_params({"f0method": "rmvpe", "f0up_key": 0, "index_rate": 0.6,
                        "filter_radius": 3, "protect": 0.33, "rms_mix_rate": 0.25})
    print("[maxim] модель загружена", flush=True)

    def synth(text: str) -> bytes:
        import edge_tts
        work = tempfile.mkdtemp()
        base, base_wav, out = (os.path.join(work, n) for n in ("b.mp3", "b.wav", "o.wav"))
        try:
            asyncio.run(edge_tts.Communicate(text, BASE_VOICE).save(base))
            subprocess.run(["ffmpeg", "-y", "-i", base, "-ar", "40000", "-ac", "1", base_wav],
                           check=True, capture_output=True)
            rvc.infer_file(base_wav, out)
            with open(out, "rb") as f:
                return f.read()
        finally:
            shutil.rmtree(work, ignore_errors=True)

    webapp = FastAPI()

    @webapp.post("/tts")
    async def tts(request: Request):
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "bad json"}, status_code=400)
        if data.get("token") != os.environ.get("TOKEN"):
            return JSONResponse({"error": "unauthorized"}, status_code=403)
        text = (data.get("text") or "").strip()[:600]
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)
        wav = await asyncio.to_thread(synth, text)
        return Response(content=wav, media_type="audio/wav")

    @webapp.get("/warm")
    async def warm():
        return {"ok": True}

    return webapp
