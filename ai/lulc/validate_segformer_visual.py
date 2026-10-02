import json, math, os, re, time
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window, from_bounds
from PIL import Image, ImageDraw, ImageFont
import torch
import torch.nn.functional as F
from transformers import SegformerForSemanticSegmentation

ROOT = Path(r"D:\12+18\intelligent-land-record-platform")
RUN = ROOT / "data" / "local" / "lulc_segformer_b2"
MODEL = RUN / "best_model"
S2 = Path(r"C:\datasets\sentinel2_tamilnadu_2021\rgbnir")
WC = Path(r"C:\datasets\worldcover")
OUT = RUN / "visual_validation"
OUT.mkdir(parents=True, exist_ok=True)
PATCH = 512
BATCH = 2
CODES = np.array([10,20,30,40,50,60,70,80,90,95,100], dtype=np.uint8)
NAMES = ["tree","shrubland","grassland","cropland","built_up","bare_sparse","snow_ice","water","wetland","mangroves","moss_lichen"]
MEAN = np.array([0.485,0.456,0.406,0.50], np.float32)[:,None,None]
STD = np.array([0.229,0.224,0.225,0.25], np.float32)[:,None,None]
LUT = np.full(256,255,dtype=np.uint8)
for i,c in enumerate(CODES): LUT[c]=i
PALETTE = {0:(0,0,0),10:(0,100,0),20:(255,187,34),30:(255,255,76),40:(240,150,255),50:(250,0,0),60:(180,180,180),70:(240,240,240),80:(0,100,200),90:(0,150,160),95:(0,207,117),100:(250,230,160)}
def parse_id(tile):
    m=re.match(r"([NS])(\d{2})([EW])(\d{3})$",tile)
    lat=int(m.group(2))*(1 if m.group(1)=="N" else -1)
    lon=int(m.group(4))*(1 if m.group(3)=="E" else -1)
    return lat,lon

def wc_path(tile):
    lat,lon=parse_id(tile)
    blat=math.floor(lat/3)*3; blon=math.floor(lon/3)*3
    bid=f"{'N' if blat>=0 else 'S'}{abs(blat):02d}{'E' if blon>=0 else 'W'}{abs(blon):03d}"
    return WC/f"ESA_WorldCover_10m_2021_v200_{bid}_Map.tif"

def positions(w,h):
    out=[]
    for y in range(0,h,PATCH):
        for x in range(0,w,PATCH):
            out.append((x,y,min(PATCH,w-x),min(PATCH,h-y)))
    return out

def normalize_patch(a):
    x=np.zeros((4,PATCH,PATCH),np.float32)
    x[:,:a.shape[1],:a.shape[2]]=a.astype(np.float32)/10000.0
    x=np.clip(x,0,1)
    return (x-MEAN)/STD

def colorize(arr):
    rgb=np.zeros((arr.shape[0],arr.shape[1],3),dtype=np.uint8)
    for code,color in PALETTE.items(): rgb[arr==code]=color
    return rgb

def stretch_rgb(arr):
    a=arr.astype(np.float32)
    out=np.zeros_like(a,dtype=np.uint8)
    for b in range(3):
        v=a[b]; nz=v[v>0]
        if nz.size:
            lo,hi=np.percentile(nz,[2,98])
            out[b]=np.clip((v-lo)/max(hi-lo,1)*255,0,255).astype(np.uint8)
    return np.transpose(out,(1,2,0))
