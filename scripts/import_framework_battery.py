"""Copy and audit a complete selected ASAP success chain for a new CAD product."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / "runtime/products/framework_battery_v1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def mesh(path):
    vertices, faces = [], []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("v "):
            vertices.append([float(v) for v in line.split()[1:4]])
        elif line.startswith("f "):
            face = [int(v.split("/")[0])-1 for v in line.split()[1:]]
            if len(face) != 3:
                raise ValueError("Expected triangulated source OBJ.")
            faces.append(face)
    v, f = np.array(vertices, dtype=float), np.array(faces, dtype=int)
    if not len(v) or not len(f) or not np.isfinite(v).all() or f.min()<0 or f.max()>=len(v):
        raise ValueError("Invalid OBJ.")
    return v, f


def write_mesh(path, vertices, faces):
    with Path(path).open("w", encoding="ascii") as stream:
        np.savetxt(stream, vertices, fmt="v %.15g %.15g %.15g")
        np.savetxt(stream, faces+1, fmt="f %d %d %d")


def validate_chain(result, part_ids, base):
    if result.get("status") != "PASS" or result.get("success") is not True or not result.get("actual_physics"):
        raise ValueError("Source is not a successful physical disassembly result.")
    if result.get("base_part") != base or result["configuration"]["base_part"] != base:
        raise ValueError("Fixed base mismatch.")
    remaining, order, steps = set(part_ids), [], []
    chain = result["selected_plan"]
    for edge in chain:
        parent, child, moving, sim = edge["parent"], edge["child"], edge["part_move"], edge["sim_info"]
        if (len(parent)!=len(set(parent)) or len(child)!=len(set(child))
                or set(parent)!=remaining or moving not in remaining or moving==base
                or set(child)!=remaining-{moving} or base not in child
                or sim.get("feasible") is not True or sim.get("part_move")!=moving
                or sim.get("base_part")!=base):
            raise ValueError("Broken or mixed selected success chain.")
        extra = set(sim["parts_fix"])-{base}
        if not extra <= set(child):
            raise ValueError("Invalid support IDs.")
        if sim["pose"] is not None:
            raise ValueError("This bridge currently supports the recorded fixed-base null-pose case.")
        if extra:
            raise ValueError("Extra support requires a new explicitly modeled fixture.")
        direction = np.asarray(sim["action"], dtype=float)
        if direction.shape!=(3,) or not np.isfinite(direction).all():
            raise ValueError("Invalid disassembly action metadata.")
        steps.append({"part_id":moving,"remaining_before":parent,"remaining_after":child,
                      "source_pose":sim["pose"],"pose_meaning":"source assembly frame; no additional pose transform",
                      "base_part":base,"additional_supports":sorted(extra),
                      "source_action":direction.tolist(),"action_use":"disassembly evidence only",
                      "source_grasp":sim.get("grasp")})
        remaining = set(child)
        order.append(moving)
    if remaining!={base} or len(order)!=len(part_ids)-1:
        raise ValueError("Selected chain does not end at the fixed base alone.")
    if (result["connected"]["complete_chain"]!=chain or not result["connected"]["success"]
            or result["native_stats"]["sequence"]!=order):
        raise ValueError("Selected chain disagrees with connected/native evidence.")
    return order,steps


def verify_bundle(folder):
    folder = Path(folder)
    transfer = read(folder/"transfer.json")
    if transfer.get("schema")!="cad_selected_chain_transfer/v1" or transfer.get("product")!="framework_battery":
        raise ValueError("Unsupported CAD transfer schema/product.")
    for rel, digest in transfer["bundle_sha256"].items():
        path = folder / rel
        if not path.resolve().is_relative_to(folder.resolve()) or sha(path)!=digest:
            raise ValueError("Transferred evidence/CAD changed: "+rel)
    result = read(folder/"source/worker_result.json")
    order,_ = validate_chain(result, transfer["parts"], transfer["fixed_base"])
    if (transfer["assembly_candidate"] != list(reversed(order)) or transfer["disassembly_order"]!=order
            or transfer["final_remaining_parts"]!=[transfer["fixed_base"]]):
        raise ValueError("Assembly candidate differs from reverse selected chain.")
    normalization=read(folder/"source/normalization.json")["normalization"]
    scale=.001/normalization["asap_per_mm"]
    if not np.isclose(scale,transfer["coordinates"]["metres_per_source_unit"],rtol=0,atol=1e-12):
        raise ValueError("Changed metric scale.")
    case,=[c for c in read(folder/"source/manifest.json")["cases"] if c["product"]=="Industrial/framework_battery"]
    for pid,part in transfer["parts"].items():
        vertices,_=mesh(folder/part["source_obj"])
        local,_=mesh(folder/part["local_mesh"])
        expected=vertices*scale
        reconstructed=local+part["assembled_body_position_product_m"]
        if (sha(folder/part["source_obj"])!=case["input_sha256"][pid+".obj"]
                or result["configuration"]["input_sha256"][pid+".obj"]!=case["input_sha256"][pid+".obj"]
                or not np.allclose(reconstructed,expected,atol=1e-10,rtol=0)
                or not np.allclose(part["assembled_body_rotation_product"],np.eye(3),atol=1e-12,rtol=0)):
            raise ValueError("CAD hash/pose disagrees with embedded source coordinates.")
    return transfer


def import_bundle(source_root, source_step, destination):
    source_root,source_step,destination = map(Path,(source_root,source_step,destination))
    if destination.exists():
        raise ValueError("Use a new bundle folder; an existing import is preserved.")
    reference = source_root/"reference/industrial_asap3_v1"
    run = source_root/"runs/industrial_asap3_compare_20261115"
    manifest = read(reference/"manifest.json")
    case, = [c for c in manifest["cases"] if c["product"]=="Industrial/framework_battery"]
    source_dir = source_root / case["assembly_dir"]
    source_info = reference/"reference_notes/framework_battery"
    normalization = read(source_info/"parts-and-geometry.json")
    origin = np.asarray(normalization["normalization"]["origin_mm"])
    scale = normalization["normalization"]["asap_per_mm"]
    source_meta = read(source_info/"source.json")
    if sha(source_step)!=source_meta["sha256"]:
        raise ValueError("Source STEP digest does not match manufacturer provenance.")
    unit_lines = [line for line in source_step.read_text().splitlines() if "LENGTH_UNIT()" in line and "SI_UNIT" in line]
    if not unit_lines or any("SI_UNIT(.MILLI.,.METRE.)" not in line for line in unit_lines):
        raise ValueError("Source length units are ambiguous.")
    if not np.isfinite(scale) or scale<=0 or origin.shape!=(3,):
        raise ValueError("Missing normalization metadata.")
    if (source_dir/"config.json").exists():
        raise ValueError("Embedded assembly coordinates required; unexpected transform config.")
    result_path = run/"live/p01/episodes/astra/p01-astra/worker_result.json"
    result = read(result_path)
    expected = case["input_sha256"]
    if set(expected)!={f"{i}.obj" for i in range(case["parts"])}:
        raise ValueError("Manifest must cover every part OBJ.")
    for leaf,digest in expected.items():
        if sha(source_dir/leaf)!=digest or result["configuration"]["input_sha256"].get(leaf)!=digest:
            raise ValueError("Result/manifest/source CAD digest mismatch.")
    order,steps = validate_chain(result,{str(i) for i in range(case["parts"])},case["fixed_base"])
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="battery-import-",dir=destination.parent) as tmp:
        output = Path(tmp)/"bundle"
        for name in ("source/obj","meshes"):
            (output/name).mkdir(parents=True,exist_ok=True)
        copies = {"source/manifest.json":reference/"manifest.json",
                  "source/worker_result.json":result_path,"source/REPORT.md":run/"REPORT.md",
                  "source/normalization.json":source_info/"parts-and-geometry.json",
                  "source/source.json":source_info/"source.json","source/manufacturer.step":source_step}
        copies.update({f"source/obj/{leaf}":source_dir/leaf for leaf in expected})
        source_hashes = {str(path.resolve()):sha(path) for path in copies.values()}
        for rel,src in copies.items():
            shutil.copyfile(src,output/rel)
        parts = {}
        for item in normalization["parts"]:
            pid = str(item["id"])
            vertices,faces = mesh(output/f"source/obj/{pid}.obj")
            metres = vertices*(0.001/scale)
            low,high=metres.min(axis=0),metres.max(axis=0)
            center=(low+high)/2
            write_mesh(output/f"meshes/{pid}.obj",metres-center,faces)
            parts[pid]={"id":pid,"name":item["source_name"],"source_obj":f"source/obj/{pid}.obj",
                        "local_mesh":f"meshes/{pid}.obj","size_m":(high-low).tolist(),
                        "assembled_body_position_product_m":center.tolist(),
                        "assembled_body_rotation_product":np.eye(3).tolist(),
                        "embedded_source_mesh_pose":np.eye(4).tolist(),
                        "local_origin":"bounding box center in the metric normalized assembly frame"}
        transfer = {"schema":"cad_selected_chain_transfer/v1","product":"framework_battery",
                    "fixed_base":case["fixed_base"],"parts":parts,"disassembly_order":order,
                    "assembly_candidate":list(reversed(order)),"final_remaining_parts":[case["fixed_base"]],
                    "disassembly_steps":steps,
                    "coordinates":{"source_units":"normalized ASAP units","source_step_units":"mm",
                        "source_step_unit_declarations":unit_lines,"normalized_units_per_mm":scale,
                        "metres_per_source_unit":0.001/scale,"original_step_origin_mm":origin.tolist(),
                        "assembly_frame":"right-handed source OBJ axes; relative final placements embedded in vertices",
                        "original_mm_formula":"v_mm = v_asap / normalized_units_per_mm + original_step_origin_mm",
                        "product_m_formula":"v_product_m = v_asap * metres_per_source_unit"},
                    "source_scope":{"planner":"Astra candidate ranking; ASAP DFS/pose/direction/support search and RedMax",
                        "source_commit":result["source_commit"],"robot_assembly_validated":False,
                        "supports_exclude_fixed_base":True,"original_configuration":result["configuration"]},
                    "source_sha256":source_hashes,
                    "bundle_sha256":{str(p.relative_to(output)).replace("\\","/"):sha(p) for p in output.rglob("*") if p.is_file()}}
        save(output/"transfer.json",transfer)
        verify_bundle(output)
        for path,digest in source_hashes.items():
            if sha(path)!=digest:
                raise ValueError("Source changed during read-only transfer.")
        output.rename(destination)
    print(json.dumps({"bundle":str(destination),"disassembly":order,"assembly":list(reversed(order)),
                      "metres_per_source_unit":0.001/scale}))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root",required=True,type=Path)
    parser.add_argument("--source-step",required=True,type=Path)
    parser.add_argument("--output",type=Path,default=DEFAULT_BUNDLE)
    args=parser.parse_args()
    import_bundle(args.source_root,args.source_step,args.output)
