# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Mẫu điền (mỗi nhóm 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, run ID `E-seeded_regression-20261117-78f3` |
| Triệu chứng | Grader `db_fact_kept` FAIL + `pass_rate` FAIL (`status resolved != denied`). Reply "đã hoàn 20.000đ" nhưng `delay_days` bị lấy từ ticket (5) thay vì DB (2), nên khoản hoàn đáng lẽ phải là `denied`. |
| Bước sai đầu tiên | Event #3 `model_override` `{"delay_days": 5}` — ghi đè `delay_days=2` đọc được từ DB. |
| Vì sao là bước *đầu tiên* | Event #1 `handoff` và #2 `investigator` (chứa `lookup_order.delay_days=2`) đều đúng. Bước #3 ghi đè bằng số từ ticket là thời điểm đầu tiên dữ liệu bị bẻ. |
| Failure mode | FM-03 `ticket_trust` (`docs/failure_taxonomy.md`) |
| Đối chứng | `fixed C-002@11`: variant bật `no_override` nên **không** có event `model_override`; event #3 là `quote {"amount_vnd": 0, "reason": "no_entitlement"}` → `denied`, `db_fact_kept` PASS. |
| Regression test | `tests/test_lab9_trace_case.py::test_ticket_override_is_flagged` (grader `DbFactKept` bắt event `model_override`); fail trên `seeded_regression`, pass trên `fixed`. |
| Chưa chắc chắn | Nếu `delay_days` trong ticket trùng DB thì không có override, trace không phân biệt được; chưa đo trên LLM thật (MockLLM). |

> Điền số event và claim/seed thật từ `python -m lab9.trace_case`; "bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
