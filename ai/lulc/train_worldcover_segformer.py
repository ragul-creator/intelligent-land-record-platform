import csv, hashlib, json, math, os, random, re, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window, from_bounds, bounds as window_bounds
import torch
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from transformers import SegformerModel, SegformerForSemanticSegmentation

ROOT = Path(os.getenv("LULC_ROOT", r"D:\12+18\intelligent-land-record-platform"))
S2_DIR = Path(os.getenv("LULC_S2_DIR", r"C:\datasets\sentinel2_tamilnadu_2021\rgbnir"))
WC_DIR = Path(os.getenv("LULC_WC_DIR", r"C:\datasets\worldcover"))
MODEL_DIR = Path(os.getenv("LULC_MODEL_DIR", str(ROOT / "models" / "segformer" / "mit-b2")))
RUN_DIR = Path(os.getenv("LULC_RUN_DIR", str(ROOT / "data" / "local" / "lulc_segformer_b2")))
PATCH = int(os.getenv("LULC_PATCH", "512"))
EPOCHS = int(os.getenv("LULC_EPOCHS", "30"))
SEED = int(os.getenv("LULC_SEED", "1337"))
CODES = [10,20,30,40,50,60,70,80,90,95,100]
NAMES = ["tree","shrubland","grassland","cropland","built_up","bare_sparse","snow_ice","water","wetland","mangroves","moss_lichen"]
LUT = np.full(256, 255, dtype=np.uint8)
for i, code in enumerate(CODES): LUT[code] = i
RUN_DIR.mkdir(parents=True, exist_ok=True)
def seed_all(seed=SEED):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available(): torch.cuda.manual_seed_all(seed)

def tile_id(lat, lon):
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"

def parse_s2(path):
    m = re.search(r"_([NS])(\d{2})([EW])(\d{3})_S2RGBNIR\.tif$", path.name)
    if not m: raise ValueError(f"Unrecognized Sentinel tile: {path.name}")
    lat = int(m.group(2)) * (1 if m.group(1) == "N" else -1)
    lon = int(m.group(4)) * (1 if m.group(3) == "E" else -1)
    return lat, lon

def label_path_for(lat, lon):
    blat = math.floor(lat / 3) * 3; blon = math.floor(lon / 3) * 3
    return WC_DIR / f"ESA_WorldCover_10m_2021_v200_{tile_id(blat, blon)}_Map.tif"

def discover():
    recs = []
    for s2 in sorted(S2_DIR.glob("*.tif")):
        lat, lon = parse_s2(s2); wc = label_path_for(lat, lon)
        if not wc.exists(): raise FileNotFoundError(f"Missing label {wc}")
        with rasterio.open(s2) as a, rasterio.open(wc) as b:
            if a.crs != b.crs or a.res != b.res or a.count != 4:
                raise RuntimeError(f"Alignment/spec mismatch: {s2.name}")
            if not (b.bounds.left <= a.bounds.left and b.bounds.right >= a.bounds.right and b.bounds.bottom <= a.bounds.bottom and b.bounds.top >= a.bounds.top):
                raise RuntimeError(f"Label does not cover imagery: {s2.name}")
            recs.append({"s2":str(s2),"wc":str(wc),"tile":tile_id(lat,lon),"w":a.width,"h":a.height})
    if not recs: raise RuntimeError("No Sentinel-2 TIFFs found")
    return recs
def spatial_split(recs):
    shuffled = recs[:]
    random.Random(SEED).shuffle(shuffled)
    test = shuffled[:3]; val = shuffled[3:7]; train = shuffled[7:]
    return {"train":train,"val":val,"test":test}

def grid_positions(w, h):
    xs = list(range(0, w-PATCH+1, PATCH)); ys = list(range(0, h-PATCH+1, PATCH))
    if xs[-1] != w-PATCH: xs.append(w-PATCH)
    if ys[-1] != h-PATCH: ys.append(h-PATCH)
    return [(x,y) for y in ys for x in xs]

