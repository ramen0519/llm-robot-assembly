"""API-free Framework battery scene and bounded physical assembly experiment."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import time
import traceback

import mujoco

from battery_scene import DEFAULT_CELL,build_scene,audit_target_contacts
from battery_execution import BatteryExecution
from failure_report import FailureEvent,capture_observation,json_safe,write_json
from import_framework_battery import DEFAULT_BUNDLE,ROOT,sha
from run_pick_place import save_snapshot


def run(args):
    started=time.monotonic()
    out=ROOT/"runs/framework_battery"/datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True)
    report={"mode":"scripted" if args.execute else "scene_check","api_calls":0,"hardware_commands":0,
            "status":"initializing","robot_installations":[],"run_dir":str(out),"elapsed_limit_s":args.seconds,
            "config_sha256":sha(args.config),"transfer_sha256":sha(args.bundle/"transfer.json")}
    model=data=executor=None
    stream=None
    def picture(name):
        if args.snapshots:
            save_snapshot(model,data,{"table":{"top_center_world_m":[0,0,.75],"size_xy_m":[1,.8],"yaw_deg":0}},out/(name+".png"))
    try:
        model,data,registry,config,transfer,spec=build_scene(args.bundle,args.config)
        write_json(out/"config_snapshot.json",config)
        (out/"scene.xml").write_text(spec.to_xml(),encoding="utf-8")
        write_json(out/"resolved_scene.json",{"registry":registry,"config":config,"transfer":transfer,
                    "mujoco_version":mujoco.__version__,"control":"existing WorkcellMotion/Mink/ArmPositionController",
                    "implementation_sha256":{p.name:sha(p) for p in (ROOT/"scripts").glob("*.py")
                        if p.name in ("battery_scene.py","battery_execution.py","workcell_motion.py","move_tcp.py","run_framework_battery.py")}})
        report["assembly_candidate"]=transfer["assembly_candidate"]
        nominal=audit_target_contacts(model,data,registry,transfer["assembly_candidate"] if args.all_parts else ["5"])
        write_json(out/"target_geometry_audit.json",nominal)
        report["target_geometry"]=nominal
        if nominal["maximum_penetration_m"]>nominal["acceptance_m"]:
            raise ValueError("Collision approximation overlaps at the nominal CAD target; refine before robot execution.")
        stream=(out/"trace.jsonl").open("w",encoding="utf-8")
        last_sample=[-1.]
        def sample(row):
            if time.monotonic()-started>args.seconds:
                raise TimeoutError("Bounded experiment time exhausted.")
            if row["simulation_time_s"]-last_sample[0]>=.02:
                import json
                stream.write(json.dumps(json_safe(row))+"\n")
                last_sample[0]=row["simulation_time_s"]
        def stage(name):
            print(f"{executor.verifier.pid} {name} sim={data.time:.2f}s wall={time.monotonic()-started:.1f}s",flush=True)
            stream.flush()
            report["last_stage"]=name
            if name in ("pick_lift","transfer","install_lower","verify_stability"):
                picture(executor.verifier.pid+"_"+name)
        executor=BatteryExecution(model,data,registry,config,sample,stage)
        picture("initial")
        if args.execute:
            order=transfer["assembly_candidate"] if args.all_parts else transfer["assembly_candidate"][:1]
            for pid in order:
                report["robot_installations"].append(executor.install(pid))
            report["status"]="PASS"
        else:
            from battery_execution import BatteryVerifier
            from workcell_motion import WorkcellMotion
            verifier=BatteryVerifier(model,registry,config,"5",set())
            motion=WorkcellMotion(model,data,verifier,sample)
            motion.hold(2.,allow_support=True,allow_grasp=False)
            report["status"]="SCENE_READY"
        picture("final")
    except Exception as error:
        report["status"]="FAILED"
        report["error"]={"type":type(error).__name__,"message":str(error)}
        if isinstance(error,FailureEvent):
            report["error"].update(code=error.code,phase=error.phase,violations=error.violations,candidate=error.candidate)
        else:
            report["error"]["traceback"]=traceback.format_exc()
        if model is not None:
            report["failure_observation"]=capture_observation(model,data)
            picture("failed")
    finally:
        if stream:
            stream.close()
        report["elapsed_s"]=time.monotonic()-started
        if data is not None:
            report["final_observation"]=capture_observation(model,data)
        if executor and executor.motion:
            report["motion_plans"]=executor.motion.plans
        write_json(out/"result.json",report)
        print(report["status"],out,report.get("error",{}).get("message",""),flush=True)
    return 0 if report["status"] in ("PASS","SCENE_READY") else 1


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle",type=Path,default=DEFAULT_BUNDLE)
    parser.add_argument("--config",type=Path,default=DEFAULT_CELL)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--all-parts",action="store_true")
    parser.add_argument("--snapshots",action="store_true")
    parser.add_argument("--seconds",type=float,default=240)
    args=parser.parse_args()
    if not 5<=args.seconds<=300:
        parser.error("Use a wall-time budget between 5 and 300 seconds per invocation.")
    if args.all_parts and not args.execute:
        parser.error("--all-parts requires --execute.")
    raise SystemExit(run(args))
