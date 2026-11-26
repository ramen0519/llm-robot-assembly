"""Diagnose prepared CAD's reference and initial supply stability without APIs."""
import argparse
import json
from pathlib import Path
from assembly_v12.contracts import read,location
from assembly_v2.budget import Budget
from assembly_v2.readiness import diagnose_start


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',required=True,help='Existing prepared.json; never modified.')
    parser.add_argument('--output',required=True,help='New diagnostic folder.')
    parser.add_argument('--seconds',type=float,default=180)
    parser.add_argument('--rebuild-supply-layout',action='store_true',help='Regenerate only initial supply poses using the current common algorithm.')
    args=parser.parse_args()
    result=diagnose_start(read(location(args.prepared)),location(args.output),Budget(args.seconds),rebuild_supply=args.rebuild_supply_layout)
    print(json.dumps(result,ensure_ascii=True,indent=2))
    return 0 if result['ready_for_robot_attempt'] else 2


if __name__=='__main__':
    raise SystemExit(main())
