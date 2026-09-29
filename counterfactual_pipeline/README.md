# 🧠 GIPS-DoRA: Counterfactual Generation Pipeline (P & G)

> **Dự án**: *GIPS-DoRA — Generator-Invariant Provenance Subspace Guided Weight-Decomposed Low-Rank Adaptation*  
> **Mục tiêu**: Xây dựng kho dữ liệu đối ứng AI (AI Counterfactuals) phục vụ hai bài toán cốt lõi:
> 1. **Khám phá không gian hình học chung ($Q_{\text{GIPS}}$ via SVD)**: Phân tích vector biến đổi $H \to P$ và $H \to G$ trên nhiều họ generator.
> 2. **Huấn luyện mô hình phát hiện phân đoạn mức câu (Sequence Labeling with CRF)**: Nhận diện chính xác ranh giới $H H H \mid P P \mid H \mid G G$ và tính toán tỷ lệ AI-generated ($r_G$) cùng AI-assisted ($r_{\text{AI}}$).

---

## 1. Cơ sở lý thuyết & Kiến trúc Dữ liệu

Dựa trên đặc tả chuẩn tại [`overall_architect.md`](../docs/overall_architect.md):

```
                   Human source H (Đoạn trích luận văn tinh tuyển)
                                   │
               ┌───────────────────┴───────────────────┐
               │                                       │
           AI Polish                               Semantic Skeleton
      (P-light, P-medium, P-heavy)            (Entities, Assertions, Numbers)
               │                                       │
               ▼                                       ▼
               P                                 Multiple Generators
                                                (Gemini, GPT, Qwen, LLaMA)
                                                       │
                                                       ▼
                                                       G
```

### 1.1 Nhãn và Nhiệm vụ
- **$H$ (Label 0 - Human)**: Đoạn văn nguyên bản từ khoá luận tốt nghiệp CNTT sau khi chuẩn hoá bảo toàn nội dung.
- **$P$ (Label 1 - AI Polish)**: Hiệu đính từ văn bản nguồn $H$ theo 3 mức can thiệp:
  - **`P-light`**: Sửa chính tả, dấu câu, chuẩn hóa từ ngữ; **bảo toàn nguyên vẹn 100% cấu trúc câu và số lượng câu**.
  - **`P-medium`**: Viết lại ở mức câu cho mượt mà, chuẩn văn phong học thuật tiếng Việt; cho phép gộp/tách câu nhưng giữ nguyên luận điểm.
  - **`P-heavy`**: Tái cấu trúc hoàn toàn phong cách hành văn và tổ chức đoạn; bảo toàn đầy đủ các thực thể kỹ thuật, số liệu và mối quan hệ logic.
- **$G$ (Label 2 - AI Generation)**: **Tuyệt đối không truyền văn bản gốc $H$**. Trích xuất **Semantic Skeleton** (Khung ngữ nghĩa) gồm:
  - Chuyên ngành & Tiêu đề luận văn
  - Thực thể kỹ thuật trọng tâm (`key_entities`)
  - Luận điểm / sự kiện kỹ thuật (`core_assertions`)
  - Ràng buộc số liệu / thông số (`numeric_constraints`)
  - Ngưỡng độ dài mục tiêu (`target_tokens`, `target_sentences`)  
  Generator sẽ tự do hành văn và liên kết ý độc lập hoàn toàn, triệt tiêu hiện tượng rò rỉ bề mặt (surface leakage).

---

## 2. Cấu trúc Mô-đun (`counterfactual_pipeline/`)

```
counterfactual_pipeline/
├── schemas/
│   ├── semantic_skeleton.schema.json      # Schema khung ngữ nghĩa
│   ├── counterfactual_record.schema.json  # Schema mẫu dữ liệu P & G
│   └── hybrid_sequence.schema.json        # Schema chuỗi câu hỗn hợp (CRF)
├── providers/
│   ├── base.py                            # Lớp cơ sở: rate-limit, backoff, retry
│   ├── mock_provider.py                   # Provider giả lập phục vụ unit test & CI
│   ├── gemini_provider.py                 # Google Gemini API (REST qua httpx)
│   └── openai_compatible.py               # OpenAI, OpenRouter, Qwen, DeepSeek, vLLM
├── skeleton_extractor.py                  # Bộ bóc tách thực thể & thông số kỹ thuật
├── prompt_templates.py                    # Khung prompt chuẩn hóa kèm mã băm SHA256
├── sentence_aligner.py                    # Tách câu, khử markdown ticks, đánh số ordinal
├── generator.py                           # Động cơ sinh dữ liệu, checkpointing, chống trùng lặp
├── hybrid_synthesizer.py                  # Ghép nối chuỗi nhãn hỗn hợp H/P/G cho CRF
├── validate_counterfactuals.py            # Bộ kiểm định toàn vẹn (Split isolation, schemas)
├── run_counterfactual_pilot.py            # CLI Runner cho thực nghiệm Pilot & Batch
└── counterfactual_generator_colab.ipynb   # Notebook chạy trên Google Colab
```

---

## 3. Hướng dẫn Thực thi (Quickstart)

### 3.1 Chạy thử nghiệm giả lập (Mock Offline - Tốc độ cao, không tốn API key)
Chạy pilot 20 chunks mẫu (10 train, 5 dev, 5 test) để kiểm chứng luồng hoạt động:
```bash
python counterfactual_pipeline/run_counterfactual_pilot.py \
    --provider mock \
    --n_train 10 \
    --n_dev 5 \
    --n_test 5 \
    --synthesize_hybrids
```

### 3.2 Chạy với Google Gemini API
```bash
python counterfactual_pipeline/run_counterfactual_pilot.py \
    --provider gemini \
    --model_name gemini-1.5-flash \
    --api_key "AIzaSy..." \
    --n_train 50 \
    --n_dev 20 \
    --n_test 20 \
    --synthesize_hybrids
```

### 3.3 Chạy với OpenAI (GPT-4o-mini)
```bash
python counterfactual_pipeline/run_counterfactual_pilot.py \
    --provider openai \
    --model_name gpt-4o-mini \
    --api_key "sk-..." \
    --n_train 50 \
    --n_dev 20 \
    --n_test 20
```

### 3.4 Chạy với OpenRouter (Qwen2.5, DeepSeek, LLaMA-3.1)
```bash
python counterfactual_pipeline/run_counterfactual_pilot.py \
    --provider openrouter \
    --model_name qwen/qwen-2.5-72b-instruct \
    --api_key "sk-or-..." \
    --n_train 50 \
    --n_dev 20 \
    --n_test 20
```

---

## 4. Kiểm định Toàn vẹn Dữ liệu (Validation Suite)

Để kiểm tra tính cách ly split (Zero cross-split leakage) và tính hợp lệ của nhãn:
```bash
python counterfactual_pipeline/validate_counterfactuals.py \
    --dataset ai_counterfactual_dataset_v1/counterfactual_records.jsonl \
    --human_splits human_written_dataset_v2_16_paper/gips_curated/splits
```

Kết quả kiểm định chuẩn:
- `Split Mismatches`: 0
- `Duplicate Records`: 0
- `Empty Texts`: 0
- `Sentence Ordinal Errors`: 0
- `Valid`: `True`
