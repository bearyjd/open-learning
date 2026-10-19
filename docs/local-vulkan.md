# Optional local inference with Vulkan

This Linux x86_64 example uses llama.cpp b9551 and the official Qwen2.5-7B-Instruct
Q4_K_M weights. Neither the server nor model weights are bundled with Open Learning;
the app can use another compatible server. Model weights are separately licensed
under Apache-2.0. See the [official model card][model] and [license][license].

The [b9551 release][release] provides a Linux Vulkan binary, but no Linux CUDA
binary. Use an existing working Vulkan loader and NVIDIA driver; this procedure
does not install system packages. Keep the extracted shared libraries together.
This configuration loaded on an 8 GB RTX 4070 Laptop GPU. Successful loading does
not establish answer quality; see the [model results](verification.md#live-model-results).

## Download and verify

Use a directory with at least 5 GB free. Downloads can resume after interruption.
The model revision and all three SHA256 checks are pinned to official artifacts.

```bash
set -euo pipefail
mkdir -p "$HOME/.local/share/open-learning-vulkan"
cd "$HOME/.local/share/open-learning-vulkan"
archive=llama-b9551-bin-ubuntu-vulkan-x64.tar.gz
curl --fail --location --retry 3 --continue-at - --output "$archive" \
  "https://github.com/ggml-org/llama.cpp/releases/download/b9551/$archive"
printf '%s  %s\n' \
  f253db4714bc1672a0c6c7fbe08f0acb24daeb9be383ba8d6e89074d8b43d6ea \
  "$archive" | sha256sum --check - && tar -xzf "$archive"

model_base=https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/bb5d59e06d9551d752d08b292a50eb208b07ab1f
for part in 00001 00002; do
  file="qwen2.5-7b-instruct-q4_k_m-${part}-of-00002.gguf"
  curl --fail --location --retry 3 --continue-at - --output "$file" "$model_base/$file" || break
done
sha256sum --check <<'SUMS'
dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db  qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf
539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a  qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf
SUMS
```

Stop if any checksum fails. The two model parts total 4,683,073,632 bytes
(4.361 GiB); keep both in the same directory. b9551 loads both when given the
first part, without merging. Hash sources: [release API][release-api] and
[official model metadata][model-api].

## Select the NVIDIA GPU and start the server

```bash
ldd ./llama-b9551/llama-server
ldd ./llama-b9551/libggml-vulkan.so
./llama-b9551/llama-server --list-devices
```

Resolve any missing libraries before proceeding. Copy the NVIDIA device identifier
from the list into `nvidia_device` below. For example, it may be `Vulkan1` when
Intel graphics is also present; the index varies by machine.

```bash
nvidia_device=REPLACE_WITH_NVIDIA_DEVICE
./llama-b9551/llama-server \
  --model ./qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf \
  --alias qwen2.5-7b-instruct-q4_k_m \
  --device "$nvidia_device" --gpu-layers all \
  --ctx-size 8192 --parallel 1 --flash-attn on \
  --batch-size 512 --ubatch-size 128 \
  --no-jinja --chat-template chatml \
  --host 127.0.0.1 --port 18080
```

The ChatML flags are specific to this Qwen family. Check startup logs for the
NVIDIA device, GPU offload, context size, and successful server startup before
testing answers. See the [b9551 server options][server].

For a backend running directly on the same host, configure:

```dotenv
OL_AI_BASE_URL=http://127.0.0.1:18080/v1
OL_AI_MODEL=qwen2.5-7b-instruct-q4_k_m
OL_AI_API_KEY=local
```

Container networking needs a reachable host address; see [local AI setup](local-ai.md).
Validate source-grounded responses using the checks in [verification](verification.md).

## Compact model comparison

The [Qwen3-VL-4B-Instruct-GGUF][compact-model] Q4_K_M file was evaluated as a
text-only alternative. Download the official pinned revision and verify its
SHA256 before use. The 2,497,281,664-byte model file is a separate Apache-2.0
third-party download; Open Learning does not bundle it. The comparison did not
load a vision encoder or `mmproj`, so it establishes nothing about image input.

```bash
set -euo pipefail
cd "$HOME/.local/share/open-learning-vulkan"
compact_file=Qwen3VL-4B-Instruct-Q4_K_M.gguf
curl --fail --location --retry 3 --continue-at - --output "$compact_file" \
  "https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/1cd86afb9a95c410a6038ab3b40d8b578c892266/$compact_file"
printf '%s  %s\n' \
  66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a \
  "$compact_file" | sha256sum --check -
```

With a working Vulkan-enabled llama.cpp server, select the NVIDIA device as
above and start the model with its **embedded Jinja template**:

```bash
nvidia_device=REPLACE_WITH_NVIDIA_DEVICE
./llama-b9551/llama-server \
  --model ./Qwen3VL-4B-Instruct-Q4_K_M.gguf \
  --alias qwen3-vl-4b-instruct-q4_k_m \
  --device "$nvidia_device" --gpu-layers all \
  --ctx-size 8192 --parallel 1 --flash-attn on \
  --batch-size 512 --ubatch-size 128 \
  --host 127.0.0.1 --port 18080
```

The measured comparison used llama.cpp `b9551-2-g9e3b928fd` on an 8 GB RTX
4070 Laptop GPU, with the server defaults for the model's embedded Jinja
template and thinking disabled. Do not carry over the 7B command's `--no-jinja`
or `--chat-template chatml` flags. Point `OL_AI_MODEL` at
`qwen3-vl-4b-instruct-q4_k_m` when reproducing the
[quality evaluation](model-evaluation.md#compact-local-model-comparison).
Both sample runs failed its semantic gate; this model is not a recommended
default. See the [pinned file metadata][compact-model-api] and
[license][compact-license].

[release]: https://github.com/ggml-org/llama.cpp/releases/tag/b9551
[release-api]: https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b9551
[model]: https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/blob/bb5d59e06d9551d752d08b292a50eb208b07ab1f/README.md
[model-api]: https://huggingface.co/api/models/Qwen/Qwen2.5-7B-Instruct-GGUF/revision/bb5d59e06d9551d752d08b292a50eb208b07ab1f?blobs=true
[license]: https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/blob/bb5d59e06d9551d752d08b292a50eb208b07ab1f/LICENSE
[server]: https://github.com/ggml-org/llama.cpp/blob/b9551/tools/server/README.md
[compact-model]: https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/blob/1cd86afb9a95c410a6038ab3b40d8b578c892266/README.md
[compact-model-api]: https://huggingface.co/api/models/Qwen/Qwen3-VL-4B-Instruct-GGUF/revision/1cd86afb9a95c410a6038ab3b40d8b578c892266?blobs=true
[compact-license]: https://www.apache.org/licenses/LICENSE-2.0
