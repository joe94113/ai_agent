# RWG Thesis Agent Bundle (Merchant-Friendly Prompts)

這個版本保留原本的內部 JSON schema、policy agent / FSM 架構與 OLLAMA 可切換 extractor，
但把互動層改成較適合面對商家使用者的口語問法。

## 主要改動

- 不再顯示內部 slot 名稱，例如 `[default_policy]`
- 解析失敗時會原地重問同一題，不會跳去下一題
- 將工程化輸入改成商家比較能理解的問法
  - `default_policy` → 「一般時段線上訂位大概占多少位置？」
  - `time_block_overrides` → 「假日晚餐不開線上，只接現場」
  - `service_scheduling_rules` → 「訂位 2 小時前、取消前一天」
  - `no_show_tolerance` / `popularity` → A/B/C 或自然語言皆可

## 執行方式

```bash
python -m rwg_thesis_agent.app policy --extractor rule
python -m rwg_thesis_agent.app policy --extractor ollama
python -m rwg_thesis_agent.app policy --extractor auto
```

## 例子

- 一般時段線上策略
  - `A` / `大部分都給線上`
  - `B` / `一半左右`
  - `C` / `少量就好`
  - `D` / `平常不開放線上`

- 忙時特殊規則
  - `假日晚餐不開線上，只接現場`
  - `平日中午多開一點線上`
  - `沒有`

- 最晚訂位 / 取消規則
  - `訂位 2 小時前、取消前一天`
  - `B D`

