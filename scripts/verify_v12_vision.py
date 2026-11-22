"""Actual local DINO smoke on saved images; not a robot run or a learned success claim."""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from assembly_v12.contracts import sha,write
from assembly_v12.memory import Encoder


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('model');p.add_argument('image',nargs='+');p.add_argument('--output',required=True)
    a=p.parse_args();started=time.monotonic()
    encoder=Encoder(a.model);rows=[];vectors=[]
    for image in a.image:
        vector=encoder.encode(image);vectors.append(vector)
        rows.append({'path':str(Path(image).resolve()),'sha256':sha(image),'dimensions':len(vector),
                     'finite':bool(np.isfinite(vector).all()),'norm':float(np.linalg.norm(vector))})
    repeated=encoder.encode(a.image[0])
    result={'status':'pass','actual_local_encoder':True,'api_calls':0,'images':rows,
            'repeat_max_difference':float(np.max(np.abs(vectors[0]-repeated))),
            'pairwise_cosine':(np.asarray(vectors)@np.asarray(vectors).T).tolist(),
            'encoder_version':encoder.version,'encoder_meta':encoder.meta,'seconds':time.monotonic()-started,
            'scope':'Feature computation only. These images are not relabeled or inserted into an index. This is not closed-loop Astra retrieval validation.'}
    if result['repeat_max_difference']>1e-5 or any(not r['finite'] or abs(r['norm']-1)>1e-5 for r in rows):
        result['status']='failed'
    write(a.output,result);print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status']=='pass' else 1


if __name__=='__main__':raise SystemExit(main())
