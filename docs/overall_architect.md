# Overall Architect: GIPS-DoRA

> Trạng thái: Architecture freeze v1  
> Phạm vi: phát hiện và phân đoạn H/P/G trong luận văn CNTT tiếng Việt  
> Tên phương pháp: **GIPS-DoRA — Generator-Invariant Provenance Subspace Guided Weight-Decomposed Low-Rank Adaptation**

## 1. Câu hỏi khoa học

> Human → AI transformation có tạo ra một low-dimensional provenance subspace chung giữa nhiều generator hay không? Nếu có, có thể ép PEFT chỉ thích nghi theo subspace đó để tăng khả năng tổng quát hóa sang generator chưa từng thấy hay không?

Novelty chính là **GIPS-DoRA**. Quantization chỉ là lớp tối ưu hiệu năng/VRAM và không nằm trong tên phương pháp.

## 2. Kiến trúc tổng thể

```text
╔══════════════════════════════════════════════════════════╗
║                  DATA / COUNTERFACTUALS                  ║
╚══════════════════════════════════════════════════════════╝

500+ Vietnamese CS theses
              │
       filter + clean + dedup
              │
   source-document split FIRST
              │
              ▼
        Human source H
              │
     ┌────────┴────────────────────┐
     │                             │
 AI polish                       semantic skeleton
 light/medium/heavy              facts + relations
     │                             │
     ▼                             ▼
     P                 multiple generator families
                                 │
                       G₁ G₂ G₃ G₄ ...


╔══════════════════════════════════════════════════════════╗
║                 OFFLINE GEOMETRY STAGE                   ║
╚══════════════════════════════════════════════════════════╝

Frozen BF16/FP16 backbone
      │
H/P/G representations
      │
same-source differences
      │
per-generator SVD
      │
Q₁ Q₂ Q₃ Q₄
      │
singular spectrum + principal-angle analysis
      │
consensus projection
      │
      ▼
Generator-Invariant Provenance Subspace
Q_GIPS


╔══════════════════════════════════════════════════════════╗
║                    PEFT TRAINING STAGE                   ║
╚══════════════════════════════════════════════════════════╝

Quantized/frozen backbone
          │
selected q/k/v/o layers
          │
          ▼
       GIPS-DoRA

W' = m · normalize(V + B · A_GIPS)

A_GIPS = fixed
B      = trainable
m      = trainable

          │
          ▼
sentence attention pooling
          │
1–2 layer bidirectional sentence Transformer
          │
          ▼
       H / P / G emissions
          │
         CRF
          │
          ▼
full-document provenance map


╔══════════════════════════════════════════════════════════╗
║                         OUTPUT                           ║
╚══════════════════════════════════════════════════════════╝

H H H | P P | H | G G G | H
      ↑     ↑   ↑       ↑

sentence/span labels
multi-boundaries
AI-generated ratio
AI-assisted ratio
confidence
```

## 3. Nhãn và nhiệm vụ

- `H`: Human-written nguyên bản.
- `P`: Human-written được AI polish.
- `G`: AI-generated độc lập từ semantic skeleton.

Mô hình thực hiện sequence labeling ở mức câu. Ranh giới được suy ra từ chuyển trạng thái nhãn, không cần boundary head riêng:

```text
H H H | P P | H | G G
```

Các tỷ lệ được suy ra trực tiếp từ chuỗi nhãn, có trọng số theo độ dài:

```text
r_G = total_length(G) / total_length
r_AI_assisted = [total_length(P) + total_length(G)] / total_length
```

Độ dài mặc định dùng số ký tự Unicode hoặc số token, nhưng phải chọn một cách duy nhất trước khi báo cáo kết quả.

## 4. Counterfactual data construction

### 4.1 Human source H

`H` là đoạn văn nguyên bản sau quy trình extraction và normalization bảo toàn nội dung. Không dùng LLM để sửa chính tả, viết lại hoặc làm mượt nhánh Human.

Mỗi source passage phải có định danh bất biến:

```text
source_document_id
section_id
passage_id
sentence_ids
character offsets
content hash
```

### 4.2 AI-polished P

`P` giữ nguyên source text làm đầu vào và chia ba mức can thiệp:

```text
P-light
- sửa ngữ pháp, dấu câu, lựa chọn từ
- hạn chế thay đổi cấu trúc câu

P-medium
- viết lại ở mức câu
- cho phép gộp/tách câu nhưng giữ luận điểm

P-heavy
- tái cấu trúc đoạn
- giữ facts, entities, figures và quan hệ cốt lõi
```

Trajectory nghiên cứu:

```text
H → P-light → P-medium → P-heavy → G
```

Không mặc định DoRA magnitude tương ứng với mức can thiệp. Chỉ đưa ra kết luận sau khi đo angle, norm và correlation.

