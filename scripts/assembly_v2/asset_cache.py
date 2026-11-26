"""Content-addressed, verified geometry reuse; never assembly-success reuse."""
from pathlib import Path
import shutil
import time
import uuid
import numpy as np
from assembly_v12.contracts import InputError,digest,read,write,sha,relative,location
from .storage import data_root

SCHEMA='verified_collision_cache/v1'


def settings_for(profile):
    return {**profile['collision'],**{k:profile['resources'][k]
            for k in ('max_hulls_per_body','max_geometry_cells')}}


def root():
    return data_root()/'runtime/assets/verified_collision_v1'


def key(source,settings):
    return digest({'schema':SCHEMA,'source_mesh_sha256':sha(source),'settings':settings,
                   'validator_sha256':validator_hash()})


def validator_hash():
    return digest({p.name:sha(p) for p in (Path(__file__),Path(__file__).with_name('geometry.py'))})


def checked_asset(source,folder,settings):
    folder=location(folder);asset=read(folder/'asset.json')
    if (asset.get('schema')!='collision_asset/v2.0' or
        asset.get('asset_hash')!=digest({k:v for k,v in asset.items() if k!='asset_hash'}) or
        asset.get('source_mesh_sha256')!=sha(source) or asset.get('settings')!=settings or
        asset.get('quality',{}).get('status')!='passed' or
        not 1<=len(asset.get('hulls',[]))<=settings['max_hulls_per_body']):
        raise ValueError('Asset identity/settings/quality differs from the source.')
    for row in [asset['visual'],*asset['hulls']]:
        if sha(relative(folder,row['path']))!=row['sha256']:
            raise ValueError('Collision cache file differs from its verified bytes.')
    return asset


def check_receipt(source,folder,settings):
    receipt=read(folder/'verification.json')
    if receipt.get('schema')!=SCHEMA or receipt.get('validator_sha256')!=validator_hash():
        raise ValueError('Cache validator version changed; register assets again.')
    if receipt.get('key')!=key(source,settings) or receipt.get('status')!='passed':
        raise ValueError('Cache receipt does not match source/settings.')
    asset=checked_asset(source,folder/'collision',settings)
    if (receipt.get('asset_hash')!=asset['asset_hash'] or
        sha(folder/'source_metric.obj')!=sha(source) or
        receipt.get('receipt_hash')!=digest({k:v for k,v in receipt.items() if k!='receipt_hash'})):
        raise ValueError('Cache receipt identity changed.')
    return asset,receipt


def reuse(source,folder,profile,budget,*,cache_root=None):
    settings=settings_for(profile);entry=Path(cache_root or root())/key(source,settings)
    if not entry.exists():return None
    budget.check('collision_cache_read')
    # An older validator is a cache miss. Altered content with the current
    # validator is an error, never silently treated as a successful validation.
    receipt=read(entry/'verification.json')
    if receipt.get('validator_sha256')!=validator_hash():return None
    asset,receipt=check_receipt(source,entry,settings)
    dest=Path(folder);dest.mkdir(parents=True,exist_ok=True)
    for row in [asset['visual'],*asset['hulls']]:
        shutil.copy2(relative(entry/'collision',row['path']),relative(dest,row['path']))
    shutil.copy2(entry/'collision/asset.json',dest/'asset.json')
    write(dest/'cache_reuse.json',{'schema':'collision_cache_reuse/v1','key':receipt['key'],
        'receipt_hash':receipt['receipt_hash'],'source_entry':str(entry),
        'source_mesh_sha256':sha(source),'asset_hash':asset['asset_hash'],
        'physics_validation_reused':False,'reference_grasp_installation':'must_run_again'})
    return {**asset,'folder':str(dest.absolute())}


