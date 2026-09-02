# MANIFEST: cross-framework matrix

- Repository commit: `1b2c0655ab24`
- Seed: `20260902`
- N per cell: `50`
- Machine: macOS-26.3-arm64-arm-64bit arm64, CPython
- Started: 2026-09-02T12:47:53+00:00
- Finished: 2026-09-02T12:47:53+00:00

## Commands

    experiments/frameworks/livekit_1_7_1/.venv/bin/python experiments/frameworks/livekit_shared/run.py --config sync-tool --n 50 --seed 20260902 --out results/frameworks/raw/livekit-1.7.1-sync-tool.jsonl
    experiments/frameworks/livekit_1_7_1/.venv/bin/python experiments/frameworks/livekit_shared/run.py --config inflight-tool --n 50 --seed 20260902 --out results/frameworks/raw/livekit-1.7.1-inflight-tool.jsonl
    experiments/frameworks/livekit_1_7_1/.venv/bin/python experiments/frameworks/livekit_shared/run.py --config late-tool --n 50 --seed 20260902 --out results/frameworks/raw/livekit-1.7.1-late-tool.jsonl
    experiments/frameworks/livekit_1_7_1/.venv/bin/python experiments/frameworks/livekit_shared/run.py --config disallow-interruptions --n 50 --seed 20260902 --out results/frameworks/raw/livekit-1.7.1-disallow-interruptions.jsonl
    experiments/frameworks/livekit_1_7_1/.venv/bin/python experiments/frameworks/livekit_shared/run.py --config handoff-tool --n 50 --seed 20260902 --out results/frameworks/raw/livekit-1.7.1-handoff-tool.jsonl
    experiments/frameworks/livekit_1_3_10/.venv/bin/python experiments/frameworks/livekit_shared/run.py --config sync-tool --n 50 --seed 20260902 --out results/frameworks/raw/livekit-1.3.10-sync-tool.jsonl
    experiments/frameworks/livekit_1_3_10/.venv/bin/python experiments/frameworks/livekit_shared/run.py --config inflight-tool --n 50 --seed 20260902 --out results/frameworks/raw/livekit-1.3.10-inflight-tool.jsonl
    experiments/frameworks/livekit_1_3_10/.venv/bin/python experiments/frameworks/livekit_shared/run.py --config late-tool --n 50 --seed 20260902 --out results/frameworks/raw/livekit-1.3.10-late-tool.jsonl
    experiments/frameworks/livekit_1_3_10/.venv/bin/python experiments/frameworks/livekit_shared/run.py --config disallow-interruptions --n 50 --seed 20260902 --out results/frameworks/raw/livekit-1.3.10-disallow-interruptions.jsonl
    experiments/frameworks/livekit_1_3_10/.venv/bin/python experiments/frameworks/livekit_shared/run.py --config handoff-tool --n 50 --seed 20260902 --out results/frameworks/raw/livekit-1.3.10-handoff-tool.jsonl
    experiments/frameworks/pipecat/.venv/bin/python experiments/frameworks/pipecat/run.py --config sync-tool --n 50 --seed 20260902 --out results/frameworks/raw/pipecat-sync-tool.jsonl
    experiments/frameworks/pipecat/.venv/bin/python experiments/frameworks/pipecat/run.py --config inflight-tool --n 50 --seed 20260902 --out results/frameworks/raw/pipecat-inflight-tool.jsonl
    experiments/frameworks/pipecat/.venv/bin/python experiments/frameworks/pipecat/run.py --config late-tool --n 50 --seed 20260902 --out results/frameworks/raw/pipecat-late-tool.jsonl
    experiments/frameworks/pipecat/.venv/bin/python experiments/frameworks/pipecat/run.py --config disallow-interruptions --n 50 --seed 20260902 --out results/frameworks/raw/pipecat-disallow-interruptions.jsonl
    experiments/frameworks/pipecat/.venv/bin/python experiments/frameworks/pipecat/run.py --config inflight-tool --optout --n 50 --seed 20260902 --out results/frameworks/raw/pipecat-inflight-optout.jsonl
    experiments/frameworks/openai_agents/.venv/bin/python experiments/frameworks/openai_agents/run.py --config sync-tool --n 50 --seed 20260902 --out results/frameworks/raw/openai-agents-sync-tool.jsonl
    experiments/frameworks/openai_agents/.venv/bin/python experiments/frameworks/openai_agents/run.py --config inflight-tool --n 50 --seed 20260902 --out results/frameworks/raw/openai-agents-inflight-tool.jsonl
    experiments/frameworks/openai_agents/.venv/bin/python experiments/frameworks/openai_agents/run.py --config late-tool --n 50 --seed 20260902 --out results/frameworks/raw/openai-agents-late-tool.jsonl
    experiments/frameworks/openai_agents/.venv/bin/python experiments/frameworks/openai_agents/run.py --config disallow-interruptions --n 50 --seed 20260902 --out results/frameworks/raw/openai-agents-disallow-interruptions.jsonl
    experiments/frameworks/openai_agents/.venv/bin/python experiments/frameworks/openai_agents/run.py --config handoff-tool --n 50 --seed 20260902 --out results/frameworks/raw/openai-agents-handoff-tool.jsonl

