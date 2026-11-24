"""Actual geometry preparation probes; no planner/robot/LLM success is fabricated."""
import argparse
from pathlib import Path
import sys
import trimesh
from assembly_v12.contracts import read,write,import_export
from assembly_v2.budget import Budget
from assembly_v2.geometry import prepare_asset
from assembly_v2.preparation import prepare,PROFILE

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output');parser.add_argument('--export')
    a=parser.parse_args();folder=Path(a.output).absolute();folder.mkdir(parents=True,exist_ok=False)
    budget=Budget(300)
    if a.export:
        import_export(a.export,folder/'input')
        result=prepare(folder/'input',folder,PROFILE,budget)
        write(folder/'geometry_probe.json',{'status':'prepared_geometry','parts':len(result['parts']),
              'physics':'not_run','policy':'not_run','manual_registry':False})
    else:
        shapes={'cylinder':trimesh.creation.cylinder(radius=.015,height=.06,sections=32),
                'through_hole':trimesh.creation.annulus(r_min=.012,r_max=.023,height=.025,sections=32)}
        asymmetric=trimesh.creation.box(extents=[.04,.025,.03]);asymmetric.vertices[:,0]+=.25*asymmetric.vertices[:,2]
        shapes['asymmetric']=asymmetric
        results={}
        for name,m in shapes.items():
            source=folder/(name+'.obj');m.export(source)
            try:results[name]=prepare_asset(source,folder/name,read(PROFILE),budget)
            except Exception as exc:results[name]={'status':'failed','error':str(exc)}
        write(folder/'geometry_probe.json',{'status':'measured_geometry_only','results':results,'physics':'not_run','api_calls':0})
    print(folder/'geometry_probe.json')

if __name__=='__main__':main()
