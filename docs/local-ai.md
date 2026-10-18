# Local inference

Open Learning sends extracted passages to the endpoint you configure. The
default is local; there is no automatic cloud fallback. Both insight generation
and tutoring require strict `response_format` JSON-schema output from a chat
completions endpoint. OpenAI API compatibility alone is insufficient: a server
supporting only JSON-object output fails closed.
Malformed output or evidence that does not match the supplied text causes a
failed operation rather than publication of an unsupported card.

## llama.cpp

Install llama.cpp separately with the CPU or GPU build appropriate to your host.
Download a legally licensed instruct GGUF model independently and check its
license. Choose a size and quantization that fit your hardware; memory use also
depends on context size and GPU layer count. CPU inference may require a smaller
model and longer request timeouts.
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
The host-gateway name does not make a server bound only to host loopback
reachable from a container. Check connectivity from the worker's network before
submitting an article.

The provider must support `response_format` with `type: "json_schema"` and strict
schema output. Current server options
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

## Verify your setup

Live local-model acceptance remains pending in the current verification report.
The [manual smoke procedure](verification.md#acceptance-still-pending) exercises
the real API, extraction, model, tutor, and review loop. It requires
`OL_API_TOKEN`, accepts `OL_SERVER_URL` and `OL_SMOKE_URL`, and modifies the
connected library. Use a fresh public source and database for its due-review
check; keep credentials and private endpoint details out of shared output.