def register_asset(source,folder,profile,budget,*,cache_root=None,provenance=None):
    """Recheck imported geometry against its original metric mesh before publishing."""
    from scipy.spatial import ConvexHull
    from .geometry import load_mesh,geometry_matches,quality
    source=location(source);folder=location(folder)
    settings=settings_for(profile);asset=checked_asset(source,folder,settings)
    cache=Path(cache_root or root());cache.mkdir(parents=True,exist_ok=True)
    entry=cache/key(source,settings)
    if entry.exists():
        _,receipt=check_receipt(source,entry,settings)
        return {**receipt,'existing_entry':True}
    budget.check('collision_cache_verify')
    original=load_mesh(source);visual=load_mesh(relative(folder,asset['visual']['path']))
    if not geometry_matches(original,visual):raise ValueError('Visual differs from original CAD.')
    if (not np.isclose(asset['volume_m3'],original.volume,rtol=1e-9,atol=1e-15) or
        not np.allclose(asset['center_mass_m'],original.center_mass,rtol=0,atol=1e-10) or
        not np.allclose(asset['inertia_per_kg'],original.moment_inertia/original.mass,rtol=1e-8,atol=1e-12)):
        raise ValueError('Collision metadata mass properties differ from original CAD.')
    hulls=[]
    for row in asset['hulls']:
        budget.check('collision_cache_convexity')
        hull=load_mesh(relative(folder,row['path']))
        if not np.isclose(hull.volume,ConvexHull(hull.vertices).volume,rtol=1e-6,atol=1e-15):
            raise ValueError('A non-convex piece would change shape inside MuJoCo.')
        hulls.append(hull)
    q=quality(original,hulls,settings);budget.check('collision_cache_quality')
    if q['status']!='passed':raise InputError('collision_geometry_invalid','Imported geometry failed independent quality check.')
    receipt={'schema':SCHEMA,'key':entry.name,'status':'passed','validator_sha256':validator_hash(),
        'source_mesh_sha256':sha(source),'asset_hash':asset['asset_hash'],'settings':settings,
        'independent_quality':q,'original_mass_properties':'passed','convex_pieces':'passed',
        'provenance':provenance or {},'created_unix_s':time.time(),
        'scope':'geometry only; reference, supply, grasp and assembly must be physically checked again'}
    receipt['receipt_hash']=digest(receipt)
    # Publish only a fully checked, self-contained directory. A failed import
    # leaves no valid entry. Unique staging avoids collisions between runs.
    staging=cache/('_pending_'+uuid.uuid4().hex);staging.mkdir()
    (staging/'collision').mkdir()
    shutil.copy2(source,staging/'source_metric.obj')
    shutil.copy2(Path(folder)/'asset.json',staging/'collision/asset.json')
    for row in [asset['visual'],*asset['hulls']]:
        shutil.copy2(relative(folder,row['path']),relative(staging/'collision',row['path']))
    write(staging/'verification.json',receipt)
    check_receipt(source,staging,settings)
    staging.rename(entry)
    return receipt


def register_prepared(path,output,budget):
    from .validation import check_prepared
    source=location(path);p=read(source);out=location(output)
    out.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    result={'schema':'collision_cache_import/v1','source_prepared':str(source),
        'source_prepared_sha256':sha(source),'status':'running','parts':{},'api_calls':0,
        'assembly_success':False}
    try:
        check_prepared(p,budget)
        print('Source preparation identity verified.',flush=True)
        for pid,part in p['parts'].items():
            print('Verifying collision asset',pid,flush=True)
            receipt=register_asset(part['mesh'],part['asset']['folder'],p['profile'],budget,
                provenance={'prepared_sha256':sha(source),'part_id':pid,'source':str(source)})
            result['parts'][pid]={'cache_key':receipt['key'],'receipt_hash':receipt['receipt_hash'],
                'status':'passed','existing_entry':receipt.get('existing_entry',False)}
            write(out/'result.json',result)
        result['status']='passed'
    except Exception as exc:
        result.update(status='failed',error=type(exc).__name__+': '+str(exc));raise
    finally:
        result['elapsed_s']=time.monotonic()-started;write(out/'result.json',result)
    return result