def quicklook(tile,s2_path,wc_path_,pred_path,metrics):
    size=900
    with rasterio.open(s2_path) as s:
        rgb=s.read([1,2,3],out_shape=(3,size,size),resampling=rasterio.enums.Resampling.bilinear)
        rgb=stretch_rgb(rgb)
        sb=s.bounds
    with rasterio.open(wc_path_) as w:
        win=from_bounds(*sb,transform=w.transform).round_offsets().round_lengths()
        gt=w.read(1,window=win,out_shape=(size,size),resampling=rasterio.enums.Resampling.nearest)
    with rasterio.open(pred_path) as p:
        pr=p.read(1,out_shape=(size,size),resampling=rasterio.enums.Resampling.nearest)
    panels=[Image.fromarray(rgb),Image.fromarray(colorize(gt)),Image.fromarray(colorize(pr))]
    canvas=Image.new("RGB",(size*3, size+180),"white")
    for i,im in enumerate(panels): canvas.paste(im,(i*size,70))
    d=ImageDraw.Draw(canvas)
    font=ImageFont.load_default()
    d.text((10,10),f"{tile} | pixel acc {metrics['pixel_accuracy']:.3f} | mIoU {metrics['miou']:.3f}",fill="black",font=font)
    for i,t in enumerate(["Sentinel-2 RGB","WorldCover ground truth","SegFormer-B2 prediction"]):
        d.text((i*size+10,45),t,fill="black",font=font)
    x,y=10,size+85
    for i,(code,name) in enumerate(zip(CODES,NAMES)):
        xx=x+(i%6)*440; yy=y+(i//6)*32
        d.rectangle((xx,yy,xx+20,yy+20),fill=PALETTE[int(code)])
        d.text((xx+28,yy+3),name,fill="black",font=font)
    fp=OUT/f"{tile}_comparison.png"; canvas.save(fp,quality=95)
    return fp

def metrics_from_conf(conf):
    tp=np.diag(conf).astype(np.float64); union=conf.sum(1)+conf.sum(0)-tp
    iou=np.divide(tp,union,out=np.full_like(tp,np.nan),where=union>0)
    acc=float(tp.sum()/max(conf.sum(),1))
    return {"pixel_accuracy":acc,"miou":float(np.nanmean(iou)),"per_class_iou":{NAMES[i]:(None if np.isnan(iou[i]) else float(iou[i])) for i in range(len(NAMES))}}
def infer_tile(model,device,tile):
    s2_path=S2/f"ESA_WorldCover_10m_2021_v200_{tile}_S2RGBNIR.tif"
    wcp=wc_path(tile); pred_path=OUT/f"{tile}_prediction.tif"
    conf=np.zeros((len(CODES),len(CODES)),dtype=np.int64)
    t0=time.time()
    with rasterio.open(s2_path) as s, rasterio.open(wcp) as w:
        profile=s.profile.copy(); profile.update(count=1,dtype="uint8",nodata=0,compress="DEFLATE",tiled=True,blockxsize=512,blockysize=512)
        pos=positions(s.width,s.height)
        with rasterio.open(pred_path,"w",**profile) as dst:
            for bi in range(0,len(pos),BATCH):
                batch_pos=pos[bi:bi+BATCH]; xs=[]; valid_masks=[]
                for x,y,ww,hh in batch_pos:
                    a=s.read(window=Window(x,y,ww,hh))
                    valid=np.max(a,axis=0)>0
                    xs.append(normalize_patch(a)); valid_masks.append(valid)
                xt=torch.from_numpy(np.stack(xs)).to(device,non_blocking=True)
                with torch.inference_mode(), torch.autocast(device_type="cuda",dtype=torch.float16):
                    logits=model(pixel_values=xt).logits
                    logits=F.interpolate(logits,size=(PATCH,PATCH),mode="bilinear",align_corners=False)
                    pi=logits.argmax(1).cpu().numpy()
                for j,(x,y,ww,hh) in enumerate(batch_pos):
                    idx=pi[j,:hh,:ww]; pred=CODES[idx].copy()
                    valid=valid_masks[j]
                    pred[~valid]=0
                    dst.write(pred,1,window=Window(x,y,ww,hh))
                    b=rasterio.windows.bounds(Window(x,y,ww,hh),s.transform)
                    lwin=from_bounds(*b,transform=w.transform).round_offsets().round_lengths()
                    gt=w.read(1,window=lwin,out_shape=(hh,ww),resampling=rasterio.enums.Resampling.nearest)
                    gi=LUT[gt]; ok=valid & (gi!=255)
                    if ok.any():
                        z=gi[ok].astype(np.int64)*len(CODES)+idx[ok].astype(np.int64)
                        conf += np.bincount(z,minlength=len(CODES)**2).reshape(len(CODES),len(CODES))
                if ((bi//BATCH)+1)%100==0:
                    print(tile,"batch",bi//BATCH+1,"/",math.ceil(len(pos)/BATCH),flush=True)
    m=metrics_from_conf(conf); m["minutes"]=(time.time()-t0)/60
    m["tile"]=tile; m["prediction"]=str(pred_path)
    quicklook(tile,s2_path,wcp,pred_path,m)
    (OUT/f"{tile}_metrics.json").write_text(json.dumps(m,indent=2),encoding="utf-8")
    print("TILE_DONE",tile,json.dumps(m),flush=True)
    return m
def main():
    if not torch.cuda.is_available(): raise RuntimeError("CUDA unavailable")
    splits=json.loads((RUN/"split_tiles.json").read_text())
    tests=splits["test"]
    device=torch.device("cuda")
    model=SegformerForSemanticSegmentation.from_pretrained(MODEL,local_files_only=True).to(device).eval()
    print("GPU",torch.cuda.get_device_name(0),"tiles",tests,flush=True)
    allm=[infer_tile(model,device,t) for t in tests]
    summary={"tiles":allm,"mean_tile_miou":float(np.mean([m["miou"] for m in allm])),"mean_tile_accuracy":float(np.mean([m["pixel_accuracy"] for m in allm]))}
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    imgs=[Image.open(OUT/f"{t}_comparison.png") for t in tests]
    combo=Image.new("RGB",(max(i.width for i in imgs),sum(i.height for i in imgs)),"white")
    y=0
    for im in imgs: combo.paste(im,(0,y)); y+=im.height
    combo.save(OUT/"heldout_comparison_all.png",quality=95)
    print("VALIDATION_COMPLETE",json.dumps(summary),flush=True)

if __name__=="__main__":
    main()
