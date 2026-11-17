# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5`

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | CÓ | **dùng** (không làm gate độc lập) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | KHÔNG (κ 0.375 < 0.6, R 0.300 < 0.7) | **loại** |

Ma trận nhầm lẫn (FAIL = positive class):

| Tiêu chí | tp | fp | fn | tn | fail rate quan sát | fail rate hiệu chỉnh (Rogan–Gladen) |
|---|---|---|---|---|---|---|
| tone_ok | 24 | 4 | 6 | 66 | 0.280 | 0.300 |
| helpful | 9 | 0 | 21 | 70 | 0.090 | 0.300 |

## Vì sao `helpful` bị loại

Judge `helpful` chỉ chấm giọng điệu/độ đầy đủ của mẫu câu, **không** đối chiếu số tiền trong reply với ledger. Toàn bộ 21 case nó bỏ sót (`fn`) đều là "sai tiền" — reply nói một con số, ledger ghi con số khác. Vì tiền phải chấm bằng code (xem `docs/failure_taxonomy.md`, FM-08), một judge không đọc ledger không thể làm gate cho bài toán hoàn tiền. κ = 0.375 và R = 0.300 đều dưới sàn → loại.

## ≥5 case audit thủ công

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha" | PASS | FAIL | Người | Xưng "em"/"nha" suồng sã với khách; rubric phải cấm xưng hô thân mật. |
| 2 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm" | PASS | FAIL | Người | Giọng ra lệnh ("không cần nhắn thêm"); judge chỉ thấy "Dạ, vui lòng". Thêm tiêu chí "không ra lệnh khách". |
| 3 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Ngắn nhưng đủ ý; bỏ yêu cầu "bắt buộc câu xin lỗi". |
| 4 | H-051 "…đã refund 90.000đ…" | FAIL | PASS | Người | CS cho phép từ "refund"; bỏ phạt tiếng Anh khi chỉ là từ vựng. |
| 5 | H-094 "Da, LogiX da hoan … xin loi vi su bat tien" | PASS | FAIL | Người | Mất dấu tiếng Việt do dịch máy; thêm tiêu chí chính tả/dấu. |
| 6 | HL-015 "hoàn 55.000đ" (ledger 40.000đ) | PASS | FAIL | Người | Sai tiền; judge helpful không đọc ledger → cần code grader (FM-08), không phải sửa judge. |
| 7 | HL-020 "90.000đ" (ledger 120.000đ) | PASS | FAIL | Người | Sai tiền; cùng kết luận như trên. |

## Kết luận

- **Loại `helpful`**: độ chính xác hoàn hảo (P=1.0) là ảo — nó chỉ đúng khi câu đúng kiểu mẫu; recall 0.3 chứng tỏ nó bỏ sót 21/30 lỗi, toàn bộ là lỗi tiền (FM-08). Không dùng làm gate.
- **`tone_ok` dùng được** cho lớp "giọng điệu" sau khi sửa rubric (bỏ phạt thiếu xin lỗi/từ "refund", thêm cấm xưng hô suồng sã + ra lệnh + lỗi dấu). Vẫn chỉ là judge phụ, không bao giờ chấm tiền/approval.
- **Rủi ro còn lại**: cả hai judge chỉ được kiểm trên dữ liệu replay đã công khai (100 dòng); cần nhãn người mới để có held-out thật trước khi dùng trong quyết định merge.
