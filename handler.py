import base64
import json
import os
import shutil
import time
import uuid
import requests
import runpod

COMFY_URL = os.getenv("COMFY_URL", "http://127.0.0.1:8188")
WORKFLOW_PATH = os.getenv("WORKFLOW_PATH", "/app/workflows/wan26_t2v.json")
INPUT_DIR = os.getenv("COMFY_INPUT_DIR", "/comfyui/input")
OUTPUT_DIR = os.getenv("COMFY_OUTPUT_DIR", "/comfyui/output")
COMFY_START_TIMEOUT = int(os.getenv("COMFY_START_TIMEOUT", "900"))

def wait_for_comfyui(timeout=COMFY_START_TIMEOUT):
    start = time.time()
    while time.time() - start < timeout:
        try:
            requests.get(f"{COMFY_URL}/system_stats", timeout=3)
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("ComfyUI did not start in time")

def replace_tokens(obj, mapping):
    if isinstance(obj, dict):
        return {k: replace_tokens(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replace_tokens(v, mapping) for v in obj]
    if isinstance(obj, str):
        for key, val in mapping.items():
            if f"__{key}__" in obj:
                obj = obj.replace(f"__{key}__", str(val))
        return obj
    return obj

def handler(event):
    job_input = event["input"]
    
    # 1. Wait for ComfyUI
    wait_for_comfyui()

    # 2. Load and Prepare Workflow
    with open(WORKFLOW_PATH, "r") as f:
        workflow = json.load(f)

    # 3. Build Token Mapping
    mapping = {
        "PROMPT": job_input.get("prompt", ""),
        "NEGATIVE": job_input.get("negative_prompt", "ugly, blurry, low resolution"),
        "WIDTH": int(job_input.get("width", 832)),
        "HEIGHT": int(job_input.get("height", 480)),
        "LENGTH": int(job_input.get("length", 81)), # ~5s at 16fps
        "STEPS": int(job_input.get("steps", 25)),
        "CFG": float(job_input.get("cfg", 5.0)),
        "SEED": int(job_input.get("seed", 42)),
        "SCHEDULER": job_input.get("scheduler", "dpmpp_2m_sde"),
    }

    workflow = replace_tokens(workflow, mapping)

    # 4. Prompt ComfyUI
    resp = requests.post(f"{COMFY_URL}/prompt", json={"prompt": workflow})
    resp.raise_for_status()
    prompt_id = resp.json()["prompt_id"]

    # 5. Poll for Completion
    while True:
        history = requests.get(f"{COMFY_URL}/history/{prompt_id}").json()
        if prompt_id in history:
            break
        time.sleep(1)

    # 6. Extract Output
    output_files = []
    for node_id, node_output in history[prompt_id]["outputs"].items():
        if "gifs" in node_output:
            for gif in node_output["gifs"]:
                output_files.append(os.path.join(OUTPUT_DIR, gif["filename"]))
        elif "images" in node_output:
            for img in node_output["images"]:
                output_files.append(os.path.join(OUTPUT_DIR, img["filename"]))

    # 7. Encode and Return
    results = []
    for file_path in output_files:
        with open(file_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")
            results.append(f"data:video/mp4;base64,{b64_data}")

    return {"output": results[0] if results else None}

if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
