# Local inference

Open Learning sends extracted passages to the endpoint you configure. The
default is local; there is no automatic cloud fallback. Both insight generation
and tutoring request JSON from an OpenAI-compatible chat completions endpoint.
Malformed output or evidence that does not match the supplied text causes a
failed operation rather than publication of an unsupported card.

## llama.cpp

Install llama.cpp separately with the CUDA build appropriate to your Linux host.
Download a legally licensed instruct GGUF model independently and check its
license. A quantized 3B–7B instruct model is a reasonable starting size for an
8 GB GPU; actual memory use also depends on context size and GPU layer count.
No particular model is required by the application.

Example using an already downloaded model:

```sh
llama-server -m /path/to/instruct-Q4_K_M.gguf \
  --alias local-model --host 127.0.0.1 --port 8080 \
  --ctx-size 8192 --n-gpu-layers 99
```

For a directly running Python backend, set:

```dotenv
OL_AI_BASE_URL=http://127.0.0.1:8080/v1
OL_AI_MODEL=local-model
OL_AI_API_KEY=local
```

Reduce GPU layers or context size if your model runs out of VRAM. Worker
containers must use a reachable host address rather than their own loopback.
If you change the server bind address for container access, firewall it to the
private bridge; do not expose the unauthenticated model endpoint publicly.

The provider must support `response_format` JSON output. Current server options
and API details are in the [official llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

## Ollama

Run Ollama separately and provision an instruct model that fits your hardware.
Use its OpenAI compatibility endpoint:

```dotenv
OL_AI_BASE_URL=http://127.0.0.1:11434/v1
OL_AI_MODEL=YOUR_INSTALLED_MODEL_NAME
OL_AI_API_KEY=local
```

For Compose, replace the loopback host with the reachable private host address.
Use the exact model identifier shown by your Ollama installation. See
[Ollama's official compatibility documentation](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx).

## Optional remote provider and embeddings

A remote OpenAI-compatible server can use the same configuration with its HTTPS
endpoint, model name, and key. Doing so sends article passages and tutor answers
to that provider. This is an explicit administrator choice; review its terms
before sending private reading material.

`OL_EMBEDDING_MODEL` is reserved for a later embedding pipeline. In this
milestone, leaving it blank is expected: search is lexical and no embedding
request is made. Separate embedding endpoint configuration, semantic search,
and cross-document RAG are not implemented yet.