def sampled_patches(records, per_tile):
    out = []
    for r in records:
        pos = grid_positions(r["w"], r["h"])
        h = int(hashlib.md5(r["tile"].encode()).hexdigest()[:8], 16)
        rng = random.Random(SEED + h); rng.shuffle(pos)
        for x,y in pos[:min(per_tile,len(pos))]:
            out.append({"s2":r["s2"],"wc":r["wc"],"tile":r["tile"],"x":x,"y":y})
    return out

def valid_patch(p):
    with rasterio.open(p["s2"]) as src:
        a = src.read(1, window=Window(p["x"],p["y"],PATCH,PATCH))
    return float(np.count_nonzero(a)) / a.size >= 0.25

def prepare_manifests():
    recs = discover(); splits = spatial_split(recs)
    print("tiles", {k:len(v) for k,v in splits.items()}, flush=True)
    manifest = {}
    for name, records in splits.items():
        per = 128 if name == "train" else 96
        candidates = sampled_patches(records, per)
        kept = [p for p in candidates if valid_patch(p)]
        manifest[name] = kept
        fp = RUN_DIR / f"{name}_manifest.jsonl"
        fp.write_text("\n".join(json.dumps(p) for p in kept) + "\n", encoding="utf-8")
        print(name, "patches", len(kept), flush=True)
    (RUN_DIR/"split_tiles.json").write_text(json.dumps({k:[r["tile"] for r in v] for k,v in splits.items()},indent=2))
    return manifest
class LULCDataset(Dataset):
    def __init__(self, items, augment=False):
        self.items=items; self.augment=augment; self._s2={}; self._wc={}
        self.mean=np.array([0.485,0.456,0.406,0.50],np.float32)[:,None,None]
        self.std=np.array([0.229,0.224,0.225,0.25],np.float32)[:,None,None]
    def __len__(self): return len(self.items)
    def __getstate__(self):
        d=self.__dict__.copy(); d["_s2"]={}; d["_wc"]={}; return d
    def _open(self, cache, path):
        if path not in cache: cache[path]=rasterio.open(path)
        return cache[path]
    def __getitem__(self, i):
        p=self.items[i]; s=self._open(self._s2,p["s2"]); w=self._open(self._wc,p["wc"])
        win=Window(p["x"],p["y"],PATCH,PATCH)
        img=s.read(window=win).astype(np.float32)/10000.0
        img=np.clip(img,0,1)
        b=window_bounds(win,s.transform)
        lwin=from_bounds(*b,transform=w.transform).round_offsets().round_lengths()
        raw=w.read(1,window=lwin,out_shape=(PATCH,PATCH),resampling=rasterio.enums.Resampling.nearest)
        lab=LUT[raw]
        valid=(np.max(img,axis=0)>0) & (lab!=255)
        lab=lab.copy(); lab[~valid]=255
        if self.augment:
            if random.random()<0.5: img=img[:,:,::-1]; lab=lab[:,::-1]
            if random.random()<0.5: img=img[:,::-1,:]; lab=lab[::-1,:]
        img=(img-self.mean)/self.std
        return torch.from_numpy(np.ascontiguousarray(img)), torch.from_numpy(np.ascontiguousarray(lab.astype(np.int64)))
def estimate_class_weights(items, n=256):
    counts=np.zeros(len(CODES),dtype=np.int64)
    rng=random.Random(SEED); sample=rng.sample(items,min(n,len(items)))
    ds=LULCDataset(sample,False)
    for _,lab in ds:
        a=lab.numpy(); v=a!=255
        if v.any(): counts += np.bincount(a[v],minlength=len(CODES))
    freq=counts/np.maximum(counts.sum(),1)
    nz=freq[freq>0]; med=float(np.median(nz)) if nz.size else 1.0
    weights=np.ones(len(CODES),dtype=np.float32)
    for i,f in enumerate(freq):
        if f>0: weights[i]=np.clip(med/f,0.5,5.0)
    print("class_counts",dict(zip(NAMES,counts.tolist())),flush=True)
    print("class_weights",dict(zip(NAMES,weights.round(3).tolist())),flush=True)
    return torch.tensor(weights,dtype=torch.float32)

