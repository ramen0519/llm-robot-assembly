"""Build reproducible approximate convex collision meshes from the copied CAD."""
import argparse
from importlib.metadata import version
from pathlib import Path
import time

import coacd
import manifold3d as md
import numpy as np

from import_framework_battery import DEFAULT_BUNDLE, mesh, save, sha, verify_bundle, write_mesh


def solid(vertices, faces):
    return md.Manifold(md.Mesh64(np.array(vertices,dtype=np.float64,order="C"),
                                np.array(faces,dtype=np.uint64,order="C")))


def prepare(folder, output_name="collision", threshold=.015, selected_parts=None):
    transfer = verify_bundle(folder)
    chosen=set(transfer["parts"] if selected_parts is None else selected_parts)
    if not chosen or not chosen<=set(transfer["parts"]):
        raise ValueError("Unknown/empty collision part selection.")
    if Path(output_name).name!=output_name:
        raise ValueError("Collision output must be a simple folder name.")
    target = folder/output_name
    target.mkdir(exist_ok=False)
    settings = dict(threshold=threshold,preprocess_mode="off",resolution=2000,
                    mcts_nodes=20,mcts_iterations=100,mcts_max_depth=3,
                    max_ch_vertex=128,merge=False,decimate=False,seed=0)
    coacd.set_log_level("warn")
    records = {}
    started=time.monotonic()
    for pid,part in transfer["parts"].items():
        if pid not in chosen:
            continue
        vertices,faces=mesh(folder/part["local_mesh"])
        input_solid=solid(vertices,faces)
        if input_solid.status()!=md.Error.NoError or input_solid.volume()<=0:
            raise ValueError("Invalid source solid "+pid)
        pieces=coacd.run_coacd(coacd.Mesh(vertices,faces),**settings)
        part_folder=target/pid
        part_folder.mkdir(exist_ok=True)
        hulls=[]
        for index,(v,f) in enumerate(pieces):
            path=part_folder/f"{index}.obj"
            write_mesh(path,v,f)
            shape=solid(v,f)
            if shape.status()!=md.Error.NoError or shape.volume()<=0:
                raise ValueError("Invalid collision hull.")
            hulls.append(shape)
        union=md.Manifold.batch_boolean(hulls,md.OpType.Add)
        records[pid]={"files":[str(p.relative_to(folder)).replace("\\","/") for p in sorted(part_folder.glob("*.obj"))],
                      "hulls":len(pieces),"volume_m3":input_solid.volume(),
                      "added_collision_volume_m3":(union-input_solid).volume(),
                      "omitted_volume_m3":(input_solid-union).volume(),
                      "mass_model":"assumed uniform rigid body; no electrochemistry or pouch deformation"}
        print(pid,len(pieces),"hulls",flush=True)
    report={"algorithm":"CoACD","version":version("coacd"),"settings":settings,"parts":records,
            "transfer_sha256":sha(folder/"transfer.json"),"elapsed_seconds":time.monotonic()-started,
            "scope":"approximate convex collision; source CAD retained as separate visual meshes",
            "files_sha256":{str(p.relative_to(folder)).replace("\\","/"):sha(p) for p in target.rglob("*.obj")}}
    save(target/"manifest.json",report)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle",type=Path,default=DEFAULT_BUNDLE)
    parser.add_argument("--output-name",default="collision")
    parser.add_argument("--threshold",type=float,default=.015)
    parser.add_argument("--parts",nargs="+",help="Optional subset, e.g. 0 for the fixed base only.")
    args=parser.parse_args()
    if not .001<=args.threshold<=.02:
        parser.error("Use threshold between .001 and .02.")
    prepare(args.bundle,args.output_name,args.threshold,args.parts)
