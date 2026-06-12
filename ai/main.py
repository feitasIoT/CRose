import os
import re
import uuid

import docker
import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from sentence_transformers import SentenceTransformer

app = FastAPI()
model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
TRAIN_CONTAINER_NAME = os.getenv("CROSE_TRAIN_CONTAINER_NAME", "crose_ai_train")
TRAIN_YAML_DIR = os.getenv("CROSE_TRAIN_YAML_DIR", "/app/ai_config/generated")
TRAIN_LOG_DIR = os.getenv("CROSE_TRAIN_LOG_DIR", "/app/output")
VLLM_BASE_URL = os.getenv("CROSE_VLLM_BASE_URL", "http://vllm:8000").rstrip("/")
VLLM_CHAT_API_PATH = os.getenv("CROSE_VLLM_CHAT_API_PATH", "/v1/chat/completions")
VLLM_LOAD_ADAPTER_API_PATH = os.getenv("CROSE_VLLM_LOAD_ADAPTER_API_PATH", "/v1/load_lora_adapter")
VLLM_UNLOAD_ADAPTER_API_PATH = os.getenv("CROSE_VLLM_UNLOAD_ADAPTER_API_PATH", "/v1/unload_lora_adapter")
TRAIN_JOBS = {}
LOADED_ADAPTERS = {}


def _docker_client():
    try:
        return docker.from_env()
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Docker is unavailable: {error}")


def _write_train_yaml(job_id, yaml_text):
    os.makedirs(TRAIN_YAML_DIR, exist_ok=True)
    yaml_path = os.path.join(TRAIN_YAML_DIR, f"{job_id}.yaml")
    with open(yaml_path, "w", encoding="utf-8") as yaml_fp:
        yaml_fp.write(yaml_text)
    return yaml_path


def _tail_log(container, log_path):
    try:
        result = container.exec_run(["/bin/sh", "-lc", f"tail -n 120 {log_path} || true"])
        output = result.output or b""
        return output.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_progress(log_text):
    if not log_text:
        return None
    matches = re.findall(r"(\d{1,3}(?:\.\d+)?)\s*%", log_text)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except Exception:
        return None


def _normalize_path(path):
    normalized = str(path or "").strip()
    if not normalized:
        return "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def _vllm_url(path):
    return f"{VLLM_BASE_URL}{_normalize_path(path)}"


def _get_vllm_serving_model_name():
    try:
        response = requests.get(_vllm_url("/v1/models"), timeout=20)
        response.raise_for_status()
        payload = response.json() if response.text else {}
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list) and data:
            first = data[0] if isinstance(data[0], dict) else {}
            model_id = str(first.get("id") or "").strip()
            if model_id:
                return model_id
    except Exception:
        return ""
    return ""


def _call_vllm_api(path, payload):
    try:
        response = requests.post(_vllm_url(path), json=payload, timeout=60)
        response.raise_for_status()
        if not response.text:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}
    except requests.HTTPError as error:
        response = error.response
        detail = ""
        if response is not None and response.text:
            detail = response.text
        if response is not None and response.status_code == 400:
            normalized_detail = detail.lower()
            if "load_lora_adapter" in _normalize_path(path) and ("already" in normalized_detail and "load" in normalized_detail):
                return {"status": "already_loaded", "raw": detail}
            if "unload_lora_adapter" in _normalize_path(path) and ("not loaded" in normalized_detail or "not found" in normalized_detail):
                return {"status": "already_unloaded", "raw": detail}
        raise HTTPException(status_code=500, detail=f"vLLM API call failed: {error}. body={detail}")
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"vLLM API call failed: {error}")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/embed")
async def get_embedding(data: dict):
    text = data.get("text", "")
    vector = model.encode(text).tolist()
    return {"vector": vector}


@app.post("/v1/vllm/adapters/load")
async def load_vllm_adapter(data: dict):
    adapter_name = str(data.get("adapter_name") or data.get("lora_name") or "").strip()
    adapter_path = str(data.get("adapter_path") or data.get("lora_path") or "").strip()
    base_model = str(data.get("model") or "").strip()
    if not adapter_name:
        raise HTTPException(status_code=400, detail="adapter_name is required.")
    if not adapter_path:
        raise HTTPException(status_code=400, detail="adapter_path is required.")
    payload = {
        "lora_name": adapter_name,
        "lora_path": adapter_path,
    }
    result = _call_vllm_api(VLLM_LOAD_ADAPTER_API_PATH, payload)
    serving_model = _get_vllm_serving_model_name()
    LOADED_ADAPTERS[adapter_name] = {
        "adapter_path": adapter_path,
        "base_model": base_model,
        "serving_model": serving_model,
    }
    return {
        "status": "loaded",
        "adapter_name": adapter_name,
        "adapter_path": adapter_path,
        "result": result,
    }


