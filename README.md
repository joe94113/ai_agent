# AI Agent Helper

Conversational FastAPI tool that guides PB merchants through entering business hours, dining policies, and table mixes, then optionally simulates backend apply calls through Ollama-hosted Llama 3.1 models.

## Runtime Requirements

- Python 3.11+
- Ollama runtime with `llama3.1:8b-instruct-q4_K_M` (or compatible model) available at `http://127.0.0.1:11434`
- Python packages:
  - `fastapi`
  - `uvicorn[standard]`
  - `httpx`
  - `jinja2`
  - `itsdangerous`
  - `python-dotenv`
  - `pydantic`
  - `jsonschema`
  - `python-multipart`

Install them inside a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install fastapi uvicorn[standard] httpx jinja2 itsdangerous python-dotenv pydantic jsonschema python-multipart
```

Run the server:

```powershell
uvicorn app:app --reload
```

## Optional Training Stack

The `scripts/` folder contains utilities to create supervised datasets and LoRA adapters if you want to specialize the chat assistant:

- `scripts/make_alpaca.py` converts `setup_train.jsonl` into `data/train_alpaca.json`.
- `scripts/train_lora.py` trains a QLoRA adapter using Unsloth + TRL.

Training dependencies:

- `unsloth`
- `transformers`
- `datasets`
- `accelerate`
- `bitsandbytes`
- `peft`
- `trl`

Set up an isolated environment for training:

```powershell
python -m venv .venv-train
.\.venv-train\Scripts\Activate.ps1
pip install unsloth transformers datasets accelerate bitsandbytes peft trl
python scripts/make_alpaca.py
python scripts/train_lora.py
```

Export the resulting adapter to GGUF and build a custom Ollama model if desired:

```powershell
python -m unsloth.export gguf --adapter checkpoints/pb-lora --output adapters/pb-lora.gguf
ollama create pb-helper -f Modelfile
ollama run pb-helper
```

## Project Structure

- `app.py` – FastAPI app that powers the web UI and Ollama interactions.
- `ai_agent.py` – API-only suggestion endpoint with JSON schema validation.
- `setup_train.jsonl` / `setup_rag.jsonl` – seed data for few-shot prompts and RAG evidence.
- `scripts/` – dataset preparation and LoRA training helpers.

## Environment Variables

Create a `.env` file with at least:

```
PB_SIGNER_SECRET=<32+ char secret used for session cookies>
```

With these dependencies installed, any contributor can boot the FastAPI UI or run optional fine-tuning without hunting through prior shell history.