### 4.3 AI-generated G

Không đưa nguyên văn đoạn H cho model rồi yêu cầu rewrite; đó là P, không phải G.

Quy trình G:

```text
Human passage
    ↓
semantic skeleton
    ├─ topic
    ├─ section title
    ├─ key entities
    ├─ technical facts
    ├─ claims
    ├─ relationships
    └─ constraints/numeric values
    ↓
general-purpose generator
    ↓
independently realized Vietnamese academic passage
```

Mục tiêu là giữ semantic content có kiểm soát nhưng cho generator tự tạo surface realization.

## 5. PairDiff và GIPS

Với source passage `s` và generator family `g`:

```text
d_(s,g) = h(G_(s,g)) - h(H_s)
```

Đây là Human → AI transformation vector. Pairing phải đúng cùng source; không ghép ngẫu nhiên các chủ đề khác nhau.

Với từng generator:

```text
D_g = [d_(1,g), d_(2,g), ..., d_(n,g)]
D_g = U_g Σ_g V_g^T
Q_g = top-r right singular directions
```

Mỗi `Q_g` mô tả provenance subspace của một generator family.

Consensus geometry:

```text
P_g = Q_g Q_g^T
M   = (1 / number_of_generators) · Σ_g P_g
Q_GIPS = TopEig(M, r)
```

Trực giác:

```text
Qwen provenance      ───┐
                        │
Llama provenance     ───┼──► shared/common provenance directions
                        │
DeepSeek provenance  ───┤
                        │
GPT-family provenance ───┘
```

- Direction chỉ có ở một generator có khả năng là generator fingerprint và bị giảm trọng số.
- Direction lặp lại ở nhiều family được giữ làm GIPS.

PairDiff tạo các vector hình học; GIPS tìm phần hình học chung giữa các generator.

## 6. Geometry discovery trước adapter training

Không quantize backbone trong lần khám phá geometry chính. Dùng frozen BF16/FP16 để tránh nhiễu lượng tử làm méo singular spectrum và principal angles.

### 6.1 Low-rank hypothesis

```text
E(r) = Σ_(i=1..r) σ_i² / Σ_i σ_i²
```

Báo cáo `E(r)` cho `r ∈ {4, 8, 16, 32, 64}` và effective rank. Không chọn rank chỉ dựa trên test set.

### 6.2 Generator invariance

Đo principal angles giữa mọi cặp:

```text
θ(Q_Qwen, Q_Llama)
θ(Q_Qwen, Q_DeepSeek)
θ(Q_Llama, Q_DeepSeek)
...
```

So sánh với null distributions từ shuffled pairing và random subspaces cùng chiều.

### 6.3 Topic invariance

Tái dựng subspace theo các nhóm chủ đề, ví dụ:

```text
Software Engineering
Cybersecurity
Artificial Intelligence
Networking
Information Systems
```

Đo principal angles và subspace similarity giữa các chủ đề. Mục tiêu là xác định provenance directions có ổn định khi topic thay đổi hay không.

### 6.4 Pairing control

So sánh:

```text
Correct:  H(doc17, passage4) ↔ AI(doc17, passage4)
Shuffled: H(doc17, passage4) ↔ AI(doc283, passage2)
```

Correct pairing phải tốt hơn shuffled pairing trên geometry quality và unseen-generator detection. Đây là causal control quan trọng để loại confound chủ đề.

## 7. GIPS-DoRA

DoRA tách weight thành magnitude và direction. Standard form:

```text
W' = m · normalize(V + BA)
```

GIPS-DoRA cố định nhánh `A` theo provenance subspace:

```text
A = A_GIPS = fixed
W' = m · normalize(V + B · A_GIPS)
```

Trainable:

```text
B            ← train
magnitude m  ← train
A_GIPS       ← frozen
backbone     ← frozen
```

Thông điệp phương pháp:

```text
DoRA = HOW to adapt
GIPS = WHERE to adapt
```

GIPS giới hạn directional update trong không gian mà nhiều generator cùng thể hiện Human → AI change; DoRA học cường độ directional update và magnitude.

### 7.1 Điều kiện tương thích chiều

Representation-space GIPS không tự động có cùng basis/shape với mọi LoRA `A` matrix. Trước implementation phải định nghĩa phép ánh xạ chính xác từ `Q_GIPS` sang input subspace của từng target module, ví dụ:

- khám phá layer-wise GIPS tại chính hidden input của từng `q/k/v/o` module; hoặc
- học/fix một orthogonal projector từ shared representation space sang module input space.

Không được chỉ gán một `Q_GIPS` toàn cục vào mọi `A` nếu shape và semantic space không tương thích.

## 8. Quantization

Quantization là efficiency experiment:

```text
Geometry discovery:
BF16/FP16 frozen backbone

Detector training/inference:
BF16 GIPS-DoRA
vs 8-bit GIPS-DoRA
vs 4-bit NF4 GIPS-DoRA
```

Adapter computation giữ BF16. Ghi nhận VRAM, throughput, latency và chênh lệch chất lượng. Paper vẫn gọi phương pháp là GIPS-DoRA.

## 9. Detector head

Giữ head đơn giản để novelty tập trung vào provenance geometry:

```text
tokens
  ↓
frozen/quantized backbone + GIPS-DoRA
  ↓
sentence attention pooling
  ↓
s₁, s₂, ..., sₙ
  ↓
1–2 layer bidirectional sentence Transformer
  ↓
Linear(H/P/G)
  ↓
CRF
```

Không đưa vào main model:

```text
DETR
separate boundary head
separate ratio head
handcrafted style features
separate document head
```

Các thành phần bổ sung chỉ được cân nhắc sau khi main hypothesis đã được kiểm chứng và phải xuất hiện như extension/ablation, không làm loãng câu chuyện GIPS.

## 10. Generator protocol

### Pilot

```text
3 reference families
+ 1 completely held-out family
```

### Full experiment

```text
4 reference families
+ 1–2 unseen families
```

Các reference phải khác family, không phải nhiều checkpoint gần nhau của cùng một family.

Held-out generator tuyệt đối không tham gia:

- GIPS construction;
- adapter training;
- threshold/calibration tuning;
- prompt-template selection dựa trên kết quả test.

Codex không dùng làm generator prose chính. Có thể dùng làm hard OOD stress test cho các đoạn giải thích code, thuật toán, triển khai kỹ thuật và kiến trúc phần mềm.

## 11. Baselines

```text
TF-IDF + SVM
TextCNN
BiLSTM
Frozen backbone + linear/CRF
LoRA
DoRA
CorDA hoặc task-aware LoRA
PairDiff-LoRA
GIPS-LoRA
GIPS-DoRA        ← proposed
```

Efficiency comparison:

```text
GIPS-DoRA BF16
GIPS-DoRA int8
GIPS-DoRA NF4/int4
```

## 12. Ablations bắt buộc

### Geometry

- Correct pairing vs shuffled pairing.
- Per-generator PairDiff vs GIPS consensus.
- GIPS rank `r ∈ {4, 8, 16, 32, 64}`.
- Layer-wise GIPS vs one global GIPS.
- Một, hai, ba và bốn reference generator families.
- GIPS có/không có P trajectories.

### Adaptation

- Standard LoRA vs standard DoRA.
- GIPS-LoRA vs GIPS-DoRA.
- GIPS-DoRA với magnitude frozen vs trainable.
- Random frozen A vs PCA A vs per-generator A vs consensus GIPS A.
- q/v-only vs q/k/v/o target modules.

### Detector

- No Bi-Sentence Transformer vs 1 layer vs 2 layers.
- Linear decoding vs CRF.
- Sentence mean pooling vs attention pooling.
- Context length/chunk overlap sensitivity.

### Generalization

- Seen generator.
- Unseen checkpoint cùng family.
- Unseen generator family.
- Unseen topic.
- Unseen university/source.
- AI-polish intensity shift.
- Codex technical-prose hard test.

## 13. Main success criteria

GIPS hypothesis chỉ được xem là được hỗ trợ khi đồng thời có:

1. Transformation matrices có singular spectrum tập trung hơn null/shuffled controls.
2. Principal angles giữa generator subspaces nhỏ hơn random/shuffled controls.
3. GIPS cải thiện unseen-family performance so với PairDiff/per-generator subspace.
4. Correct source pairing tốt hơn shuffled pairing.
5. GIPS-DoRA cải thiện OOD mà không đánh đổi quá lớn in-domain performance.

Nếu các điều kiện trên không đạt, không gọi shared provenance geometry là kết luận; chuyển GIPS thành negative result/analysis và dùng DoRA/LoRA làm detector baseline.

## 14. Paper story

1. Kiểm tra Human → AI transformations có low-rank geometry hay không.
2. Kiểm tra các generator có shared provenance subspace hay không.
3. Xây GIPS từ phần geometry chung.
4. Dùng GIPS để constrain directional branch của DoRA.
5. Quantize frozen backbone để giảm VRAM.
6. Đánh giá trên generator family hoàn toàn chưa từng thấy.

## 15. Tài liệu tham khảo kỹ thuật

- [DoRA: Weight-Decomposed Low-Rank Adaptation](https://proceedings.mlr.press/v235/liu24bn.html)
- [Hugging Face PEFT: DoRA](https://huggingface.co/docs/peft/main/en/package_reference/lora_variant_dora)

