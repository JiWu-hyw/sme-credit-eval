"""SciBERT 语义向量化加载器（模块一：非结构化文本 → 向量）。

模型：allenai/scibert_scivocab_uncased，已离线固化在
``data/raw/scibert_scivocab_uncased/``（含 config.json / vocab.txt /
special_tokens_map.json / pytorch_model.bin）。

重要：本项目固定 torch==2.5.1+cu121（<2.6）。transformers >= 4.5x 对
torch<2.6 会拒绝加载 legacy ``.bin`` 权重（CVE-2025-32434 安全限制）。
因此这里直接用 ``torch.load(weights_only=False)`` 载入，并剥离 SciBERT
checkpoint 的 ``bert.`` 前缀 / 丢弃 ``cls.`` 掩码语言头，映射到裸 ``BertModel``。
"""
import os
import torch
from collections import OrderedDict
from transformers import AutoConfig, AutoTokenizer, AutoModel

_DEFAULT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "raw", "scibert_scivocab_uncased")
)


def load_scibert(model_dir: str = _DEFAULT_DIR, device: str = "cpu"):
    """返回 (model, tokenizer)。model 为 eval 模式并已搬到 device。"""
    cfg = AutoConfig.from_pretrained(model_dir)
    model = AutoModel.from_config(cfg)
    sd = torch.load(
        os.path.join(model_dir, "pytorch_model.bin"),
        map_location=device,
        weights_only=False,
    )
    # SciBERT 的 .bin 是完整 BertForMaskedLM：键带 'bert.' 前缀且含 'cls.' 头
    clean = OrderedDict((k[5:], v) for k, v in sd.items() if k.startswith("bert."))
    model.load_state_dict(clean)
    model.eval()
    tok = AutoTokenizer.from_pretrained(model_dir)
    if device not in ("cpu", None):
        model.to(device)
    return model, tok


def encode_text(model, tok, texts, device: str = "cpu", max_length: int = 128):
    """批量编码，返回最后一层隐藏态 (B, L, 768)。"""
    if isinstance(texts, str):
        texts = [texts]
    enc = tok(
        texts,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    enc = {k: v.to(device) for k, v in enc.items()}
    with torch.no_grad():
        return model(**enc)[0]


if __name__ == "__main__":
    m, t = load_scibert(device="cpu")
    v = encode_text(m, t, ["科技型中小企业 信用评估", "发明专利 授权率"], max_length=64)
    print("loaded SciBERT | emb shape =", tuple(v.shape))