## Environments (`pip freeze` per venv)

### LiveKit Agents (Python) 1.7.1

Interpreter: `/Users/amirhosseinkazemkhani/dev/pt-frameworks/experiments/frameworks/livekit_1_7_1/.venv/bin/python (Python 3.12.12)`

```
aiofiles==25.1.0
aiohappyeyeballs==2.7.1
aiohttp==3.14.3
aiosignal==1.4.0
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.14.2
attrs==26.1.0
av==18.1.0
certifi==2026.7.22
cffi==2.1.1
charset-normalizer==3.5.1
click==8.5.0
colorama==0.4.6
distro==1.9.0
docstring-parser==0.18.0
eval-type-backport==0.4.0
frozenlist==1.8.0
googleapis-common-protos==1.75.2
grpcio==1.83.1
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.19
iniconfig==2.3.0
jiter==0.16.0
json-repair==0.60.1
livekit==1.1.15
livekit-agents==1.7.1
livekit-api==1.2.1
livekit-blingfire==1.1.0
livekit-local-inference==0.2.7
livekit-protocol==1.1.26
markdown-it-py==4.2.0
mdurl==0.1.2
multidict==6.7.1
nest-asyncio==1.6.0
numpy==2.5.2
openai==2.54.0
opentelemetry-api==1.44.0
opentelemetry-exporter-otlp==1.44.0
opentelemetry-exporter-otlp-proto-common==1.44.0
opentelemetry-exporter-otlp-proto-grpc==1.44.0
opentelemetry-exporter-otlp-proto-http==1.44.0
opentelemetry-proto==1.44.0
opentelemetry-sdk==1.44.0
opentelemetry-semantic-conventions==0.65b0
packaging==26.3
pluggy==1.6.0
prometheus-client==0.26.0
propcache==0.5.2
protobuf==7.36.1
psutil==7.2.2
pycparser==3.0
pydantic==2.13.5
pydantic-core==2.46.5
pygments==2.21.0
pyjwt==2.13.0
pytest==9.1.1
pyyaml==6.0.3
requests==2.34.2
rich==15.0.0
shellingham==1.5.4
sniffio==1.3.1
sounddevice==0.5.6
tqdm==4.70.0
typer==0.27.2
types-protobuf==7.35.1.20260827
typing-extensions==4.16.0
typing-inspection==0.4.4
urllib3==2.7.0
watchfiles==1.2.0
yarl==1.24.5
```

### LiveKit Agents (Python) 1.3.10

Interpreter: `/Users/amirhosseinkazemkhani/dev/pt-frameworks/experiments/frameworks/livekit_1_3_10/.venv/bin/python (Python 3.12.12)`