@app.post("/v1/load_lora_adapter")
async def load_vllm_adapter_legacy(data: dict):
    return await load_vllm_adapter(data)


@app.post("/v1/vllm/adapters/unload")
async def unload_vllm_adapter(data: dict):
    adapter_name = str(data.get("adapter_name") or data.get("lora_name") or "").strip()
    if not adapter_name:
        raise HTTPException(status_code=400, detail="adapter_name is required.")
    payload = {
        "lora_name": adapter_name,
    }
    result = _call_vllm_api(VLLM_UNLOAD_ADAPTER_API_PATH, payload)
    LOADED_ADAPTERS.pop(adapter_name, None)
    return {
        "status": "unloaded",
        "adapter_name": adapter_name,
        "result": result,
    }


@app.post("/v1/unload_lora_adapter")
async def unload_vllm_adapter_legacy(data: dict):
    return await unload_vllm_adapter(data)


@app.get("/v1/vllm/adapters")
async def list_vllm_adapters():
    return {"adapters": LOADED_ADAPTERS}


@app.post("/v1/vllm/chat")
async def vllm_chat(data: dict):
    model_name = str(data.get("model") or "").strip()
    messages = data.get("messages")
    if not model_name:
        raise HTTPException(status_code=400, detail="model is required.")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages is required.")
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": data.get("temperature", 0.1),
    }
    if data.get("max_tokens") is not None:
        payload["max_tokens"] = data.get("max_tokens")
    if model_name in LOADED_ADAPTERS:
        adapter_info = LOADED_ADAPTERS.get(model_name) or {}
        serving_model = str(adapter_info.get("serving_model") or "").strip()
        if serving_model:
            payload["model"] = serving_model
        payload["extra_body"] = {
            "lora_request": {
                "lora_name": model_name,
            },
        }
    result = _call_vllm_api(VLLM_CHAT_API_PATH, payload)
    return result


@app.post("/v1/train")
async def start_training(request: Request):
    body = await request.body()
    yaml_text = body.decode("utf-8", errors="ignore").strip()
    if not yaml_text:
        raise HTTPException(status_code=400, detail="Training YAML is empty.")
    job_id = uuid.uuid4().hex
    yaml_path = _write_train_yaml(job_id, yaml_text)
    log_path = f"{TRAIN_LOG_DIR}/{job_id}.log"
    client = _docker_client()
    try:
        container = client.containers.get(TRAIN_CONTAINER_NAME)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Training container not found: {error}")
    command = f"llamafactory-cli train {yaml_path} > {log_path} 2>&1"
    try:
        exec_info = client.api.exec_create(
            container.id,
            ["/bin/sh", "-lc", command],
            stdout=True,
            stderr=True,
        )
        exec_id = exec_info.get("Id")
        client.api.exec_start(exec_id, detach=True)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Failed to start training command: {error}")
    TRAIN_JOBS[job_id] = {
        "exec_id": exec_id,
        "container_name": TRAIN_CONTAINER_NAME,
        "log_path": log_path,
        "yaml_path": yaml_path,
    }
    return {"job_id": job_id, "status": "queued"}


@app.get("/v1/train/{job_id}")
async def training_status(job_id: str):
    job = TRAIN_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found.")
    client = _docker_client()
    try:
        container = client.containers.get(job["container_name"])
        inspect_data = client.api.exec_inspect(job["exec_id"])
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Failed to inspect training job: {error}")
    running = bool(inspect_data.get("Running"))
    exit_code = inspect_data.get("ExitCode")
    status = "running"
    error_text = ""
    if not running:
        if exit_code == 0:
            status = "completed"
        else:
            status = "failed"
            error_text = f"Training process exited with code {exit_code}."
    logs = _tail_log(container, job["log_path"])
    progress = _extract_progress(logs)
    payload = {
        "job_id": job_id,
        "status": status,
        "progress": progress if progress is not None else (100.0 if status == "completed" else 0.0),
        "logs": logs,
    }
    if error_text:
        payload["error"] = error_text
    return payload

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
