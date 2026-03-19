# RWG Thesis Agent Bundle（論文用：可比較的對話式設定 Agent）

這個資料夾是一個「對話式填表 + 規則檢查」的小型框架，用來模擬商家在設定 Google 訂位/餐廳線上預訂時，Agent 如何一步步把資訊問齊、即時檢查矛盾，最後輸出可被後端吃進去的 JSON。

白話講：

- 你可以把它想成「訂位設定導覽員」：它會一直問你問題（桌型、用餐時間、可訂時段、忙時規則…），把你的回答整理成結構化設定。
- 它不是只做 prompt 抽 JSON：它有狀態（state）、有流程（FSM 或動態策略）、有約束（constraints），並且能跑 benchmark 比較兩種問法/策略。
- 解析使用者回答的方式可以切換：先用本地規則解析；必要時才呼叫本機 OLLAMA 來做更難的抽取。

## 檔案結構

- `rwg_thesis_agent/app.py`：CLI 入口（互動 demo / eval）
- `rwg_thesis_agent/state_tracker.py`：狀態（state）與 slot 規格（哪些是 core/policy/optional、是否必填）
- `rwg_thesis_agent/prompt_handlers.py`：每個 slot 要怎麼問（問句）+ 本地規則解析（例如「2人桌4張」→ JSON）
- `rwg_thesis_agent/extractors.py`：抽取器切換（rule / ollama / auto），以及 OLLAMA 呼叫與 JSON 解析
- `rwg_thesis_agent/constraints.py`：約束檢查（衝突/警告）、條件欄位推導（例如自動推導線上可訂時段、推 max_party_size）與 feed readiness
- `rwg_thesis_agent/builders.py`：把 state 組成一份「後端可用」的內部輸出 JSON（含視覺化 payload、feed job input）
- `rwg_thesis_agent/simulation.py`：簡化模擬器（用來產生建議文字，非核心可執行規則）
- `rwg_thesis_agent/baseline_fsm.py`：Baseline：固定順序問問題（FSM）
- `rwg_thesis_agent/policy_agent.py`：Policy 版：動態決定下一題（優先把可生成 feed 的核心資料問齊）
- `rwg_thesis_agent/evaluation.py`：內建 scenario benchmark（比較兩種 agent 的平均輪數/衝突率/可生成率）

## 它怎麼跑（白話流程）

1. 載入一份「預載商家資料（唯讀）」：例如店名、電話、營業時間（`merchant_context`）
2. Agent 依策略挑一個 slot 來問（固定順序或動態策略）
3. 你回答後，系統把回答抽成該 slot 的結構化值
4. 每輪都跑 constraint：如果有矛盾（例如線上可訂時段超出營業時間）就立刻提醒
5. 全部問完（或 policy 版判定已足夠生成 feed），輸出一份整合 JSON

你會在輸出裡看到三塊重點：

- `reservation_settings`：設定本體（桌型、時段、策略、override…）
- `laravel_visual_payload`：給後台 UI 顯示用的整理版資料
- `daily_feed_job_input`：模擬後端生成 feed job 會吃的 input

## 執行方式

### 規則版

```bash
python -m rwg_thesis_agent.app fsm --extractor rule
python -m rwg_thesis_agent.app policy --extractor rule
python -m rwg_thesis_agent.app eval --extractor rule
```

提示：`fsm` 會照固定順序一直問到全部完整；`policy` 會偏向先問「能讓設定可執行/可生成 feed」的核心資料，降低輪數。

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

補充：目前 `ollama` 與 `auto` 都是「先本地規則解析，失敗才呼叫 OLLAMA」的策略（避免簡單答案也去打模型）。

## OLLAMA 設定

預設：
- URL: `http://localhost:11434/api/chat`
- Model: `llama3.1:8b-instruct-q4_K_M`

可用環境變數覆寫：

### PowerShell（Windows）

```powershell
$env:RWG_OLLAMA_URL = "http://localhost:11434/api/chat"
$env:RWG_OLLAMA_MODEL = "llama3.1:8b-instruct-q4_K_M"
```

### Bash（macOS / Linux）

```bash
export RWG_OLLAMA_URL=http://localhost:11434/api/chat
export RWG_OLLAMA_MODEL=llama3.1:8b-instruct-q4_K_M
```

## 論文定位

你可以把研究主軸描述成：

> 在 Google 預訂設定情境下，設計一個 constraint-aware、mixed-initiative conversational configuration agent，
> 讓商家能用更少輪數完成可執行設定，並降低後續人工修改與 feed generation 的錯誤。

## 想改行為要看哪裡？

- 想調整「每題怎麼問、怎麼解析」：改 `prompt_handlers.py`
- 想調整「何時算矛盾/警告、哪些欄位可推導」：改 `constraints.py`
- 想調整「輸出 JSON 長相」：改 `builders.py`
- 想調整「哪些欄位算核心、policy 版怎麼排序」：改 `state_tracker.py`、`policy_agent.py`
- 想比較不同策略表現：跑 `python -m rwg_thesis_agent.app eval --extractor rule`