```
aiofiles==25.1.0
aiohappyeyeballs==2.7.1
aiohttp==3.13.3
aiosignal==1.4.0
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.14.2
attrs==26.1.0
av==18.1.0
certifi==2026.7.22
cffi==2.1.1
charset-normalizer==3.5.1
click==8.5.0
colorama==0.4.6
distro==1.9.0
docstring-parser==0.18.0
eval-type-backport==0.4.0
frozenlist==1.8.0
googleapis-common-protos==1.75.2
grpcio==1.83.1
h11==0.16.0
httpcore==1.0.9
httpcore2==2.12.0
httpx==0.28.1
httpx2==2.12.0
idna==3.19
importlib-metadata==8.7.1
iniconfig==2.3.0
jiter==0.16.0
livekit==1.0.23
livekit-agents==1.3.10
livekit-api==1.1.0
livekit-blingfire==1.1.0
livekit-protocol==1.1.1
markdown-it-py==4.2.0
mdurl==0.1.2
multidict==6.7.1
nest-asyncio==1.6.0
numpy==2.5.2
openai==2.15.0
opentelemetry-api==1.39.1
opentelemetry-exporter-otlp==1.39.1
opentelemetry-exporter-otlp-proto-common==1.39.1
opentelemetry-exporter-otlp-proto-grpc==1.39.1
opentelemetry-exporter-otlp-proto-http==1.39.1
opentelemetry-proto==1.39.1
opentelemetry-sdk==1.39.1
opentelemetry-semantic-conventions==0.60b1
packaging==26.3
pluggy==1.6.0
prometheus-client==0.26.0
propcache==0.5.2
protobuf==6.33.6
psutil==7.2.2
pycparser==3.0
pydantic==2.12.5
pydantic-core==2.41.5
pygments==2.21.0
pyjwt==2.13.0
pytest==9.1.1
requests==2.34.2
rich==15.0.0
shellingham==1.5.4
sniffio==1.3.1
sounddevice==0.5.6
tqdm==4.70.0
truststore==0.10.4
typer==0.27.2
types-protobuf==7.35.1.20260827
typing-extensions==4.16.0
typing-inspection==0.4.4
urllib3==2.7.0
watchfiles==1.2.0
yarl==1.24.5
zipp==4.1.0
```

### Pipecat 1.8.1

Interpreter: `/Users/amirhosseinkazemkhani/dev/pt-frameworks/experiments/frameworks/pipecat/.venv/bin/python (Python 3.12.12)`

```
aiofiles==25.1.0
aiohappyeyeballs==2.7.1
aiohttp==3.14.3
aiosignal==1.4.0
annotated-types==0.8.0
anyio==4.14.2
attrs==26.1.0
certifi==2026.7.22
click==8.5.0
cloudpickle==3.1.2
defusedxml==0.7.1
distro==1.9.0
docopt==0.6.2
docstring-parser==0.18.0
flatbuffers==25.12.19
frozenlist==1.8.0
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.19
jiter==0.16.0
joblib==1.6.0
llvmlite==0.49.0
loguru==0.7.3
loudness==0.2.0
markdown==3.10.3
mpmath==1.3.0
multidict==6.7.1
nltk==3.10.3
num2words==0.5.14
numba==0.67.0
numpy==2.5.2
onnxruntime==1.24.4
openai==2.54.0
packaging==26.3
pillow==12.3.0
pipecat-ai==1.8.1
propcache==0.5.2
protobuf==6.33.6
pydantic==2.13.5
pydantic-core==2.46.5
pyyaml==6.0.3
regex==2026.9.3
resampy==0.4.3
sniffio==1.3.1
soxr==1.0.0
sympy==1.14.0
tqdm==4.70.0
typing-extensions==4.16.0
typing-inspection==0.4.4
websockets==17.1
yarl==1.24.5
```

### OpenAI Agents SDK (Python) 0.22.0

Interpreter: `/Users/amirhosseinkazemkhani/dev/pt-frameworks/experiments/frameworks/openai_agents/.venv/bin/python (Python 3.12.12)`

```
annotated-types==0.8.0
anyio==4.14.2
attrs==26.1.0
certifi==2026.7.22
cffi==2.1.1
charset-normalizer==3.5.1
click==8.5.0
cryptography==50.0.1
griffelib==2.2.0
h11==0.16.0
httpcore2==2.12.0
httpx2==2.12.0
idna==3.19
jiter==0.16.0
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
mcp==2.1.1
mcp-types==2.1.1
openai==3.7.0
openai-agents==0.22.0
opentelemetry-api==1.44.0
pycparser==3.0
pydantic==2.13.5
pydantic-core==2.46.5
pyjwt==2.13.0
python-multipart==0.0.32
referencing==0.37.0
requests==2.34.2
rpds-py==2026.6.3
sniffio==1.3.1
sse-starlette==3.4.8
starlette==1.6.0
truststore==0.10.4
typing-extensions==4.16.0
typing-inspection==0.4.4
urllib3==2.7.0
uvicorn==0.52.4
websockets==16.1.1
```

