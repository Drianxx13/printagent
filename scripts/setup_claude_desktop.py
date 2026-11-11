"""Register printagent in Claude Desktop's config (keeps a .bak of the old file)."""
import json
import os
import shutil
import sys
from pathlib import Path

exe = Path(sys.prefix) / ("Scripts/printagent.exe" if os.name == "nt" else "bin/printagent")
if os.name == "nt":
    base = Path(os.environ["APPDATA"])
elif sys.platform == "darwin":
    base = Path.home() / "Library" / "Application Support"
else:
    base = Path.home() / ".config"
cfg = base / "Claude" / "claude_desktop_config.json"

data = {}
if cfg.exists() and cfg.read_text(encoding="utf-8").strip():
    data = json.loads(cfg.read_text(encoding="utf-8"))
    shutil.copy(cfg, cfg.with_name(cfg.name + ".bak"))
data.setdefault("mcpServers", {})["printagent"] = {"command": str(exe)}
cfg.parent.mkdir(parents=True, exist_ok=True)
cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
print(f"printagent registered in {cfg}")
print(f"command: {exe}")
