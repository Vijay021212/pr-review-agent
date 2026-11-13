import subprocess

SIZE = "200x200"


def make_thumbnail(file_name: str) -> None:
    subprocess.run(f"convert {file_name} -resize {SIZE} thumb_{file_name}", shell=True, check=True)
