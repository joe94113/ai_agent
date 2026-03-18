# RWG Thesis Agent Bundle (Rule-based + OLLAMA switchable)

這包是為了論文而整理的可比較架構，重點不是只有 prompt 抽 JSON，而是：

- Fixed FSM baseline
- Dynamic question policy agent
- State tracking
- Constraint checking
- Executable reservation overrides
- Evaluation harness
- **可切換 rule-based / OLLAMA extractor**

## 檔案結構

- `rwg_thesis_agent/baseline_fsm.py`：固定順序 baseline
- `rwg_thesis_agent/policy_agent.py`：動態 question policy 版本
- `rwg_thesis_agent/state_tracker.py`：slot 狀態追蹤
- `rwg_thesis_agent/constraints.py`：業務約束與 feed readiness
- `rwg_thesis_agent/simulation.py`：簡化模擬器
- `rwg_thesis_agent/builders.py`：Laravel 內部 JSON 組裝
- `rwg_thesis_agent/prompt_handlers.py`：每個 slot 的 prompt 與 rule parser
- `rwg_thesis_agent/extractors.py`：可切換 rule-based / OLLAMA 的 extractor
- `rwg_thesis_agent/evaluation.py`：benchmark / evaluation harness
- `rwg_thesis_agent/app.py`：CLI demo

## 執行方式

### 規則版

```bash
python -m rwg_thesis_agent.app fsm --extractor rule
python -m rwg_thesis_agent.app policy --extractor rule
python -m rwg_thesis_agent.app eval --extractor rule
```

### OLLAMA 版

先確認本機 OLLAMA 有啟動，且模型存在：

```bash
ollama run llama3.1:8b-instruct-q4_K_M
```

再執行：

```bash
python -m rwg_thesis_agent.app policy --extractor ollama
```

或自動回退：

```bash
python -m rwg_thesis_agent.app policy --extractor auto
```

## OLLAMA 設定

預設：
- URL: `http://localhost:11434/api/chat`
- Model: `llama3.1:8b-instruct-q4_K_M`

可用環境變數覆寫：

```bash
export RWG_OLLAMA_URL=http://localhost:11434/api/chat
export RWG_OLLAMA_MODEL=llama3.1:8b-instruct-q4_K_M
```

## 論文定位

你可以把研究主軸描述成：

> 在 Google 預訂設定情境下，設計一個 constraint-aware、mixed-initiative conversational configuration agent，
> 讓商家能用更少輪數完成可執行設定，並降低後續人工修改與 feed generation 的錯誤。
