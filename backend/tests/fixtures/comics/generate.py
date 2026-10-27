"""Regenerate our tiny RAR5 solid fixture with a local licensed RAR CLI."""
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

target = Path(__file__).with_name("rar5-solid.cbr")
with TemporaryDirectory() as temporary:
    root = Path(temporary)
    for name, color in [("10.png", "blue"), ("2.png", "green"), ("1.png", "red")]:
        Image.new("RGB", (30, 20), color).save(root / name)
    subprocess.run([sys.argv[1], "a", "-ma5", "-s", "-m5", "-ep", "-idq", str(target),
                    str(root / "10.png"), str(root / "2.png"), str(root / "1.png")], check=True)