def build_model():
    base=SegformerModel.from_pretrained(str(MODEL_DIR),local_files_only=True)
    cfg=base.config; cfg.num_labels=len(CODES); cfg.num_channels=4
    cfg.id2label={i:n for i,n in enumerate(NAMES)}; cfg.label2id={n:i for i,n in enumerate(NAMES)}
    model=SegformerForSemanticSegmentation(cfg)
    src=base.state_dict(); dst=model.segformer.state_dict(); adapted={}
    for k,v in src.items():
        if k not in dst: continue
        if v.shape==dst[k].shape: adapted[k]=v
        elif k.endswith("patch_embeddings.0.proj.weight") and v.ndim==4 and v.shape[1]==3 and dst[k].shape[1]==4:
            nv=dst[k].clone(); nv[:,:3]=v; nv[:,3:4]=v.mean(1,keepdim=True); adapted[k]=nv
    model.segformer.load_state_dict(adapted,strict=False)
    print("pretrained_encoder_tensors",len(adapted),flush=True)
    return model
def dice_loss(logits, labels):
    probs=torch.softmax(logits,dim=1); valid=labels!=255
    safe=labels.clone(); safe[~valid]=0
    one=F.one_hot(safe,num_classes=len(CODES)).permute(0,3,1,2).float()
    mask=valid.unsqueeze(1)
    inter=(probs*one*mask).sum((0,2,3)); den=((probs+one)*mask).sum((0,2,3))
    present=(one*mask).sum((0,2,3))>0
    d=(2*inter+1)/(den+1)
    return 1-d[present].mean() if present.any() else logits.sum()*0

def loss_fn(model,x,y,weights):
    out=model(pixel_values=x).logits
    out=F.interpolate(out,size=y.shape[-2:],mode="bilinear",align_corners=False)
    ce=F.cross_entropy(out,y,weight=weights,ignore_index=255,label_smoothing=0.02)
    return ce+0.5*dice_loss(out,y), out

@torch.no_grad()
def evaluate(model,loader,device,weights):
    model.eval(); conf=torch.zeros((len(CODES),len(CODES)),dtype=torch.int64); losses=[]
    for x,y in loader:
        x=x.to(device,non_blocking=True); y=y.to(device,non_blocking=True)
        with torch.autocast(device_type="cuda",dtype=torch.float16):
            loss,logits=loss_fn(model,x,y,weights)
        losses.append(float(loss)); pred=logits.argmax(1)
        v=y!=255
        if v.any():
            z=(y[v]*len(CODES)+pred[v]).detach().cpu()
            conf += torch.bincount(z,minlength=len(CODES)**2).reshape(len(CODES),len(CODES))
    tp=conf.diag().float(); union=conf.sum(1)+conf.sum(0)-tp
    iou=torch.where(union>0,tp/union,torch.nan)
    return {"loss":sum(losses)/max(len(losses),1),"miou":float(torch.nanmean(iou)),"per_class_iou":{NAMES[i]:(None if torch.isnan(iou[i]) else float(iou[i])) for i in range(len(CODES))}}
def make_loader(ds,batch,shuffle):
    return DataLoader(ds,batch_size=batch,shuffle=shuffle,num_workers=2,pin_memory=True,persistent_workers=True,drop_last=shuffle)

