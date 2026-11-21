"""Reuse a completed fine base decomposition with verified coarse moving parts.

Fine decomposition of all thin cells is deliberately bounded: thousands of
tiny hulls are not useful for the first single-cell robot experiment. Every
referenced hull is hashed; nominal assembled geometry is checked separately.
"""
import argparse
from pathlib import Path

import manifold3d as md
from import_framework_battery import DEFAULT_BUNDLE,mesh,read,save,sha,verify_bundle
from prepare_battery_collision import solid


def compose(bundle,base_directory,output_name):
    transfer=verify_bundle(bundle)
    source=read(bundle/"collision/manifest.json")
    if source["transfer_sha256"]!=sha(bundle/"transfer.json"):
        raise ValueError("Different transferred CAD.")
    for rel,digest in source["files_sha256"].items():
        if sha(bundle/rel)!=digest:
            raise ValueError("Changed original collision mesh.")
    base_directory=base_directory.resolve()
    if not base_directory.is_relative_to(bundle.resolve()):
        raise ValueError("Base decomposition must be inside this bundle.")
    fine_manifest=base_directory.parent/"manifest.json"
    if fine_manifest.exists():
        fine=read(fine_manifest)
        if fine["transfer_sha256"]!=sha(bundle/"transfer.json") or fine["settings"]["threshold"]!=.003:
            raise ValueError("Unexpected refined base provenance.")
        for rel in fine["parts"]["0"]["files"]:
            if sha(bundle/rel)!=fine["files_sha256"][rel]:
                raise ValueError("Changed refined base hull.")
    files=sorted(base_directory.glob("*.obj"),key=lambda p:int(p.stem))
    if not files or [int(p.stem) for p in files]!=list(range(len(files))):
        raise ValueError("Incomplete base hull indices.")
    hulls=[]
    for p in files:
        shape=solid(*mesh(p))
        if shape.status()!=md.Error.NoError or shape.volume()<=0:
            raise ValueError("Invalid base hull.")
        hulls.append(shape)
    union=md.Manifold.batch_boolean(hulls,md.OpType.Add)
    exact=solid(*mesh(bundle/transfer["parts"]["0"]["local_mesh"]))
    omitted=(exact-union).volume()
    # Volume is a coarse integrity guard, not a fit tolerance. CoACD cuts have
    # small numerical omissions; the separate 0.1 mm target test stays strict.
    if omitted>exact.volume()*.001:
        raise ValueError(f"Incomplete fine base coverage: omitted={omitted}, source={exact.volume()}, fraction={omitted/exact.volume()}")
    records=source["parts"]
    records["0"]={"files":[str(p.relative_to(bundle.resolve())) for p in files],
        "hulls":len(files),"volume_m3":exact.volume(),
        "added_collision_volume_m3":(union-exact).volume(),"omitted_volume_m3":omitted,
        "maximum_omitted_volume_fraction":.001,
        "mass_model":"fixed base; completed fine CoACD hulls",
        "generation":"prepare_battery_collision.py --output-name collision_fine --threshold 0.003 --parts 0",
        "generation_scope":"Refined base only; other five parts retain their audited coarse hulls."}
    if Path(output_name).name!=output_name:
        raise ValueError("Use a simple output folder name.")
    target=bundle/output_name
    target.mkdir(exist_ok=False)
    all_files=[p for part in records.values() for p in part["files"]]
    save(target/"manifest.json",{"algorithm":"CoACD mixed precision","version":source["version"],
        "parts":records,"transfer_sha256":sha(bundle/"transfer.json"),
        "settings":{"base_threshold":.003,"moving_parts":source["settings"]},
        "scope":"Approximate convex collision, not proof of fitting; requires nominal-target audit.",
        "files_sha256":{p:sha(bundle/p) for p in all_files}})
    print(target, "base hulls",len(files), "omitted volume",omitted,flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle",type=Path,default=DEFAULT_BUNDLE)
    args=parser.parse_args()
    compose(args.bundle,args.bundle/"collision_fine/0","collision_base_refined")
