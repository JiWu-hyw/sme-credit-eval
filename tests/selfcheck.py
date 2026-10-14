import sys, importlib
print('python', sys.version.split()[0])
mods = ['torch','torch_geometric','torchvision','transformers','numpy','pandas','scipy',
        'networkx','sklearn','shap','xgboost','fastapi','uvicorn','matplotlib',
        'openpyxl','docx','sentencepiece','tokenizers','jupyter']
ok=0; fail=0
for m in mods:
    try:
        mod=importlib.import_module(m)
        print(f'OK   {m:14s} {getattr(mod,"__version__","?")}')
        ok+=1
    except Exception as e:
        print(f'FAIL {m:14s} {e}')
        fail+=1

import torch
print('-'*40)
print('torch cuda=', torch.version.cuda, '| gpu_avail=', torch.cuda.is_available(),
      '| device=', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')

# SciBERT via torch.load (torch<2.6 cannot load .bin through transformers)
import os
from collections import OrderedDict
from transformers import AutoConfig, AutoTokenizer, AutoModel
ld = r'C:\Users\Rocky\WorkBuddy\2026-09-28-13-51-47\data\raw\scibert_scivocab_uncased'
cfg = AutoConfig.from_pretrained(ld)
model = AutoModel.from_config(cfg)
sd = torch.load(os.path.join(ld, 'pytorch_model.bin'), map_location='cpu', weights_only=False)
clean = OrderedDict((k[5:], v) for k, v in sd.items() if k.startswith('bert.'))
model.load_state_dict(clean); model.eval()
tok = AutoTokenizer.from_pretrained(ld)
out = model(**tok('科技型中小企业 信用评估', return_tensors='pt'))[0]
print('SciBERT ok | emb_shape=', tuple(out.shape))
print('-'*40)
print(f'MODULES ok={ok} fail={fail}  |  ENV HEALTHY' if fail==0 else f'MODULES ok={ok} fail={fail}')