def main():
    seed_all()
    if not torch.cuda.is_available(): raise RuntimeError("CUDA PyTorch is required; refusing CPU overnight training")
    device=torch.device("cuda"); print("gpu",torch.cuda.get_device_name(0),flush=True)
    manifests=prepare_manifests()
    if len(manifests["train"])<500 or len(manifests["val"])<50: raise RuntimeError("Too few valid patches")
    weights=estimate_class_weights(manifests["train"]).to(device)
    train_ds=LULCDataset(manifests["train"],True); val_ds=LULCDataset(manifests["val"],False); test_ds=LULCDataset(manifests["test"],False)
    model=build_model().to(device)
    batch=2
    try:
        probe=make_loader(train_ds,batch,True); x,y=next(iter(probe)); x=x.to(device); y=y.to(device)
        with torch.autocast(device_type="cuda",dtype=torch.float16): loss,_=loss_fn(model,x,y,weights)
        loss.backward(); model.zero_grad(set_to_none=True); del probe,x,y,loss; torch.cuda.empty_cache()
    except torch.cuda.OutOfMemoryError:
        batch=1; model.zero_grad(set_to_none=True); torch.cuda.empty_cache(); print("OOM probe: falling back to batch=1",flush=True)
    accum=4 if batch==1 else 2
    train_loader=make_loader(train_ds,batch,True); val_loader=make_loader(val_ds,batch,False); test_loader=make_loader(test_ds,batch,False)
    opt=torch.optim.AdamW(model.parameters(),lr=6e-5,weight_decay=0.01)
    sched=torch.optim.lr_scheduler.CosineAnnealingLR(opt,T_max=EPOCHS)
    scaler=torch.cuda.amp.GradScaler(enabled=True)
    latest=RUN_DIR/"latest.pt"; best_file=RUN_DIR/"best.pt"; start=0; best=-1.0; stale=0
    if latest.exists():
        ck=torch.load(latest,map_location="cpu")
        model.load_state_dict(ck["model"]); opt.load_state_dict(ck["optimizer"]); sched.load_state_dict(ck["scheduler"])
        start=ck["epoch"]+1; best=ck.get("best_miou",-1.0); stale=ck.get("stale",0)
        print("resumed_epoch",start,"best",best,flush=True)
    metrics_csv=RUN_DIR/"metrics.csv"
    if not metrics_csv.exists():
        metrics_csv.write_text("epoch,train_loss,val_loss,val_miou,lr,minutes\n",encoding="utf-8")
    patience=7
    for epoch in range(start,EPOCHS):
        t0=time.time(); model.train(); opt.zero_grad(set_to_none=True); running=0.0
        for step,(x,y) in enumerate(train_loader):
            x=x.to(device,non_blocking=True); y=y.to(device,non_blocking=True)
            with torch.autocast(device_type="cuda",dtype=torch.float16):
                loss,_=loss_fn(model,x,y,weights); scaled_loss=loss/accum
            scaler.scale(scaled_loss).backward()
            if (step+1)%accum==0 or step+1==len(train_loader):
                scaler.step(opt); scaler.update(); opt.zero_grad(set_to_none=True)
            running += float(loss.detach())
            if (step+1)%100==0: print(f"epoch {epoch+1} step {step+1}/{len(train_loader)} loss {running/(step+1):.4f}",flush=True)
        sched.step(); val=evaluate(model,val_loader,device,weights); mins=(time.time()-t0)/60
        train_loss=running/max(len(train_loader),1)
        print(f"EPOCH {epoch+1}/{EPOCHS} train={train_loss:.4f} val={val['loss']:.4f} mIoU={val['miou']:.4f} minutes={mins:.1f}",flush=True)
        with metrics_csv.open("a",newline="",encoding="utf-8") as f:
            csv.writer(f).writerow([epoch+1,train_loss,val["loss"],val["miou"],opt.param_groups[0]["lr"],mins])
        improved=val["miou"]>best
        if improved: best=val["miou"]; stale=0
        else: stale+=1
        state={"epoch":epoch,"model":model.state_dict(),"optimizer":opt.state_dict(),"scheduler":sched.state_dict(),"best_miou":best,"stale":stale,"val":val}
        torch.save(state,latest)
        if improved: torch.save(state,best_file); (RUN_DIR/"best_val.json").write_text(json.dumps(val,indent=2))
        if stale>=patience: print("early_stop",flush=True); break
    ck=torch.load(best_file,map_location=device); model.load_state_dict(ck["model"])
    test=evaluate(model,test_loader,device,weights)
    (RUN_DIR/"test_metrics.json").write_text(json.dumps(test,indent=2),encoding="utf-8")
    export=RUN_DIR/"best_model"; export.mkdir(exist_ok=True)
    model.save_pretrained(export)
    print("TEST",json.dumps(test),flush=True)
    print("TRAINING_COMPLETE",flush=True)

if __name__=="__main__":
    main()
