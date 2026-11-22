"""Download only the pinned public DINO asset; never call a paid model API."""
import argparse
import json
from pathlib import Path
import time
import urllib.request
import uuid

from assembly_v12.contracts import location,sha,write

REVISION='ed25f3a31f01632728cabb09d1542f84ab7b0056'
WEIGHT_SHA='ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder')
    args=parser.parse_args()
    folder=location(args.folder);folder.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();inventory={}
    for name in ('config.json','preprocessor_config.json','model.safetensors'):
        target=folder/name
        url=f'https://huggingface.co/facebook/dinov2-small/resolve/{REVISION}/{name}'
        if not target.exists():
            partial=folder/(name+'.partial_'+uuid.uuid4().hex[:8])
            size=0
            try:
                with urllib.request.urlopen(url,timeout=30) as response, partial.open('wb') as output:
                    while True:
                        if time.monotonic()-started>180:raise TimeoutError('Asset download time limit: 180 seconds.')
                        chunk=response.read(1024*1024)
                        if not chunk:break
                        size+=len(chunk)
                        if size>120*1024*1024:raise ValueError('Unexpected DINO asset size.')
                        output.write(chunk)
                if name=='model.safetensors' and sha(partial)!=WEIGHT_SHA:
                    raise ValueError('Downloaded DINO weight hash differs from the upstream contract.')
                if name.endswith('.json'):
                    json.loads(partial.read_text(encoding='utf8'))
                partial.replace(target)
            finally:
                if partial.exists():partial.unlink()
        if name=='model.safetensors' and sha(target)!=WEIGHT_SHA:
            raise ValueError('Existing DINO weight hash differs from the upstream contract; refusing to overwrite it.')
        inventory[name]={'url':url,'sha256':sha(target),'bytes':target.stat().st_size}
    write(folder/'source.json',{'model':'facebook/dinov2-small','revision':REVISION,'files':inventory,'api_calls':0})
    print(json.dumps({'status':'pinned_assets_ready','folder':str(folder),'api_calls':0,'seconds':time.monotonic()-started}))


if __name__=='__main__':main()
