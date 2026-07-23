# # Separate audio into stems with Audio Separator

# In this example, we show how to run [Audio Separator](https://github.com/nomadkaraoke/python-audio-separator)
# on Modal to split a song into individual stems (e.g. vocals / instrumental,
# or vocals / drums / bass / other).

# `audio-separator` is a Python package that wraps the pretrained models used by
# [Ultimate Vocal Remover (UVR)](https://github.com/Anjok07/ultimatevocalremovergui),
# including modern Transformer-based architectures like BS-Roformer and
# Mel-Band Roformer, which currently produce some of the best open source
# separation quality available (well beyond older tools like Demucs or Spleeter).

# Unlike `main.py` (music generation), this is a small, self-contained,
# GPU-accelerated batch tool: you hand it a local audio file via `modal run`,
# and it hands you back separated stems, saved to a local directory. There's
# no need for a persistent web UI, an upload step, or any long-lived storage
# for user audio here -- everything happens within a single command.
# See the README for a full write-up of why this design was chosen.

from pathlib import Path
from re import findall
from typing import Optional
from uuid import uuid4

import modal

# ## Setting up dependencies

# `audio-separator` is a plain pip package (no local git clone / custom build
# steps required, unlike ACE-Step in `main.py`), so the image definition here
# is much simpler. We still need `ffmpeg` for decoding/encoding mp3/wav/flac.

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .uv_pip_install(
        "audio-separator[gpu]==0.44.3",
    )
)

# As with ACE-Step's model weights in `main.py`, we cache the (large)
# pretrained separation model checkpoints in a Modal Volume, so we don't
# redownload them on every cold start.

model_cache_dir = "/root/.cache/audio-separator-models"
model_cache = modal.Volume.from_name(
    "audio-separator-model-cache", create_if_missing=True
)

# `htdemucs_6s.yaml` is Demucs v4's 6-stem model, splitting audio into
# vocals, drums, bass, guitar, piano, and other -- a full instrument-level
# breakdown rather than just vocals/instrumental. Run
# `audio-separator --list_models` for other options, e.g. the higher-SDR but
# 2-stem-only `model_bs_roformer_ep_317_sdr_12.9755.ckpt` (vocals /
# instrumental) if you only need vocal removal.

# DEFAULT_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
DEFAULT_MODEL = "htdemucs_6s.yaml"

# ## Running audio separation on Modal

app = modal.App("audio-separator")


@app.cls(gpu="t4", image=image, volumes={model_cache_dir: model_cache})
class AudioSeparator:
    @modal.enter()
    def init(self):
        from audio_separator.separator import ( # pyright: ignore[reportMissingImports]
            Separator,
        )

        self.separator = Separator(
            output_dir="/tmp/audio-separator-output",
            model_file_dir=model_cache_dir,
        )
        self.separator.load_model(model_filename=DEFAULT_MODEL)
        self.loaded_model = DEFAULT_MODEL

    @modal.method()
    def run(
        self,
        audio_bytes: bytes,
        filename: str,
        model_filename: Optional[str] = None,
        output_format: str = "wav",
    ) -> dict[str, bytes]:
        # Swap the loaded model only if the caller asked for a different one,
        # since (re)loading a model is much more expensive than separating.
        if model_filename and model_filename != self.loaded_model:
            self.separator.load_model(model_filename=model_filename)
            self.loaded_model = model_filename

        self.separator.output_format = output_format.upper()

        input_dir = Path("/tmp/audio-separator-input")
        input_dir.mkdir(parents=True, exist_ok=True)
        input_path = input_dir / f"{uuid4()}_{filename}"
        input_path.write_bytes(audio_bytes)

        try:
            output_filenames = self.separator.separate(str(input_path))

            stems = {}
            output_dir = Path(self.separator.output_dir)
            for i, output_filename in enumerate(output_filenames):
                stem_label = _extract_stem_label(output_filename, fallback_index=i)
                extension = Path(output_filename).suffix
                output_path = output_dir / output_filename
                stems[f"{stem_label}{extension}"] = output_path.read_bytes()
                output_path.unlink(missing_ok=True)

            return stems
        finally:
            input_path.unlink(missing_ok=True)


def _extract_stem_label(output_filename: str, fallback_index: int) -> str:
    # Audio Separator names output files like
    # "<original>_(Vocals)_<model friendly name>.wav" -- pull out the part in
    # the last parentheses (e.g. "Vocals") to use as a clean stem name.
    matches = findall(r"\(([^)]+)\)", output_filename)
    return matches[-1].lower() if matches else f"stem{fallback_index}"


# We can then separate a local audio file by running code like what we have
# in the `local_entrypoint` below. There's deliberately no `modal deploy`-based
# web UI for this tool -- see the README for why a CLI-only workflow is the
# better fit for source separation.


@app.local_entrypoint()
def main(
    input_path: str,
    model_filename: Optional[str] = None,
    output_format: str = "wav",
    output_dir: str = "separated",
):
    input_file = Path(input_path)
    if not input_file.is_file():
        raise FileNotFoundError(f"入力ファイルが見つかりません: {input_file}")

    print(
        f"🎚️  '{input_file.name}' を音源分離中... (model={model_filename or DEFAULT_MODEL})"
    )

    audio_separator = AudioSeparator()  # outside of this file, use modal.Cls.from_name
    stems = audio_separator.run.remote(
        input_file.read_bytes(),
        input_file.name,
        model_filename=model_filename,
        output_format=output_format,
    )

    dest_dir = Path(output_dir) / slugify(input_file.stem)
    dest_dir.mkdir(parents=True, exist_ok=True)

    for stem_filename, stem_bytes in stems.items():
        stem_path = dest_dir / stem_filename
        stem_path.write_bytes(stem_bytes)
        print(f"  💾 {stem_path}")

    print(f"✅ 完了！ {len(stems)} 個のステムを '{dest_dir}' に保存しました")


def slugify(string):
    return (
        string.lower()
        .replace(" ", "-")
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
    )


# You can execute it with a command like:

# ``` shell
# modal run separate.py --input-path ./song.mp3
# ```

# Pass in `--help` to see options and how to use them.
