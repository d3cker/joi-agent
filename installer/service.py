"""Systemd entry point: explicit private runtime, physical GPU, offline models."""
import json
import os
from pathlib import Path
import sys


def main():
    kind, plan_path = sys.argv[1:]
    p = json.loads(Path(plan_path).read_text())
    root = Path(p["release"])
    os.environ.update({
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": str(p["gpu"]),
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
        "JOI_CONFIG_HOME": p["config"], "JOI_DATA_HOME": p["data"],
        "VOICE_AGENT_SUPERVISED": "1",
        "PYTHONPATH": str(root / "services/voice-backend"),
    })
    venv = root / f"venv-{kind}"
    site = venv / "lib/python3.12/site-packages"
    if kind == "backend":
        os.environ["LD_LIBRARY_PATH"] = ":".join(str(site / f"nvidia/{lib}/lib") for lib in ("cublas", "cudnn"))
        args = [str(venv / "bin/python"), "-m", "voice_agent"]
    elif kind == "higgs":
        cuda = site / "nvidia/cu13"
        os.environ.update({"CUDA_HOME": str(cuda), "LD_LIBRARY_PATH": str(cuda / "lib"),
            "PATH": str(cuda / "bin") + ":" + os.environ["PATH"],
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"})
        args = [str(venv / "bin/sgl-omni"), "serve", "--model-path", p["tts_model"],
            "--model-name", "bosonai/higgs-tts-3-4b", "--allowed-local-media-path", str(Path(p["voice"]).parent),
            "--host", "127.0.0.1", "--port", "8790", "--mem-fraction-static", "0.62", "--log-level", "info"]
    else:
        raise SystemExit("Unknown Joi service")
    os.chdir(root / "services/voice-backend")
    os.execv(args[0], args)


if __name__ == "__main__":
    main()
